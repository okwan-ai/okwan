"""Gusto connector: what a business's payroll paid, read only.

The point of sale says what tips staff earned at the till; payroll says
what tips were declared and paid. Gusto is the payroll side of that check.
A business's Gusto admin connects it with Connect Gusto (auth.py); nobody
types a Gusto credential into Okwan.

**Two resources.** `payrolls`: each processed payroll's check date, pay
period and totals, cash and paycheck tips among them. Its list is the
connection test, and so the live §3.5 check (passed in production
2026-11-09). `payroll_tips`: each employee's cash and paycheck tips on
each processed payroll, by Gusto's employee uuid, the side the point of
sale's tips per employee per pay period are checked against. Gusto's
payroll list carries no employee, so each payroll's detail is read; of
it, Okwan keeps the employee's uuid and the two tips, never a name, wage,
rate, gross or net pay, deduction or tax.

**Stable pages.** Gusto pages payrolls by number (`page`, `per`, at most
100) inside a window of at most a year. A walk pins its window on the
first page, so a payroll processed while it is paging cannot shift a page
already read. A short page is the last one. The tips walk pages by
employee rows, so its cursor also says which payroll it stopped in and
how many of that payroll's rows it has given.
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
from .schemas import (
    ListPayrollsIn,
    ListPayrollTipsIn,
    Payroll,
    PayrollPage,
    PayrollTip,
    PayrollTipPage,
)

DEFAULT_LOOKBACK_DAYS = 90
MAX_WINDOW_DAYS = 365
#: Payrolls read per list call by the tips walk, and employees per detail
#: call: Gusto's own maximum for a payroll's employee_compensations.
PAYROLLS_PER_READ = 25
EMPLOYEES_PER_READ = 100
#: A payroll of more employees than this many detail pages is cut there,
#: rather than read forever from an API that ignored `page`.
MAX_EMPLOYEE_PAGES = 50

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
            "Gusto: read a business's processed payrolls and each employee's tips on them, connected "
            "by its own Gusto admin with Connect Gusto. Amounts are minor units (cents) from Gusto's "
            "decimals; `tips_minor` is every tip, cash and paycheck. An employee is Gusto's uuid only: "
            "no name, wage, gross or net pay, deduction or tax is read per employee."
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
payroll_tips = gusto.resource(
    "payroll_tips", PayrollTip,
    "Each employee's cash and paycheck tips on each processed payroll, by Gusto's employee uuid")


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


def window(params: ListPayrollsIn | ListPayrollTipsIn, today: date | None = None) -> tuple[date, date]:
    """The days the walk reads: the caller's, or the last 90 to today, at
    most a year, as Gusto allows."""
    today = today or datetime.now(UTC).date()
    end = params.end_date or today
    start = params.start_date or end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    start = min(start, end)
    if (end - start).days > MAX_WINDOW_DAYS:
        start = end - timedelta(days=MAX_WINDOW_DAYS)
    return start, end


async def _payroll_list(ctx: ConnectorContext, company: str, start: date, end: date, off_cycle: bool,
                        page: int, per: int, totals: bool = False) -> list[dict[str, Any]]:
    """One page of processed payrolls in the window, oldest first."""
    query: dict[str, Any] = {
        "processing_statuses": "processed",
        "payroll_types": "regular,off_cycle" if off_cycle else "regular",
        **({"include": "totals"} if totals else {}),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "sort_order": "asc",
        "page": page,
        "per": per,
    }
    rows = await ctx.client.get(f"/v1/companies/{company}/payrolls", params=query)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


async def _tip_rows(ctx: ConnectorContext, company: str, payroll_uuid: str) -> list[PayrollTip]:
    """Every tip row of one payroll, from its detail, read a hundred
    employees at a time until a short page."""
    detail: dict[str, Any] = {}
    compensations: list[dict[str, Any]] = []
    for page in range(1, MAX_EMPLOYEE_PAGES + 1):
        body = await ctx.client.get(f"/v1/companies/{company}/payrolls/{payroll_uuid}",
                                    params={"page": page, "per": EMPLOYEES_PER_READ})
        body = body if isinstance(body, dict) else {}
        detail = detail or body
        batch = [c for c in body.get("employee_compensations") or [] if isinstance(c, dict)]
        compensations += batch
        if len(batch) < EMPLOYEES_PER_READ:
            break
    return PayrollTip.rows_from_gusto({**detail, "payroll_uuid": payroll_uuid,
                                       "employee_compensations": compensations})


def _encode_tips(page: int, index: int, given: int, start: date, end: date) -> str:
    raw = json.dumps({"p": page, "i": index, "g": given, "s": start.isoformat(), "e": end.isoformat()},
                     separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_tips(cursor: str) -> tuple[int, int, int, date, date] | None:
    """Where a tips walk stopped: the list page, the payroll on it, and how
    many of that payroll's rows were given. Unreadable restarts the walk."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        page, index, given = int(data["p"]), int(data["i"]), int(data["g"])
        start, end = date.fromisoformat(data["s"]), date.fromisoformat(data["e"])
    except (binascii.Error, ValueError, KeyError, TypeError):
        return None
    if page < 1 or not 0 <= index < PAYROLLS_PER_READ or given < 0 or start > end \
            or (end - start).days > MAX_WINDOW_DAYS:
        return None
    return page, index, given, start, end


@payrolls.operation(OpType.LIST, ListPayrollsIn, PayrollPage,
                    description="List processed payrolls over a window, oldest first, with gross, net and tips.")
async def list_payrolls(ctx: ConnectorContext, params: ListPayrollsIn) -> PayrollPage:
    state = _decode(params.cursor) if params.cursor else None
    page, (start, end) = (state[0], state[1:]) if state else (1, window(params))
    company = ctx.credentials["company_uuid"]
    rows = await _payroll_list(ctx, company, start, end, params.off_cycle, page, params.limit, totals=True)
    items = [Payroll.from_gusto(r) for r in rows]
    more = len(items) >= params.limit
    return PayrollPage(
        items=items,
        next_cursor=_encode(page + 1, start, end) if more else None,
        has_more=more,
        span_start=datetime.combine(start, time.min, tzinfo=UTC),
        span_end=datetime.combine(end, time.max, tzinfo=UTC),
    )


@payroll_tips.operation(OpType.LIST, ListPayrollTipsIn, PayrollTipPage,
                        description="List each employee's cash and paycheck tips on processed payrolls over a "
                                    "window, oldest payroll first. An employee is Gusto's uuid only.")
async def list_payroll_tips(ctx: ConnectorContext, params: ListPayrollTipsIn) -> PayrollTipPage:
    state = _decode_tips(params.cursor) if params.cursor else None
    page, index, given, start, end = state if state else (1, 0, 0, *window(params))
    company = ctx.credentials["company_uuid"]
    items: list[PayrollTip] = []
    listed: list[dict[str, Any]] | None = None
    finished = False
    while len(items) < params.limit:
        if listed is None:
            listed = await _payroll_list(ctx, company, start, end, params.off_cycle, page, PAYROLLS_PER_READ)
        if index >= len(listed):
            if len(listed) < PAYROLLS_PER_READ:
                finished = True
                break
            page, index, listed = page + 1, 0, None
            continue
        uuid = str(listed[index].get("payroll_uuid") or listed[index].get("uuid") or "")
        rows = await _tip_rows(ctx, company, uuid) if uuid else []
        taken = rows[given:given + params.limit - len(items)]
        items += taken
        given += len(taken)
        if given >= len(rows):
            index, given = index + 1, 0
    return PayrollTipPage(
        items=items,
        next_cursor=None if finished else _encode_tips(page, index, given, start, end),
        has_more=not finished,
        span_start=datetime.combine(start, time.min, tzinfo=UTC),
        span_end=datetime.combine(end, time.max, tzinfo=UTC),
    )
