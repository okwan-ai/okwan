"""Shipped reconciliation declarations.

Importing this module registers them; the MCP server, the REST router
and the DuckDB views all read the same registry. Adding a rail means
adding a declaration here and nothing else.
"""
from __future__ import annotations

from .declaration import ExactRef, Fuzzy, MSISDN, Reconciliation, ResourceRef
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
        identity=MSISDN(left="phone", right="phone", default_country_code="233"),
    )
)

__all__ = ["payments_orders"]
