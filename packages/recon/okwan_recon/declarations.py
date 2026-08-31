"""Shipped reconciliation declarations.

Importing this module registers them; the MCP server, the REST router
and the DuckDB views all read the same registry. Adding a rail means
adding a declaration here and nothing else.
"""
from __future__ import annotations

from .declaration import (
    AmountRef,
    ExactRef,
    Explains,
    Fuzzy,
    Reconciliation,
    ResourceRef,
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


#: The wedge, concretely: one order ledger, two payment rails. A US
#: merchant collecting on Stripe and PayPal against the same Shopify
#: store has no single place where the three agree, and no rail can
#: tell them which orders are unpaid — each one only knows its own.
#:
#: Joins on `invoice_id`, which is what a Shopify-through-PayPal
#: checkout puts the merchant's order name into. The fuzzy fallback
#: exists because a rail can lose the reference — a manual invoice, a
#: phone order, a checkout that dropped the field — and amount inside
#: a window is the only thing left. It is a weaker signal and reports
#: ambiguity rather than guessing: orders #1004 and #1005 are both
#: 450.00 USD, so without a reference they are genuinely
#: indistinguishable and the engine must say so.
#:
#: Compares `net_minor`, not `amount_minor`. PayPal reports its fee as
#: a separate negative figure, so the gross charge never equals what
#: the merchant receives. Reconciling gross against the order total
#: agrees on every row and hides the fee entirely; reconciling net
#: surfaces it as a discrepancy the ledger can explain.
shopify_paypal = register(
    Reconciliation(
        name="shopify_paypal",
        title="PayPal vs Shopify orders",
        description=(
            "Match PayPal payments against a live Shopify order ledger. "
            "Joins on the order name carried into PayPal as `invoice_id`, "
            "falling back to amount and currency inside a 7-day window when "
            "the rail did not carry a reference. Reports orders with no "
            "payment, payments with no order, and pairs whose figures "
            "disagree."
        ),
        left=ResourceRef(connector="shopify", resource="orders", operation="list"),
        right=ResourceRef(connector="paypal", resource="transactions", operation="list"),
        keys=[
            ExactRef(left="name", right="invoice_id"),
            Fuzzy(
                amount="net_payment_minor",
                currency="currency",
                amount_right="net_minor",
                timestamp_left="created_at",
                timestamp_right="initiated_at",
                window="7d",
            ),
        ],
        amount=AmountRef(left="net_payment_minor", right="net_minor"),
        # A refunded order will always disagree with the charge that
        # funded it, because the PayPal feed carries the refund as its
        # own row rather than adjusting the original. The ledger knows
        # why. Unexplained, it trains people to ignore the number.
        # A rail fee is a known cause, not a mystery: the merchant is
        # owed the order total and receives it less PayPal's cut. Left
        # unexplained it fills the chase-list with the one discrepancy
        # that appears on every single row, and a list that always fires
        # is a list nobody reads.
        explains=[
            Explains(path="fee_minor", side="right", label="rail_fee"),
            Explains(path="total_refunded_minor", side="left", label="refund"),
        ],
    )
)

__all__ = ["shopify_orders", "shopify_paypal"]
