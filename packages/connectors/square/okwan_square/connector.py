"""Square connector: a US point of sale and its payment rail, read only.

Square is the order ledger, the processor and the payout in one account,
so it has two reconciliations of its own (`square_payments_payouts`,
`square_payouts_deposits`): each payment against the payout line that
settled it, and each payout against the deposit the bank statement
shows. Every operation is a list; the token needs read scopes only
(PAYMENTS_READ, PAYOUTS_READ, ORDERS_READ, MERCHANT_PROFILE_READ).

Probed live against the sandbox on 2026-11-07 (§3.5): payments, refunds,
payouts and order search all answer as paged lists with that token; the
sandbox posted a processing fee on each payment a few seconds after it
completed. The sandbox makes no payouts, so payouts and payout entries
follow Square's documented shapes (the Payouts API guide's examples).

Paging is Square's own opaque cursor everywhere but payout entries,
which Square lists one payout at a time: without a `payout_id` the
operation walks the window's payouts in turn, and its cursor carries
where in that walk it stopped.
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any

from okwan_core import (
    Connector,
    ConnectorContext,
    OkwanClient,
    OpType,
    RateLimitProfile,
    register,
)

from .auth import SquareAuth, host_for
from .schemas import (
    ListLocationsIn,
    ListOrdersIn,
    ListPaymentsIn,
    ListPayoutEntriesIn,
    ListPayoutsIn,
    ListRefundsIn,
    Location,
    LocationPage,
    Order,
    OrderPage,
    Payment,
    PaymentPage,
    Payout,
    PayoutEntry,
    PayoutEntryPage,
    PayoutPage,
    Refund,
    RefundPage,
)


def _square_context(connector: Connector, credentials: dict[str, str]) -> ConnectorContext:
    connector.auth.validate(credentials)
    client = OkwanClient(
        base_url=host_for(credentials),
        auth=connector.auth.bind(credentials),
        rate_limit=connector.rate_limit,
    )
    return ConnectorContext(client=client, credentials=credentials)


square = register(
    Connector(
        name="square",
        version="0.1.0",
        description=(
            "Square: read locations, payments, refunds, payouts with each payout's lines, and orders. "
            "Amounts are minor units (cents) as Square sends them; a payment's `fee_minor` is "
            "Square's processing fee and `net_minor` what the seller keeps. Card data is the brand "
            "and last four digits only."
        ),
        base_url="",  # production or sandbox; built by the context factory
        auth=SquareAuth(),
        rate_limit=RateLimitProfile(requests_per_second=8, burst=8),
        docs_url="https://developer.squareup.com/reference/square",
        context_factory=_square_context,
    )
)

locations = square.resource("locations", Location, "The seller's business locations")
payments = square.resource("payments", Payment, "Card, cash and wallet payments")
refunds = square.resource("refunds", Refund, "Refunds of payments")
payouts = square.resource("payouts", Payout, "Transfers from Square to the seller's bank account")
payout_entries = square.resource("payout_entries", PayoutEntry, "The lines of each payout")
orders = square.resource("orders", Order, "Sales and returns")


def _rfc3339(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _query(**params: Any) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None}


def _windowed(params: Any) -> dict[str, Any]:
    return _query(limit=params.limit, cursor=params.cursor, location_id=params.location_id,
                  begin_time=_rfc3339(params.start_date), end_time=_rfc3339(params.end_date))


@locations.operation(OpType.LIST, ListLocationsIn, LocationPage, description="List the seller's locations.")
async def list_locations(ctx: ConnectorContext, params: ListLocationsIn) -> LocationPage:
    data = await ctx.client.get("/v2/locations")
    return LocationPage(items=[Location.model_validate(x) for x in data.get("locations", [])])


@payments.operation(
    OpType.LIST, ListPaymentsIn, PaymentPage,
    description=(
        "List payments, newest first, over a window (Square's default is the last year). "
        "`reference_id` and `order_id` are the join keys; filter status=COMPLETED for settled sales."
    ),
)
async def list_payments(ctx: ConnectorContext, params: ListPaymentsIn) -> PaymentPage:
    data = await ctx.client.get("/v2/payments", params=_windowed(params))
    items = [Payment.model_validate(x) for x in data.get("payments", [])]
    # The cursor is Square's, for the page as Square returned it; the
    # filter runs after, so the next page never replays a filtered tail.
    if params.status:
        items = [p for p in items if p.status == params.status.upper()]
    cursor = data.get("cursor") or None
    return PaymentPage(items=items, next_cursor=cursor, has_more=bool(cursor))


@refunds.operation(OpType.LIST, ListRefundsIn, RefundPage, description="List refunds, newest first, over a window.")
async def list_refunds(ctx: ConnectorContext, params: ListRefundsIn) -> RefundPage:
    data = await ctx.client.get("/v2/refunds", params=_query(**_windowed(params), status=params.status))
    cursor = data.get("cursor") or None
    return RefundPage(items=[Refund.model_validate(x) for x in data.get("refunds", [])],
                      next_cursor=cursor, has_more=bool(cursor))


@payouts.operation(
    OpType.LIST, ListPayoutsIn, PayoutPage,
    description="List payouts to the seller's bank account over a window; status=PAID for money that arrived.",
)
async def list_payouts(ctx: ConnectorContext, params: ListPayoutsIn) -> PayoutPage:
    data = await ctx.client.get("/v2/payouts", params=_query(**_windowed(params), status=params.status))
    cursor = data.get("cursor") or None
    return PayoutPage(items=[Payout.model_validate(x) for x in data.get("payouts", [])],
                      next_cursor=cursor, has_more=bool(cursor))


# ── payout entries: one payout's, or every payout's in a window ─────

def _encode(state: dict[str, Any]) -> str:
    raw = json.dumps(state, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> dict[str, Any] | None:
    """A cursor we cannot read restarts the walk: it is the caller's
    handle, never a reason to fail the request."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        state = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, TypeError, binascii.Error):
        return None
    if not isinstance(state, dict) or not isinstance(state.get("q", []), list):
        return None
    return state


async def _entries(ctx: ConnectorContext, payout_id: str, cursor: str | None, limit: int) -> dict[str, Any]:
    return await ctx.client.get(f"/v2/payouts/{payout_id}/payout-entries",
                                params=_query(limit=limit, cursor=cursor))


@payout_entries.operation(
    OpType.LIST, ListPayoutEntriesIn, PayoutEntryPage,
    description=(
        "List payout lines. With `payout_id`, that payout's; without it, every payout's in the "
        "window, payout by payout. A CHARGE line names the payment it settles (`payment_id`) "
        "and Square's fee on it."
    ),
)
async def list_payout_entries(ctx: ConnectorContext, params: ListPayoutEntriesIn) -> PayoutEntryPage:
    want = params.type.upper() if params.type else None

    def keep(rows: list[dict[str, Any]]) -> list[PayoutEntry]:
        items = [PayoutEntry.model_validate(x) for x in rows]
        return [e for e in items if want is None or e.type == want]

    if params.payout_id:
        data = await _entries(ctx, params.payout_id, params.cursor, params.limit)
        cursor = data.get("cursor") or None
        return PayoutEntryPage(items=keep(data.get("payout_entries", [])), next_cursor=cursor, has_more=bool(cursor))

    # The walk: `p` the payouts cursor, `q` payouts not yet read, `c` the
    # one being read and `e` its entries cursor, `d` the payouts listed.
    state = (_decode(params.cursor) if params.cursor else None) or {"p": None, "q": [], "c": None, "e": None, "d": False}
    out: list[PayoutEntry] = []
    while len(out) < params.limit:
        if state["c"] is None:
            if state["q"]:
                state["c"], state["e"] = state["q"].pop(0), None
                continue
            if state["d"]:
                break
            page = await ctx.client.get("/v2/payouts", params=_query(
                limit=100, cursor=state["p"], location_id=params.location_id,
                begin_time=_rfc3339(params.start_date), end_time=_rfc3339(params.end_date)))
            state["q"] = [p["id"] for p in page.get("payouts", []) if isinstance(p, dict) and p.get("id")]
            state["p"] = page.get("cursor") or None
            state["d"] = state["p"] is None
            continue
        data = await _entries(ctx, state["c"], state["e"], min(100, params.limit - len(out)))
        out += keep(data.get("payout_entries", []))
        state["e"] = data.get("cursor") or None
        if state["e"] is None:
            state["c"] = None
    more = not (state["d"] and not state["q"] and state["c"] is None)
    return PayoutEntryPage(items=out, next_cursor=_encode(state) if more else None, has_more=more)


@orders.operation(
    OpType.LIST, ListOrdersIn, OrderPage,
    description=(
        "List orders, oldest first, over a window, across the seller's locations (at most 10). "
        "A return is its own order, naming the sale it reverses in `source_order_id`."
    ),
)
async def list_orders(ctx: ConnectorContext, params: ListOrdersIn) -> OrderPage:
    if params.location_id:
        ids = [params.location_id]
    else:
        found = await ctx.client.get("/v2/locations")
        ids = [x["id"] for x in found.get("locations", []) if isinstance(x, dict) and x.get("id")][:10]
    if not ids:
        return OrderPage(items=[])
    query: dict[str, Any] = {"sort": {"sort_field": "CREATED_AT", "sort_order": "ASC"}}
    created = _query(start_at=_rfc3339(params.start_date), end_at=_rfc3339(params.end_date))
    filters: dict[str, Any] = {}
    if created:
        filters["date_time_filter"] = {"created_at": created}
    if params.state:
        filters["state_filter"] = {"states": [params.state.upper()]}
    if filters:
        query["filter"] = filters
    body = _query(location_ids=ids, cursor=params.cursor, limit=params.limit, query=query)
    data = await ctx.client.post("/v2/orders/search", json=body)
    cursor = data.get("cursor") or None
    return OrderPage(items=[Order.model_validate(x) for x in data.get("orders", [])],
                     next_cursor=cursor, has_more=bool(cursor))
