"""Clover connector: a US point of sale, read only.

Clover is the order ledger and the card terminal; the money settles
through the merchant's processor and lands at their bank, neither of
which Clover's API reports. So Clover has two reconciliations
(`clover_orders_payments`, `clover_payments_settlements`): each ticket
against the payments recorded on it, and each card payment against the
processor's own export of what it settled. Every operation is a list;
the token needs read permissions only (Merchant, Orders, Payments).

Probed live against the sandbox on 2026-11-07 (§3.5): payments, orders
and refunds answer as offset-paged lists (`elements`, `limit` up to 1000,
`offset`), honour `filter=createdTime>=…`, `filter=result=SUCCESS` and
`orderBy=createdTime ASC`, and state amounts in cents. The sandbox makes
card sales only through Clover's separate Ecommerce API and refunds only
there, so card and refund rows follow Clover's documented shapes.

**Clover's 90-day limit.** A time-filtered query answers at most 90 days
and, asked for more, silently keeps only the most recent 90: a 120-day
lookback would read as complete while missing its first month. The walk
here never asks for more than one slice of `SLICE_DAYS`, oldest first,
and its cursor carries the slice and the offset inside it, so any window
is read whole and every page states the span the walk covers.

**Stable pages.** Offsets are only safe over a list that does not move.
The walk pins its end on the first page and carries it in the cursor, and
orders each slice by creation time, so a sale rung up while a
reconciliation is paging lands after the window instead of shifting
every later offset by one (the row skipped or read twice).
"""
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from okwan_core import (
    Connector,
    ConnectorContext,
    OkwanClient,
    OpType,
    RateLimitProfile,
    register,
)
from pydantic import BaseModel

from .auth import CloverAuth, host_for, merchant_id
from .schemas import (
    CARD_TENDERS,
    ListOrdersIn,
    ListPaymentsIn,
    ListRefundsIn,
    Order,
    OrderPage,
    Payment,
    PaymentPage,
    Refund,
    RefundPage,
)

#: Clover answers at most 90 days per time-filtered query; a day short of
#: it leaves room for clock skew at either end.
SLICE_DAYS = 89
DEFAULT_LOOKBACK_DAYS = 90
#: Empty slices walked inside one call before handing back a cursor, so a
#: caller asking for years of a quiet merchant gets bounded calls.
MAX_SLICES_PER_CALL = 12
_SLICE_MS = SLICE_DAYS * 86_400_000


def _clover_context(connector: Connector, credentials: dict[str, str]) -> ConnectorContext:
    connector.auth.validate(credentials)
    client = OkwanClient(
        base_url=host_for(credentials),
        auth=connector.auth.bind(credentials),
        rate_limit=connector.rate_limit,
    )
    return ConnectorContext(client=client, credentials=credentials)


clover = register(
    Connector(
        name="clover",
        version="0.1.0",
        description=(
            "Clover: read a merchant's payments, refunds and orders. Amounts are minor units (cents) "
            "as Clover sends them; a payment's `total_minor` is what the card was charged (amount plus "
            "tip) and `created_on` the day in the merchant's own timezone. Card data is the brand, the "
            "last four digits and the authorization code only. Windows longer than Clover's 90-day "
            "limit are read whole."
        ),
        base_url="",  # production or sandbox; built by the context factory
        auth=CloverAuth(),
        # Clover allows 16 requests a second and 5 in flight per token.
        rate_limit=RateLimitProfile(requests_per_second=8, burst=5),
        docs_url="https://docs.clover.com/dev/reference/api-reference-overview",
        context_factory=_clover_context,
    )
)

payments = clover.resource("payments", Payment, "Payments recorded against orders: card, cash and other tenders")
refunds = clover.resource("refunds", Refund, "Refunds of payments")
orders = clover.resource("orders", Order, "Tickets: what each sale came to")


# ── the merchant: its timezone and currency, once per connection ────

#: Per transport, so it lives exactly as long as the one connection that
#: read it and is never shared between two accounts' credentials.
_merchants: WeakKeyDictionary[Any, dict[str, str | None]] = WeakKeyDictionary()


async def _merchant(ctx: ConnectorContext) -> dict[str, str | None]:
    known = _merchants.get(ctx.client)
    if known is not None:
        return known
    data = await ctx.client.get(f"/v3/merchants/{merchant_id(ctx.credentials)}",
                                params={"expand": "properties"})
    props = data.get("properties") if isinstance(data.get("properties"), dict) else {}
    zone = props.get("timezone") if isinstance(props.get("timezone"), str) else None
    try:
        ZoneInfo(zone or "")
    except (ZoneInfoNotFoundError, ValueError):
        zone = None
    currency = props.get("defaultCurrency")
    known = {"timezone": zone or "UTC",
             "currency": currency.upper() if isinstance(currency, str) and len(currency) == 3 else None}
    _merchants[ctx.client] = known
    return known


# ── the walk: oldest slice first, offsets inside a pinned window ────

def _ms(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _encode(state: dict[str, int]) -> str:
    raw = json.dumps(state, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> dict[str, int] | None:
    """A cursor we cannot read restarts the walk: it is the caller's
    handle, never a reason to fail the request."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        state = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, TypeError, binascii.Error):
        return None
    if not isinstance(state, dict) or set(state) != {"a", "s", "e", "o"}:
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in state.values()):
        return None
    if not state["a"] <= state["s"] <= state["e"]:
        return None
    return state


def _start(params: Any) -> dict[str, int]:
    now = datetime.now(UTC)
    end = _ms(params.end_date) if params.end_date else _ms(now)
    begin = _ms(params.start_date) if params.start_date else _ms(now - timedelta(days=DEFAULT_LOOKBACK_DAYS))
    begin = min(begin, end)
    # a: where the walk began, s: the slice being read, e: the pinned end, o: offset in the slice.
    return {"a": begin, "s": begin, "e": end, "o": 0}


async def _walk(
    ctx: ConnectorContext,
    params: Any,
    resource: str,
    model: type[BaseModel],
    filters: list[str],
    expand: str | None = None,
    keep: Callable[[Any], bool] = lambda _: True,
) -> dict[str, Any]:
    state = (_decode(params.cursor) if params.cursor else None) or _start(params)
    span = {"span_start": _at(state["a"]), "span_end": _at(state["e"])}
    context = await _merchant(ctx)
    path = f"/v3/merchants/{merchant_id(ctx.credentials)}/{resource}"
    items: list[Any] = []
    for _ in range(MAX_SLICES_PER_CALL):
        if state["s"] >= state["e"]:
            return {"items": items, "next_cursor": None, "has_more": False, **span}
        until = min(state["s"] + _SLICE_MS, state["e"])
        query: dict[str, Any] = {
            "filter": [f"createdTime>={state['s']}", f"createdTime<{until}", *filters],
            "orderBy": "createdTime ASC", "limit": params.limit, "offset": state["o"],
        }
        if expand:
            query["expand"] = expand
        data = await ctx.client.get(path, params=query)
        rows = [r for r in data.get("elements") or [] if isinstance(r, dict)]
        items += [x for x in (model.model_validate(r, context=context) for r in rows) if keep(x)]
        if len(rows) >= params.limit:
            state["o"] += len(rows)  # this slice may hold more
            return {"items": items, "next_cursor": _encode(state), "has_more": True, **span}
        state["s"], state["o"] = until, 0  # slice read to its end
        if state["s"] >= state["e"]:
            return {"items": items, "next_cursor": None, "has_more": False, **span}
        if items:
            return {"items": items, "next_cursor": _encode(state), "has_more": True, **span}
        # an empty slice: walk on rather than hand back an empty page
    return {"items": items, "next_cursor": _encode(state), "has_more": True, **span}


# ── operations ──────────────────────────────────────────────────────

def _is_card(payment: Payment) -> bool:
    """Settled through the card processor: a card tender, or a card on it."""
    return payment.tender in CARD_TENDERS or payment.card_last4 is not None


@payments.operation(
    OpType.LIST, ListPaymentsIn, PaymentPage,
    description=(
        "List payments, oldest first, over a window (the last 90 days by default; longer windows are "
        "read whole). `order_id` joins to orders; filter result=SUCCESS for money taken, and "
        "card_only for what a card processor settles."
    ),
)
async def list_payments(ctx: ConnectorContext, params: ListPaymentsIn) -> PaymentPage:
    filters = [f"result={params.result.upper()}"] if params.result else []
    page = await _walk(ctx, params, "payments", Payment, filters, "tender,cardTransaction,refunds",
                       _is_card if params.card_only else lambda _: True)
    return PaymentPage.model_validate(page)


@refunds.operation(OpType.LIST, ListRefundsIn, RefundPage,
                   description="List refunds, oldest first, over a window; voided=false leaves out voided ones.")
async def list_refunds(ctx: ConnectorContext, params: ListRefundsIn) -> RefundPage:
    filters = [f"voided={str(params.voided).lower()}"] if params.voided is not None else []
    return RefundPage.model_validate(await _walk(ctx, params, "refunds", Refund, filters))


@orders.operation(
    OpType.LIST, ListOrdersIn, OrderPage,
    description=(
        "List orders, oldest first, over a window, each with the ids of the payments recorded on it. "
        "skip_empty leaves out tickets with no total."
    ),
)
async def list_orders(ctx: ConnectorContext, params: ListOrdersIn) -> OrderPage:
    filters = [f"state={params.state.lower()}"] if params.state else []
    keep = (lambda o: bool(o.total_minor)) if params.skip_empty else (lambda _: True)
    return OrderPage.model_validate(await _walk(ctx, params, "orders", Order, filters, "payments", keep))
