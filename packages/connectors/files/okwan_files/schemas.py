"""The three shapes an imported file becomes.

A tenant uploads a processor's batch report, a processor's transaction
export or a bank statement, and a column mapping turns each row into one
of these. Whatever processor or bank produced the file, the rows land in
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

from pydantic import BaseModel, Field


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
    )
}

PROVENANCE = frozenset(_Provenance.model_fields)

#: Deductions, stored as positive magnitudes whatever sign the file used.
DEDUCTIONS = frozenset({"fees_minor", "chargebacks_minor", "fee_minor"})

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
