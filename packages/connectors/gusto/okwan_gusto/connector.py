"""Gusto connector: what a business's payroll paid, read only.

The point of sale says what tips staff earned at the till; payroll says
what tips were declared and paid. Gusto is the payroll side of that check.
A business's Gusto admin connects it with Connect Gusto (auth.py); nobody
types a Gusto credential into Okwan.

**One resource so far: payrolls.** The list is also the live §3.5 check:
Okwan's connection test reads one payroll the moment the admin connects.
Each payroll carries its check date, pay period and totals, cash and
paycheck tips among them. Employees, and each employee's tips, come once
that check has passed against Gusto's sandbox.

**Stable pages.** Gusto pages by number (`page`, `per`, at most 100) inside
a window of at most a year. The walk pins its window on the first page, so
a payroll processed while it is paging cannot shift a page already read.
A short page is the last one.
"""
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from okwan_core import (
    Connector,
    ConnectorContext,
    OkwanClient,
    OpType,
    RateLimitProfile,
    register,
)

from . import auth as gusto_auth
from .auth import GustoOAuth, Tokens
from .schemas import ListPayrollsIn, Payroll, PayrollPage

DEFAULT_LOOKBACK_DAYS = 90
MAX_WINDOW_DAYS = 365

_store: Callable[[], Any] | None = None


def use_store(getter: Callable[[], Any]) -> None:
    """Where a renewed token pair is written back: the API's store, at startup."""
    global _store
    _store = getter


async def save_tokens(tenant_id: str, tokens: Tokens, company_uuid: str | None = None) -> None:
    """Write a pair (and on first connect, the company) to the tenant's vault."""
    if _store is None:
        return
    store = _store()
    fields = tokens.fields()
    if company_uuid is not None:
        fields["company_uuid"] = company_uuid
    for name, value in fields.items():
        await store.put_credential(tenant_id, "gusto", name, value)


def _gusto_context(connector: Connector, credentials: dict[str, str]) -> ConnectorContext:
    connector.auth.validate(credentials)
    auth = connector.auth.bind(credentials)
    client = OkwanClient(base_url=gusto_auth.host(), auth=auth, rate_limit=connector.rate_limit)
    ctx = ConnectorContext(client=client, credentials=credentials)

    async def persist(tokens: Tokens) -> None:
        # The platform sets ctx.tenant_id after this factory returns, and a
        # renewal happens during a request, so it is read then, not now.
        if ctx.tenant_id:
            await save_tokens(ctx.tenant_id, tokens)

    auth.on_rotate = persist  # type: ignore[attr-defined]
    return ctx


gusto = register(
    Connector(
        name="gusto",
        version="0.1.0",
        description=(
            "Gusto: read a business's processed payrolls, connected by its own Gusto admin with "
            "Connect Gusto. Amounts are minor units (cents) from Gusto's decimals; `tips_minor` is "
            "every tip a payroll accounts for, cash and paycheck. No employee name is read."
        ),
        base_url="",  # demo or production; built by the context factory
        auth=GustoOAuth(),
        # Gusto allows 200 requests a minute per company token.
        rate_limit=RateLimitProfile(requests_per_second=3, burst=5, max_attempts=4),
        docs_url="https://docs.gusto.com/app-integrations/reference/get-v1-companies-company_id-payrolls",
        context_factory=_gusto_context,
    )
)

payrolls = gusto.resource("payrolls", Payroll, "Processed payrolls: when they paid, the period, gross, net and tips")


def _encode(page: int, start: date, end: date) -> str:
    raw = json.dumps({"p": page, "s": start.isoformat(), "e": end.isoformat()}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode(cursor: str) -> tuple[int, date, date] | None:
    """A cursor we cannot read restarts the walk, as Avalara's does: it is
    the caller's handle, never a reason to fail the request."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        page, start, end = int(data["p"]), date.fromisoformat(data["s"]), date.fromisoformat(data["e"])
    except (binascii.Error, ValueError, KeyError, TypeError):
        return None
    if page < 1 or start > end or (end - start).days > MAX_WINDOW_DAYS:
        return None
    return page, start, end


def window(params: ListPayrollsIn, today: date | None = None) -> tuple[date, date]:
    """The days the walk reads: the caller's, or the last 90 to today, at
    most a year, as Gusto allows."""
    today = today or datetime.now(UTC).date()
    end = params.end_date or today
    start = params.start_date or end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    start = min(start, end)
    if (end - start).days > MAX_WINDOW_DAYS:
        start = end - timedelta(days=MAX_WINDOW_DAYS)
    return start, end


@payrolls.operation(OpType.LIST, ListPayrollsIn, PayrollPage,
                    description="List processed payrolls over a window, oldest first, with gross, net and tips.")
async def list_payrolls(ctx: ConnectorContext, params: ListPayrollsIn) -> PayrollPage:
    state = _decode(params.cursor) if params.cursor else None
    page, (start, end) = (state[0], state[1:]) if state else (1, window(params))
    company = ctx.credentials["company_uuid"]
    query: dict[str, Any] = {
        "processing_statuses": "processed",
        "payroll_types": "regular,off_cycle" if params.off_cycle else "regular",
        "include": "totals",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "sort_order": "asc",
        "page": page,
        "per": params.limit,
    }
    rows = await ctx.client.get(f"/v1/companies/{company}/payrolls", params=query)
    items = [Payroll.from_gusto(r) for r in (rows if isinstance(rows, list) else [])]
    more = len(items) >= params.limit
    return PayrollPage(
        items=items,
        next_cursor=_encode(page + 1, start, end) if more else None,
        has_more=more,
        span_start=datetime.combine(start, time.min, tzinfo=UTC),
        span_end=datetime.combine(end, time.max, tzinfo=UTC),
    )
