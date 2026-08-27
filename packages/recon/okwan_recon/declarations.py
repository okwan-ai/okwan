"""Shipped reconciliation declarations.

Importing this module registers them; the MCP server, the REST router
and the DuckDB views all read the same registry. Adding a rail means
adding a declaration here and nothing else.
"""
from __future__ import annotations

from .declaration import AmountRef, ExactRef, Fuzzy, MSISDN, Reconciliation, ResourceRef
from .registry import register

#: Payment rail against the merchant's own order ledger — the cross-rail
#: case incumbents don't cover: the processor and the system of record
#: are different systems, and only the merchant can join them.
#:
#: Both sides read through postgres.sql.query here, which keeps the demo
#: reproducible without live rail credentials. Swapping `left` to
#: paystack.transactions.list changes this declaration and nothing else —
#: the MCP tool, the REST route and the DuckDB view all follow.
payments_orders = register(
    Reconciliation(
        name="payments_orders",
        title="Payment rail vs order ledger",
        description=(
            "Match successful payments against the merchant's order ledger. "
            "Reports payments with no order (possible over-collection) and "
            "orders with no payment (unpaid, or settled on another rail). "
            "Joins on shared reference first, then falls back to amount and "
            "currency inside a 48-hour window."
        ),
        left=ResourceRef(
            connector="postgres",
            resource="sql",
            operation="query",
            params={
                "sql": (
                    "SELECT payment_id, reference, amount, currency, phone, "
                    "created_at FROM recon_payments WHERE status = 'success'"
                )
            },
        ),
        right=ResourceRef(
            connector="postgres",
            resource="sql",
            operation="query",
            params={
                "sql": (
                    "SELECT order_ref, amount, currency, phone, created_at "
                    "FROM recon_orders WHERE status = 'paid'"
                )
            },
        ),
        keys=[
            ExactRef(left="reference", right="order_ref"),
            Fuzzy(amount="amount", currency="currency", window="48h"),
        ],
        identity=MSISDN(left="phone", right="phone", country_codes=("233", "225")),
    )
)

#: Payment rail against a live Shopify order ledger — two real systems,
#: not two tables. Matches on the merchant-facing order name first, then
#: falls back to amount inside a window.
#:
#: Reconciles against `net_payment_minor`, not `total_received_minor`.
#: Order #1001 collected 2423.00 and refunded 2124.00; matching the rail's
#: gross charge against the ledger's net is the discrepancy a merchant
#: needs surfaced, not hidden by picking whichever figure agrees.
shopify_orders = register(
    Reconciliation(
        name="shopify_orders",
        title="Payment rail vs Shopify orders",
        description=(
            "Match successful payments against a live Shopify order ledger. "
            "Joins on the merchant-facing order name (#1001), falling back to "
            "amount and currency inside a 7-day window when the rail did not "
            "carry a reference. Reports payments with no order and orders with "
            "no payment."
        ),
        left=ResourceRef(
            connector="postgres",
            resource="sql",
            operation="query",
            params={
                "sql": (
                    "SELECT payment_id, reference, amount, currency, created_at "
                    "FROM recon_payments WHERE status = 'success'"
                )
            },
        ),
        right=ResourceRef(
            connector="shopify",
            resource="orders",
            operation="list",
        ),
        keys=[
            ExactRef(left="reference", right="name"),
            Fuzzy(
                amount="amount",
                currency="currency",
                amount_right="net_payment_minor",
                timestamp_right="created_at",
                window="7d",
            ),
        ],
        amount=AmountRef(left="amount", right="net_payment_minor"),
    )
)

__all__ = ["payments_orders", "shopify_orders"]
