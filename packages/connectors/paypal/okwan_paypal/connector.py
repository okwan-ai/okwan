"""PayPal connector — Okwan connector #6, second US payment rail.

Transaction Search requires an explicit date window capped at 31 days,
and pages by number inside it. So the SDK cursor encodes both: a window
start and a page. When a window's pages are exhausted the cursor rolls
forward to the next window, and the walk ends at the requested end date.
Every other rail pages a single sequence; callers, MCP tools and REST
routes never learn this one is different.

The end of the walk is clamped to `last_refreshed_datetime` from the
response — PayPal's own statement of how far its ledger is current,
which lags real time by up to three hours. Reconciling against rows
that have not landed yet manufactures breaks that resolve themselves.
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from okwan_core import (
    Connector,
    ConnectorContext,
    OkwanClient,
    OpType,
    RateLimitProfile,
    register,
)

from .auth import PayPalOAuth2Auth, host_for
from .schemas import ListTransactionsIn, Transaction, TransactionPage

#: Upstream caps a single query at 31 days; 30 leaves room for skew.
WINDOW_DAYS = 30
#: Upstream refuses more than 10,000 records for one window.
MAX_RECORDS_PER_WINDOW = 10_000
DEFAULT_LOOKBACK_DAYS = 30


def _paypal_context(
    connector: Connector, credentials: dict[str, str]
) -> ConnectorContext:
    connector.auth.validate(credentials)
    client = OkwanClient(
        base_url=host_for(credentials),
        auth=connector.auth.bind(credentials),
        rate_limit=connector.rate_limit,
    )
    return ConnectorContext(client=client, credentials=credentials)


paypal = register(
    Connector(
        name="paypal",
        version="0.1.0",
        description=(
            "PayPal: read the transaction ledger via Transaction Search. "
            "Amounts are integer minor units converted from PayPal's decimal "
            "strings; `net_minor` is gross less PayPal's fee, which is the "
            "figure a payout settles to. `is_payment` separates customer "
            "payments from account adjustments and transfers."
        ),
        base_url="",  # sandbox or live; built by the context factory
        auth=PayPalOAuth2Auth(),
        rate_limit=RateLimitProfile(requests_per_second=5, burst=10),
        docs_url="https://developer.paypal.com/docs/api/transaction-search/v1/",
        context_factory=_paypal_context,
    )
)

transactions = paypal.resource(
    "transactions",
    schema=Transaction,
    description="Ledger of payments, refunds, fees and adjustments",
)


def _fmt(moment: datetime) -> str:
    """PayPal wants RFC 3339 with seconds, in UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _encode(window_start: datetime, page: int) -> str:
    payload = json.dumps({"s": _fmt(window_start), "p": page}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode(cursor: str) -> tuple[datetime | None, int]:
    """Opaque in, best-effort out. A cursor we cannot read restarts the
    walk rather than raising — it is the caller's handle, not a key."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        start = datetime.fromisoformat(str(payload["s"]))
        return start, max(1, int(payload.get("p", 1)))
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error):
        # A cursor is the caller's handle, not a key. An unreadable one
        # restarts the walk; it must never fault the request.
        return None, 1


def _as_utc(moment: datetime | None, fallback: datetime) -> datetime:
    if moment is None:
        return fallback
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


@transactions.operation(
    OpType.LIST,
    input_model=ListTransactionsIn,
    output_model=TransactionPage,
    description=(
        "List ledger rows over a date window, oldest window first. Filter "
        "`is_payment` to exclude adjustments and transfers. `invoice_id` and "
        "`custom_field` carry merchant references and are the join keys for "
        "reconciling against an order ledger."
    ),
)
async def list_transactions(
    ctx: ConnectorContext, params: ListTransactionsIn
) -> TransactionPage:
    now = datetime.now(UTC)
    requested_end = _as_utc(params.end_date, now)
    walk_start = _as_utc(
        params.start_date, now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    )

    cursor_start, page = _decode(params.cursor) if params.cursor else (None, 1)
    window_start = cursor_start or walk_start

    while True:
        window_end = min(window_start + timedelta(days=WINDOW_DAYS), requested_end)
        if window_start >= requested_end:
            return TransactionPage(items=[], next_cursor=None, has_more=False)

        payload: dict[str, Any] = await ctx.client.get(
            "/v1/reporting/transactions",
            params={
                "start_date": _fmt(window_start),
                "end_date": _fmt(window_end),
                "fields": "all",
                "balance_affecting_records_only": "Y",
                "page_size": params.limit,
                "page": page,
                **({"transaction_status": params.status} if params.status else {}),
            },
        )

        # PayPal states how current its own ledger is. Past that point
        # there is nothing to find, so stop rather than walk empty windows.
        horizon = _as_utc(
            _parse(payload.get("last_refreshed_datetime")), requested_end
        )
        effective_end = min(requested_end, horizon)

        rows = payload.get("transaction_details") or []
        items = [Transaction.model_validate(row) for row in rows]

        total_pages = int(payload.get("total_pages") or 1)
        if page < total_pages:
            return TransactionPage(
                items=items,
                next_cursor=_encode(window_start, page + 1),
                has_more=True,
            )

        next_window = window_end
        if next_window >= effective_end:
            return TransactionPage(items=items, next_cursor=None, has_more=False)

        # Window exhausted. Hand back a cursor into the next one; only
        # walk on inline when this window was empty, so a caller paging
        # a quiet year is not handed a run of empty pages.
        if items:
            return TransactionPage(
                items=items, next_cursor=_encode(next_window, 1), has_more=True
            )
        window_start, page = next_window, 1


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
