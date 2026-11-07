"""Shipped reconciliation declarations.

Importing this module registers them; the MCP server, the REST router
and the DuckDB views all read the same registry. Adding a rail means
adding a declaration here and nothing else.
"""
from __future__ import annotations

from .across import AcrossRails, Rail
from .declaration import (
    Agree,
    AmountRef,
    ExactRef,
    Explains,
    Fuzzy,
    GroupRef,
    GroupSum,
    Reconciliation,
    ResourceRef,
    SettlementLag,
)
from .registry import register, register_across

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
#: The fallback compares gross to gross, as `shopify_stripe` does.
#: PayPal's net is the payment less its fee, so net to net never equals
#: an order total: against the live ledger not one payment's net met
#: any order's, and the rule could not fire.
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
                amount="total_received_minor",
                currency="currency",
                amount_right="amount_minor",
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
        # PayPal's list defaults to the last 30 days, so without this the
        # reconciliation goes blank once the ledger stops being recent.
        # Shopify's side has no start_date and is read in full.
        lookback="90d",
    )
)


#: The same order ledger against the other half of the US merchant
#: stack. With `shopify_paypal` beside it, each order can be traced to
#: the rail that collected it — and an order neither rail knows about is
#: unpaid.
#:
#: Joins on `order_ref`, which the Stripe schema lifts out of charge
#: metadata — where Shopify Payments writes the order name.
#:
#: The fuzzy fallback compares gross to gross: what the ledger says it
#: received against what Stripe charged. Net to net cannot work here.
#: Stripe's net is the charge less its fee, so it never equals an order
#: total and the rule would never fire on a real charge. Gross to net
#: pairs on coincidence — a refunded order's remainder can equal an
#: unrelated small charge.
#:
#: Compares `net_minor` once paired. Stripe nets refunds on the charge
#: itself, as Shopify does on the order, so a refund cancels on both
#: sides and needs no explanation of its own — declaring one would only
#: let an unrelated figure of the same size explain a real break. What
#: remains is the fee, which Stripe reports positive where PayPal
#: reports it negative. `Explains` compares magnitude, so either works.
shopify_stripe = register(
    Reconciliation(
        name="shopify_stripe",
        title="Stripe vs Shopify orders",
        description=(
            "Match Stripe charges against a live Shopify order ledger. Joins "
            "on the order name carried in charge metadata, falling back to "
            "gross amount and currency inside a 7-day window when the charge "
            "carried no reference. Reports orders with no charge, charges "
            "with no order, and pairs whose figures disagree."
        ),
        left=ResourceRef(connector="shopify", resource="orders", operation="list"),
        # A failed attempt carries the same order_ref as the retry that
        # succeeded. Left in, it can take the reference join — Stripe's
        # newest-first order only usually puts the success ahead of it.
        right=ResourceRef(
            connector="stripe",
            resource="charges",
            operation="list",
            params={"status": "succeeded"},
        ),
        keys=[
            ExactRef(left="name", right="order_ref"),
            Fuzzy(
                amount="total_received_minor",
                currency="currency",
                amount_right="amount",
                timestamp_left="created_at",
                timestamp_right="created",
                window="7d",
            ),
        ],
        amount=AmountRef(left="net_payment_minor", right="net_minor"),
        explains=[
            Explains(path="fee_minor", side="right", label="rail_fee"),
        ],
        # Neither side's list input takes start_date/end_date today, so
        # this bounds nothing yet. Declared so the span is stated, and so
        # it applies the moment either connector accepts a window.
        lookback="90d",
    )
)


#: The question neither rail declaration can answer alone: was each order
#: paid once? An order collected on both Stripe and PayPal is a clean,
#: fee-explained match on each, so the double collection only exists in
#: the fold.
#:
#: Compares each rail's gross take against the order total, not against
#: what Shopify says it received. Shopify records its own checkout; it
#: cannot see that a second rail also charged the customer.
rails = register_across(
    AcrossRails(
        name="rails",
        title="Shopify orders across payment rails",
        description=(
            "Fold every payment rail reconciled against the Shopify order "
            "ledger into one verdict per order: collected once, collected "
            "twice, split across rails, collected inconsistently, uncollected, "
            "or unverifiable because a rail could not have read it. Each "
            "verdict names the rails and carries each rail's discrepancy."
        ),
        rails=[
            Rail(reconciliation="shopify_paypal", collected="amount_minor"),
            Rail(reconciliation="shopify_stripe", collected="amount"),
        ],
        ledger_total="total_price_minor",
    )
)



#: A processor's batches against the bank deposits that paid them, both
#: imported from files (Tier 1, `okwan_files`), whatever processor and bank
#: produced them. A deposit pays the batches closed since the last one: a
#: weekend's three land as one deposit, a holiday later still. So batches
#: are grouped (E1), counted in US business days after they closed and
#: never before (E2), and kept to one merchant id per deposit. The figure
#: is each batch's net, which the importer derives as gross less fees and
#: chargebacks plus adjustments when the report has no net column; a
#: processor that deposits gross has its gross column mapped to net. Money
#: out on the statement (a fee, a transfer) is not read at all.
batches_deposits = register(
    Reconciliation(
        name="batches_deposits",
        title="Processor batches vs bank deposits",
        description=(
            "Match a processor's settlement batches against the bank deposits that paid them, "
            "both from imported files. Batches closed over a weekend or holiday are summed into "
            "the one deposit that paid them, up to three US business days after they closed. "
            "Reports batches never paid, deposits no batch explains, and deposits two groupings "
            "of batches would both fit."
        ),
        left=ResourceRef(connector="files", resource="settlement_batches"),
        right=ResourceRef(connector="files", resource="bank_deposits", params={"direction": "in"}),
        keys=[
            GroupSum(
                many="left", amount="net_minor", amount_one="amount_minor",
                timestamp_many="closed_on", timestamp_one="posted_on", share=("mid",),
                lag=SettlementLag(max_days=3), ref_many="batch_id", ref_one="fitid",
            ),
        ],
        lookback="120d",
    )
)



#: Each Square payment against the payout line that settled it. Square
#: pays a payment out a business day or so after it completes, as a CHARGE
#: line naming the payment and Square's fee on it, so the join is exact
#: (the payment id) and the difference between what the buyer paid and
#: what the line adds to the payout is the fee. A payment with no line yet
#: has not been paid out: recent ones are expected to read that way.
#: Refunds are their own REFUND lines and are not read here.
square_payments_payouts = register(
    Reconciliation(
        name="square_payments_payouts",
        title="Square payments vs payouts",
        description=(
            "Match each completed Square payment against the payout line that settled it, by "
            "payment id. Square's processing fee explains the gap between what the buyer paid and "
            "what the payout carried. Reports payments not yet paid out, payout lines with no "
            "payment, and lines whose amount the fee does not explain."
        ),
        left=ResourceRef(connector="square", resource="payments", params={"status": "COMPLETED"}),
        right=ResourceRef(connector="square", resource="payout_entries", params={"type": "CHARGE"}),
        keys=[ExactRef(left="id", right="payment_id", case_sensitive=True)],
        amount=AmountRef(left="total_minor", right="net_minor"),
        explains=[Explains(path="fee_minor", side="right", label="square_fee")],
        lookback="60d",
    )
)


#: Each Square payout against the deposit the bank statement shows, from an
#: imported statement (`files.bank_deposits`). A payout names the day it
#: arrives; the bank posts it that day or up to two US business days
#: after, never before, and the amounts are equal to the cent. There is no
#: shared reference a statement reliably carries, so it is amount, currency
#: and that window, and two deposits that would both fit are ambiguous
#: rather than guessed between.
square_payouts_deposits = register(
    Reconciliation(
        name="square_payouts_deposits",
        title="Square payouts vs bank deposits",
        description=(
            "Match each paid Square payout against the deposit on the imported bank statement, by "
            "amount and currency, on its arrival date or up to two US business days after. Reports "
            "payouts the bank never shows and deposits no payout explains."
        ),
        left=ResourceRef(connector="square", resource="payouts", params={"status": "PAID"}),
        right=ResourceRef(connector="files", resource="bank_deposits", params={"direction": "in"}),
        keys=[
            Fuzzy(
                amount="amount_minor", currency="currency", amount_right="amount_minor",
                timestamp_left="arrival_date", timestamp_right="posted_on",
                lag=SettlementLag(max_days=2),
            ),
        ],
        lookback="120d",
    )
)

#: Each Clover ticket against the payments recorded on it. A ticket is paid
#: when the `amount` of its successful payments sums to its total, both
#: before the tip; a split check is several payments on one order, summed.
#: What it finds: tickets with nothing taken (a walkout, a payment voided
#: after the ticket was closed), tickets paid short or twice, and payments
#: on no ticket in the window. A ticket still open now is unpaid because it
#: is open: the most recent ones are expected to read that way.
clover_orders_payments = register(
    Reconciliation(
        name="clover_orders_payments",
        title="Clover orders vs payments",
        description=(
            "Match each Clover order against the successful payments recorded on it, by order id; "
            "a split check's payments are summed. Reports orders with no payment, orders paid "
            "short or over their total, and payments on no order in the window."
        ),
        left=ResourceRef(connector="clover", resource="orders", params={"skip_empty": True}),
        right=ResourceRef(connector="clover", resource="payments", params={"result": "SUCCESS"}),
        keys=[GroupRef(left="id", right="order_id", many="right", case_sensitive=True)],
        amount=AmountRef(left="total_minor", right="amount_minor"),
        lookback="60d",
    )
)


#: Each Clover card payment against the processor's export of what it
#: settled, from an imported file (`files.settlement_transactions`). Clover
#: reports neither the processor nor the bank, and no reference is common
#: to every processor's export, so it is the charge (amount plus tip), the
#: currency and the business day: the export dates a transaction on the
#: sale's own day in the merchant's timezone, or the next business day
#: where it dates by batch. Amount and day cannot tell two same-sized
#: sales apart, so the card's last four digits and the authorization code
#: must not disagree where both sides carry them; two candidates that
#: still both fit are ambiguous, never guessed between. What it finds:
#: card sales the processor never settled (a batch that did not close, an
#: offline sale declined when the device reconnected), and settled
#: transactions with no Clover payment. Refunds are their own export rows
#: and are not read here.
clover_payments_settlements = register(
    Reconciliation(
        name="clover_payments_settlements",
        title="Clover card payments vs processor settlement",
        description=(
            "Match each successful Clover card payment against the processor's settled "
            "transactions from an imported export, by the amount charged and the currency, on the "
            "sale's day or the next business day, where the card's last four digits and the "
            "authorization code do not disagree. Reports card sales the processor never settled "
            "and settled transactions with no Clover payment."
        ),
        left=ResourceRef(connector="clover", resource="payments",
                         params={"result": "SUCCESS", "card_only": True}),
        right=ResourceRef(connector="files", resource="settlement_transactions",
                          params={"direction": "in"}),
        keys=[
            Fuzzy(
                amount="total_minor", currency="currency", amount_right="amount_minor",
                timestamp_left="created_on", timestamp_right="transacted_on",
                lag=SettlementLag(max_days=1),
                agree=(Agree(left="card_last4"), Agree(left="auth_code")),
            ),
        ],
        lookback="120d",
    )
)

__all__ = ["batches_deposits", "clover_orders_payments", "clover_payments_settlements", "rails",
           "shopify_orders", "shopify_paypal", "shopify_stripe", "square_payments_payouts",
           "square_payouts_deposits"]
