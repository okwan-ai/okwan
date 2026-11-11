"""A sample store's week, checked by the real engine.

A new account has nothing to check until a merchant's Shopify, PayPal and
Stripe are all connected, which can take days. The sample is the same
check over made-up rows, so the first result is seconds away: eight
orders, two of them collected on both PayPal and Stripe, one never
collected, one split across the two.

Only the read is replaced. The rows below are shaped as the three
connectors return them, and `run()` hands them to `run_across` in place of
the connectors; the matching, the fold and every figure in the result are
the engine's own, as for a real merchant. Nothing here belongs to any
tenant, and nothing is stored.

Dates are counted back from the day it runs, so the week is always the
last one. Every name, reference and amount is made up.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from . import declarations
from .across import AcrossResult, run_across
from .coverage import Coverage

Row = dict[str, Any]

#: The fold the sample runs: Shopify orders across PayPal and Stripe.
FOLD = declarations.rails.name
STORE = "Sample store"

#: (order, total in cents, days ago, PayPal took, Stripe took). None: that
#: rail took nothing for the order.
WEEK: list[tuple[str, int, int, int | None, int | None]] = [
    ("#2041", 8400, 6, None, 8400),
    ("#2042", 12650, 6, 12650, None),
    ("#2043", 31200, 5, 31200, 31200),   # collected twice
    ("#2044", 5825, 4, None, 5825),
    ("#2045", 24000, 3, 10000, 14000),   # split across both
    ("#2046", 9740, 3, None, None),      # never collected
    ("#2047", 18999, 2, 18999, 18999),   # collected twice
    ("#2048", 4500, 1, None, 4500),
]


def _no_credentials(connector: str, fields: tuple[str, ...]) -> dict[str, str]:
    """The sample reads no rail, so it holds no credential."""
    return {}


def rows(now: datetime | None = None) -> dict[str, list[Row]]:
    """The week's rows by connector, as each connector's list returns them."""
    now = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    orders: list[Row] = []
    paypal: list[Row] = []
    stripe: list[Row] = []
    for name, total, days_ago, on_paypal, on_stripe in WEEK:
        placed = now - timedelta(days=days_ago, hours=3)
        taken = (on_paypal or 0) + (on_stripe or 0)
        orders.append({
            "name": name, "currency": "USD", "created_at": placed.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "financial_status": "PAID" if taken else "PENDING", "fulfillment_status": "FULFILLED" if taken else "UNFULFILLED",
            "total_price_minor": total, "total_received_minor": taken, "total_refunded_minor": 0,
            "net_payment_minor": taken,
        })
        ref = name.lstrip("#")
        if on_paypal:
            fee = -(round(on_paypal * 0.0349) + 49)
            paypal.append({
                "transaction_id": f"SAMPLE-PP-{ref}", "invoice_id": name, "currency": "USD",
                "initiated_at": (placed + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "amount_minor": on_paypal, "fee_minor": fee, "net_minor": on_paypal + fee,
            })
        if on_stripe:
            fee = round(on_stripe * 0.029) + 30
            stripe.append({
                "id": f"ch_sample_{ref}", "order_ref": name, "currency": "usd",
                "created": int((placed + timedelta(minutes=3)).timestamp()), "amount": on_stripe,
                "amount_refunded": 0, "fee_minor": fee, "net_minor": on_stripe - fee, "status": "succeeded",
            })
    return {"shopify": orders, "paypal": paypal, "stripe": stripe}


async def run(now: datetime | None = None) -> AcrossResult:
    """The sample week through the real fold. `now` fixes the week for tests."""
    week = rows(now)

    async def read(ref, resolver, cap, overrides):
        side = week[ref.connector]
        return side, Coverage(
            source=ref.qualified, records=len(side), cap=cap, truncated=False,
            span_start=(overrides or {}).get("start_date"), span_end=(overrides or {}).get("end_date"),
        )

    return await run_across(declarations.rails, _no_credentials, fetch=read)
