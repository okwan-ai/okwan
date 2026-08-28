"""Shipped reconciliation declarations.

Importing this module registers them; the MCP server, the REST router
and the DuckDB views all read the same registry. Adding a rail means
adding a declaration here and nothing else.
"""
from __future__ import annotations

from .declaration import (
    AmountRef, ExactRef, Explains, Fuzzy, MSISDN, Reconciliation, ResourceRef,
)
from .registry import register

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
        # The rail feed carries no refund rows, so a refunded order will
        # always disagree with the charge that funded it. The ledger knows
        # why. Reporting that as unexplained trains people to ignore the
        # number; reporting it as explained keeps it visible and ranked.
        explains=[
            Explains(path="total_refunded_minor", side="right", label="refund"),
        ],
    )
)

__all__ = ["shopify_orders"]
