"""Shipped reconciliation declarations.

Importing this module registers them; the MCP server, the REST router
and the DuckDB views all read the same registry. Adding a rail means
adding a declaration here and nothing else.
"""
from __future__ import annotations

from .declaration import ExactRef, Fuzzy, MSISDN, Reconciliation, ResourceRef
from .registry import register

#: Paystack settlements against the merchant's own order ledger.
#: The cross-rail case incumbents don't cover: the payment rail and the
#: system of record are different systems, and only the merchant can
#: join them.
paystack_orders = register(
    Reconciliation(
        name="paystack_orders",
        title="Paystack transactions vs order ledger",
        description=(
            "Match successful Paystack transactions against the merchant's "
            "orders table. Reports transactions with no order (possible "
            "over-collection) and orders with no transaction (unpaid or "
            "settled on another rail)."
        ),
        left=ResourceRef(
            connector="paystack",
            resource="transactions",
            operation="list",
            params={"status": "success"},
        ),
        right=ResourceRef(
            connector="postgres",
            resource="sql",
            operation="query",
            params={
                "sql": (
                    "SELECT order_ref, amount, currency, phone, created_at "
                    "FROM orders WHERE status = 'paid'"
                )
            },
        ),
        keys=[
            ExactRef(left="reference", right="order_ref"),
            Fuzzy(amount="amount", currency="currency", window="48h"),
        ],
        identity=MSISDN(left="customer.phone", right="phone", default_country_code="233"),
    )
)

#: Same shape across a second rail, to prove the declaration generalises.
stripe_orders = register(
    Reconciliation(
        name="stripe_orders",
        title="Stripe charges vs order ledger",
        description=(
            "Match succeeded Stripe charges against the merchant's orders "
            "table, using the charge description as the reference."
        ),
        left=ResourceRef(connector="stripe", resource="charges", operation="list"),
        right=ResourceRef(
            connector="postgres",
            resource="sql",
            operation="query",
            params={
                "sql": (
                    "SELECT order_ref, amount, currency, created_at "
                    "FROM orders WHERE status = 'paid'"
                )
            },
        ),
        keys=[
            ExactRef(left="description", right="order_ref"),
            Fuzzy(
                amount="amount",
                currency="currency",
                timestamp_left="created",
                window="48h",
            ),
        ],
    )
)

__all__ = ["paystack_orders", "stripe_orders"]
