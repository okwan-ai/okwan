"""Avalara AvaTax connector: what the sales tax engine recorded, read only.

A merchant's point of sale or store sends each sale to AvaTax to price its
tax, and commits it once the sale is final; a committed document is what
Avalara's returns (or the merchant's accountant) report to each state. So
AvaTax is the middle of the sales tax chain: the till charged it, AvaTax
recorded it, a return filed it. Its reconciliation
(`avatax_collected_filed`) checks the last step: for each return imported
(`files.tax_filings`), the tax AvaTax committed for that state over the
return's period against the tax the return says was filed.

Probed live on 2026-11-08 (§3.5) against a trial account: companies and
transactions answer as lists (`value`, `@recordsetCount`, `@nextLink`),
filter by date, status and type (`$filter`), sort on two keys
(`$orderBy=date ASC, id ASC`) and page by offset (`$top` to 1000, `$skip`).
The trial allowed about six requests a minute; the client waits out a 429
by its `Retry-After`. Every operation is a list.

**Stable pages.** The walk pins its window on the first page and orders
by date then id, so a sale committed while a reconciliation is paging
cannot shift a document into a page already read.
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from okwan_core import (
    Connector,
    ConnectorContext,
    OkwanClient,
    OpType,
    RateLimitProfile,
    register,
)

from .auth import AvalaraAuth, company_code, host_for
from .schemas import (
    Company,
    CompanyPage,
    ListCompaniesIn,
    ListTransactionsIn,
    Transaction,
    TransactionPage,
)

DEFAULT_LOOKBACK_DAYS = 90
_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def _avalara_context(connector: Connector, credentials: dict[str, str]) -> ConnectorContext:
    connector.auth.validate(credentials)
    client = OkwanClient(
        base_url=host_for(credentials),
        auth=connector.auth.bind(credentials),
        rate_limit=connector.rate_limit,
    )
    return ConnectorContext(client=client, credentials=credentials)


avalara = register(
    Connector(
        name="avalara",
        version="0.1.0",
        description=(
            "Avalara AvaTax: read the sales tax engine's companies and transactions. Amounts are "
            "minor units (cents) converted from AvaTax's decimals; a transaction's `total_tax_minor` "
            "is the tax on it and `committed` whether it counts on a tax return. No customer email, "
            "business id or exemption number is kept."
        ),
        base_url="",  # production or sandbox; built by the context factory
        auth=AvalaraAuth(),
        # Gentle by default: a trial account allows a few requests a minute,
        # and the client waits out a 429 by Retry-After.
        rate_limit=RateLimitProfile(requests_per_second=2, burst=2, max_attempts=6),
        docs_url="https://developer.avalara.com/api-reference/avatax/rest/v2/",
        context_factory=_avalara_context,
    )
)

companies = avalara.resource("companies", Company, "The companies in the AvaTax account")
transactions = avalara.resource(
    "transactions", Transaction, "Documents AvaTax priced: sales, returns, their tax and whether committed")


@companies.operation(OpType.LIST, ListCompaniesIn, CompanyPage,
                     description="List the account's companies; the company code names which one to read.")
async def list_companies(ctx: ConnectorContext, params: ListCompaniesIn) -> CompanyPage:
    data = await ctx.client.get("/api/v2/companies", params={"$top": params.limit})
    return CompanyPage(items=[Company.model_validate(x) for x in data.get("value") or []])


# ── the walk: a pinned window, offset pages inside it ───────────────

def _day(moment: datetime | None, fallback: date) -> date:
    if moment is None:
        return fallback
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.date()


def _encode(state: dict[str, Any]) -> str:
    raw = json.dumps(state, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> dict[str, Any] | None:
    """A cursor we cannot read restarts the walk: it is the caller's
    handle, never a reason to fail the request."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        state = json.loads(base64.urlsafe_b64decode(padded.encode()))
        first, last = date.fromisoformat(state["s"]), date.fromisoformat(state["e"])
    except (ValueError, TypeError, KeyError, binascii.Error):
        return None
    skip = state.get("k")
    if not isinstance(skip, int) or isinstance(skip, bool) or skip < 0 or first > last:
        return None
    return {"s": first.isoformat(), "e": last.isoformat(), "k": skip}


def _quoted(word: str) -> str:
    """A filter value: a status or type word, never anything that could close the quote."""
    if not word or not set(word) <= _SAFE:
        raise ValueError(f"not a status or type: {word!r}")
    return f"'{word}'"


@transactions.operation(
    OpType.LIST, ListTransactionsIn, TransactionPage,
    description=(
        "List AvaTax documents, oldest first, over a window (the last 90 days by default), sales "
        "invoices unless `type` says otherwise. status=Committed for what counts on a tax return; "
        "`code` is the document code the selling system gave the sale."
    ),
)
async def list_transactions(ctx: ConnectorContext, params: ListTransactionsIn) -> TransactionPage:
    today = datetime.now(UTC).date()
    state = _decode(params.cursor) if params.cursor else None
    if state is None:
        last = _day(params.end_date, today)
        first = min(_day(params.start_date, last - timedelta(days=DEFAULT_LOOKBACK_DAYS)), last)
        state = {"s": first.isoformat(), "e": last.isoformat(), "k": 0}
    terms = [f"date between '{state['s']}' and '{state['e']}'"]
    if params.status:
        terms.append(f"status eq {_quoted(params.status)}")
    kinds = [w.strip() for w in (params.type or "").split(",") if w.strip()]
    if kinds:
        terms.append("(" + " or ".join(f"type eq {_quoted(k)}" for k in kinds) + ")")
    data = await ctx.client.get(
        f"/api/v2/companies/{company_code(ctx.credentials)}/transactions",
        params={"$filter": " and ".join(terms), "$orderBy": "date ASC, id ASC",
                "$top": params.limit, "$skip": state["k"]},
    )
    rows = [r for r in data.get("value") or [] if isinstance(r, dict)]
    total = data.get("@recordsetCount")
    read = state["k"] + len(rows)
    # AvaTax states the window's count; trust it over @nextLink, which a
    # last page still carries.
    more = (read < total) if isinstance(total, int) and not isinstance(total, bool) \
        else (bool(data.get("@nextLink")) and len(rows) > 0)
    span = {"span_start": datetime.combine(date.fromisoformat(state["s"]), datetime.min.time(), UTC),
            "span_end": datetime.combine(date.fromisoformat(state["e"]), datetime.max.time(), UTC)}
    cursor = _encode({**state, "k": read}) if more else None
    return TransactionPage(items=[Transaction.model_validate(r) for r in rows],
                           next_cursor=cursor, has_more=more, **span)
