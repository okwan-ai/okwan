"""The shapes an imported file becomes.

A tenant uploads a processor's batch report, a processor's transaction
export, a bank statement, a delivery platform's transaction or payout
report, or a summary of its sales tax returns, and a column mapping turns
each row into one of these. Whatever processor or bank produced the file, the rows land in
the same shape, so one reconciliation covers every source and a new
processor is a mapping, not code (§9 2026-11-06, Tier 1 plan).

Each model is defined once. The mapping reads its fields from here (what
can be mapped, what is required, how each value is parsed), and the
files connector serves the same models as its Resources.

Money is integer minor units, converted through
`okwan_core.currency.money_to_minor`. Fees and chargebacks are deductions
and are stored as positive magnitudes, whatever sign the file used, so a
fee rule can subtract them; adjustments keep their sign.

What is never here: a card number. A card column can only be mapped to
`card_last4`, which keeps four digits; a full card number anywhere in a
file refuses the whole file (`guard.py`).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from pydantic import BaseModel, Field, computed_field


class _Provenance(BaseModel):
    """Where a row came from. Filled by Okwan, never mapped."""

    file_id: str = Field(description="The import this row came from")
    row: int = Field(description="Its row in the file (the transaction's position, for OFX)")
    layout: str = Field(description="The column mapping that produced it")


class SettlementBatch(_Provenance):
    """One batch from a processor's settlement report."""

    batch_id: str = Field(description="The processor's batch reference")
    closed_on: dt.date = Field(description="The day the batch closed")
    gross_minor: int = Field(description="Sales in the batch, before fees, in minor units")
    currency: str = Field(description="ISO 4217 code")
    fees_minor: int | None = Field(None, description="Processing fees, a positive amount")
    chargebacks_minor: int | None = Field(None, description="Chargebacks, a positive amount")
    adjustments_minor: int | None = Field(None, description="Adjustments, signed")
    net_minor: int | None = Field(None, description="What the processor says it paid out")
    mid: str | None = Field(None, description="Merchant ID")
    transaction_count: int | None = Field(None, description="Transactions in the batch")


class SettlementTransaction(_Provenance):
    """One card transaction from a processor's export."""

    transacted_on: dt.date = Field(description="The day of the transaction")
    amount_minor: int = Field(description="Signed: a refund is negative")
    currency: str = Field(description="ISO 4217 code")
    transaction_id: str | None = Field(None, description="The processor's transaction reference")
    batch_id: str | None = Field(None, description="The batch it settled in")
    order_ref: str | None = Field(None, description="Invoice or order number")
    type: str | None = Field(None, description="Sale, refund, void, as the file names it")
    auth_code: str | None = Field(None, description="Authorization code")
    card_brand: str | None = Field(None, description="Visa, Mastercard, …")
    card_last4: str | None = Field(None, description="The last four digits only")
    mid: str | None = Field(None, description="Merchant ID")
    fee_minor: int | None = Field(None, description="Fee on this transaction, a positive amount")


class BankDeposit(_Provenance):
    """One line of a bank statement."""

    fitid: str = Field(description="The bank's transaction id; derived from the line when the file has none")
    posted_on: dt.date = Field(description="The day the bank posted it")
    amount_minor: int = Field(description="Signed: money in is positive")
    currency: str = Field(description="ISO 4217 code")
    description: str | None = Field(None, description="The statement text")
    type: str | None = Field(None, description="Credit, debit, deposit, as the file names it")
    account_last4: str | None = Field(None, description="The last four digits of the account")


class DeliveryLine(_Provenance):
    """One line of a delivery platform's transaction report: an order, or a
    charge or credit not tied to one (a marketing fee, an adjustment).

    DoorDash, Uber Eats and Grubhub each export one; whatever the platform,
    a line lands here. Every deduction is kept apart, as a positive amount,
    so what the platform kept from a sale reads by kind of charge.
    """

    ordered_on: dt.date = Field(description="The day of the order or charge")
    currency: str = Field(description="ISO 4217 code")
    order_id: str | None = Field(None, description="The platform's order id; empty on a charge not tied to one")
    transaction_id: str | None = Field(None, description="The platform's id for this line")
    platform: str | None = Field(None, description="DoorDash, Uber Eats, Grubhub…, when the report names it")
    store_id: str | None = Field(None, description="The platform's id for the store")
    pos_order_ref: str | None = Field(
        None, description="The point-of-sale order id the platform's integration passed, when it did")
    status: str | None = Field(None, description="Delivered, cancelled…, as the report names it")
    type: str | None = Field(None, description="Order, adjustment, marketing…, as the report names it")
    subtotal_minor: int | None = Field(None, description="What the customer paid for the items, before tax")
    tax_minor: int | None = Field(None, description="Tax the platform passed to the store")
    tip_minor: int | None = Field(None, description="Tips to the store (not to the courier)")
    commission_minor: int | None = Field(None, description="The platform's commission, a positive amount")
    processing_fee_minor: int | None = Field(None, description="Payment processing fee, a positive amount")
    marketing_minor: int | None = Field(
        None, description="Marketing fees and store-funded promotions, a positive amount")
    error_charges_minor: int | None = Field(
        None, description="Charged back for a missing or wrong item, a positive amount")
    refunds_minor: int | None = Field(None, description="Refunded to the customer at the store's cost, positive")
    adjustments_minor: int | None = Field(None, description="Other adjustments, signed: a credit is positive")
    net_minor: int | None = Field(
        None, description="What this line adds to the payout; derived from the parts when the report has no column")
    payout_id: str | None = Field(None, description="The payout that paid this line")
    payout_on: dt.date | None = Field(None, description="The day of that payout")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def withheld_minor(self) -> int | None:
        """What the platform kept from the sale: items, tax and tips, less what it paid."""
        if self.subtotal_minor is None or self.net_minor is None:
            return None
        return self.subtotal_minor + (self.tax_minor or 0) + (self.tip_minor or 0) - self.net_minor

    @computed_field  # type: ignore[prop-decorator]
    @property
    def commission_rate_bps(self) -> int | None:
        """Commission as a share of the subtotal, in basis points (2500 is 25%)."""
        if not self.subtotal_minor or self.commission_minor is None:
            return None
        return round(self.commission_minor * 10_000 / self.subtotal_minor)


class DeliveryPayout(_Provenance):
    """One payout from a delivery platform to the store's bank account."""

    payout_id: str = Field(description="The platform's payout id")
    paid_on: dt.date = Field(description="The day the platform sent it")
    net_minor: int = Field(description="What it paid")
    currency: str = Field(description="ISO 4217 code")
    platform: str | None = Field(None, description="DoorDash, Uber Eats, Grubhub…, when the report names it")
    store_id: str | None = Field(None, description="The platform's id for the store")
    status: str | None = Field(None, description="Paid, pending…, as the report names it")
    period_start: dt.date | None = Field(None, description="The first day of sales it pays")
    period_end: dt.date | None = Field(None, description="The last day of sales it pays")
    sales_minor: int | None = Field(None, description="Sales in the payout, before deductions")
    commission_minor: int | None = Field(None, description="Commission across the payout, a positive amount")
    marketing_minor: int | None = Field(None, description="Marketing across the payout, a positive amount")
    error_charges_minor: int | None = Field(None, description="Error charges across the payout, positive")
    adjustments_minor: int | None = Field(None, description="Adjustments across the payout, signed")
    payout_fee_minor: int | None = Field(
        None, description="A fee on the payout itself (an instant payout), a positive amount")


class TaxFiling(_Provenance):
    """One sales tax return: what it reported for one jurisdiction over one period.

    From the return itself or a filing service's report (Avalara Returns'
    liability worksheet, a state's filing confirmation), typed or exported
    once a period. The tax it reported is compared with the tax the tax
    engine committed for the same state and days.
    """

    jurisdiction: str = Field(description="The state or jurisdiction, as the tax engine names it (WY)")
    period_start: dt.date = Field(description="The first day the return covers")
    period_end: dt.date = Field(description="The last day the return covers")
    tax_minor: int = Field(description="The tax the return reported as collected, before any discount")
    currency: str = Field(description="ISO 4217 code")
    taxable_minor: int | None = Field(None, description="Taxable sales the return reported")
    gross_sales_minor: int | None = Field(None, description="Gross sales the return reported")
    adjustments_minor: int | None = Field(
        None, description="Corrections to earlier periods carried on this return, signed")
    vendor_discount_minor: int | None = Field(
        None, description="A timely-filing discount the state let the seller keep, a positive amount")
    paid_minor: int | None = Field(None, description="What was paid with the return")
    filed_on: dt.date | None = Field(None, description="The day it was filed")
    return_id: str | None = Field(None, description="The return's or confirmation's number")


@dataclass(frozen=True, slots=True)
class Kind:
    name: str
    model: type[BaseModel]
    #: The field a row is filed under by date, for windowed reads.
    date_field: str
    description: str


KINDS: dict[str, Kind] = {
    k.name: k for k in (
        Kind("settlement_batches", SettlementBatch, "closed_on",
             "Batches from a processor's settlement report"),
        Kind("settlement_transactions", SettlementTransaction, "transacted_on",
             "Card transactions from a processor's export"),
        Kind("bank_deposits", BankDeposit, "posted_on",
             "Lines of a bank statement"),
        Kind("delivery_lines", DeliveryLine, "ordered_on",
             "Orders and charges from a delivery platform's transaction report"),
        Kind("delivery_payouts", DeliveryPayout, "paid_on",
             "Payouts from a delivery platform's payout report"),
        Kind("tax_filings", TaxFiling, "period_end",
             "Sales tax returns: what each reported for a jurisdiction and period"),
    )
}

PROVENANCE = frozenset(_Provenance.model_fields)

#: Deductions, stored as positive magnitudes whatever sign the file used.
DEDUCTIONS = frozenset({
    "fees_minor", "chargebacks_minor", "fee_minor",
    "commission_minor", "processing_fee_minor", "marketing_minor", "error_charges_minor",
    "refunds_minor", "payout_fee_minor", "vendor_discount_minor",
})

#: Fields derived when the file has no column for them.
DERIVED = frozenset({"fitid"})

#: Columns that stand in for a signed amount: money in, money out.
SPLIT_AMOUNT = ("credit", "debit")


def parse_type(name: str, annotation) -> str:
    """How a mapped value is read, from the field itself."""
    if name.endswith("_last4"):
        return "last4"
    if name == "currency":
        return "currency"
    base = getattr(annotation, "__args__", (annotation,))[0] if _optional(annotation) else annotation
    if base is dt.date:
        return "date"
    if base is int:
        return "money" if name.endswith("_minor") else "int"
    return "text"


def _optional(annotation) -> bool:
    return type(None) in getattr(annotation, "__args__", ())


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    parse: str
    required: bool
    description: str


def fields_of(kind: str) -> dict[str, FieldSpec]:
    """What a mapping for this kind can fill: every field but provenance."""
    model = KINDS[kind].model
    return {
        name: FieldSpec(name=name, parse=parse_type(name, f.annotation),
                        required=f.is_required(), description=f.description or "")
        for name, f in model.model_fields.items()
        if name not in PROVENANCE
    }
