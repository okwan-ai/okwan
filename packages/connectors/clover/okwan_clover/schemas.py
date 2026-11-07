"""Clover connector schemas: flat, typed rows for REST, SQL and MCP.

Clover states money as integer cents and time as epoch milliseconds, and
nests what a row needs (`order.id`, `cardTransaction.last4`). A
before-validator on each model flattens one raw Clover object into one
row, given the merchant's timezone and currency through the validation
context (`{"timezone": ..., "currency": ...}`); validating a row that is
already flat is a no-op, so rows round-trip through REST and SQL.

**Amounts.** A payment's `amount` is before the tip, which Clover states
apart as `tipAmount` (a tip adjust changes only that), so `total_minor`
is what the card was charged: amount plus tip. An order's `total` is
before the tip as well, so an order is paid when its payments' `amount`
sums to it. A refund's `amount` already includes its tax and tip.

**The business day.** Every row carries `created_on`, the day it happened
in the merchant's own timezone, beside the instant. A processor's export
and a bank statement write that local day, so a 9 p.m. Pacific sale is
the same day on both sides instead of tomorrow in UTC.

**Card data.** A Clover card transaction carries the card's first six
digits, the cardholder's name, a card token and vaulting details as well
as the last four. None of it is a card number, and none of it is needed
to reconcile, so a row keeps the brand, the last four digits, the
authorization code and the gateway's reference, and nothing else from
`cardTransaction` ever leaves the validator.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from okwan_core.pagination import CursorPage
from pydantic import BaseModel, Field, ValidationInfo, computed_field, model_validator


def _ms(value: Any) -> datetime | None:
    """An epoch-milliseconds instant, in UTC."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _zone(info: ValidationInfo | None) -> ZoneInfo:
    name = (info.context or {}).get("timezone") if info is not None and info.context else None
    try:
        return ZoneInfo(name) if name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _currency(info: ValidationInfo | None, *found: Any) -> str | None:
    for value in (*found, (info.context or {}).get("currency") if info is not None and info.context else None):
        if isinstance(value, str) and len(value.strip()) == 3:
            return value.strip().upper()
    return None


def _cents(value: Any) -> int | None:
    return None if isinstance(value, bool) or not isinstance(value, int) else value


def _flag(data: dict, key: str) -> bool | None:
    return data.get(key) if isinstance(data.get(key), bool) else None


def _id(obj: Any) -> str | None:
    return obj.get("id") if isinstance(obj, dict) and isinstance(obj.get("id"), str) else None


def _elements(obj: Any) -> list[dict]:
    """A nested Clover collection: `{"elements": [...]}`, or a bare list."""
    items = obj.get("elements") if isinstance(obj, dict) else obj
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def _times(data: dict, info: ValidationInfo | None) -> dict[str, Any]:
    created = _ms(data.get("createdTime"))
    return {
        "created_at": created,
        "created_on": created.astimezone(_zone(info)).date() if created else None,
        "modified_at": _ms(data.get("modifiedTime")),
    }


def _card(txn: Any) -> dict[str, str | None]:
    """The brand, the last four digits, the authorization code and the
    gateway's reference: everything else in a card transaction stays out."""
    txn = txn if isinstance(txn, dict) else {}
    last4 = txn.get("last4")
    return {
        "card_brand": txn.get("cardType") if isinstance(txn.get("cardType"), str) else None,
        "card_last4": last4 if isinstance(last4, str) and len(last4) == 4 and last4.isdigit() else None,
        "auth_code": txn.get("authCode") if isinstance(txn.get("authCode"), str) else None,
        "processor_ref": txn.get("referenceId") if isinstance(txn.get("referenceId"), str) else None,
    }


def _tender(obj: Any) -> str | None:
    """`com.clover.tender.credit_card` → `credit_card`; a custom tender's label otherwise."""
    if not isinstance(obj, dict):
        return None
    key = obj.get("labelKey")
    if isinstance(key, str) and key.startswith("com.clover.tender."):
        return key.removeprefix("com.clover.tender.")
    label = obj.get("label")
    return label if isinstance(label, str) else None


#: Tenders that settle through the card processor (and so appear in its export).
CARD_TENDERS = frozenset({"credit_card", "debit_card"})


class Payment(BaseModel):
    """One Clover payment against an order: a card, cash or other tender."""

    id: str
    order_id: str | None = Field(None, description="The order it pays; the join key to orders")
    result: str | None = Field(None, description="SUCCESS, FAIL, VOIDED, AUTH, PENDING…")
    created_at: datetime | None = None
    created_on: date | None = Field(None, description="The day it happened, in the merchant's timezone")
    modified_at: datetime | None = None
    currency: str | None = None
    amount_minor: int | None = Field(None, description="Before the tip, tax included")
    tip_minor: int | None = None
    tax_minor: int | None = None
    cashback_minor: int | None = Field(None, description="Cash handed back on a debit sale")
    refunded_minor: int = Field(0, description="Refunded so far, voided refunds excluded")
    tender: str | None = Field(None, description="credit_card, debit_card, cash, external_payment…")
    external_payment_id: str | None = Field(None, description="The point of sale's own id for the payment")
    offline: bool | None = Field(None, description="Taken offline; it can still fail when the device reconnects")
    card_brand: str | None = Field(None, description="VISA, MC, AMEX, DISCOVER…")
    card_last4: str | None = Field(None, description="The last four digits only")
    auth_code: str | None = Field(None, description="The authorization code; a processor export carries it too")
    processor_ref: str | None = Field(None, description="The gateway's reference for the card transaction")
    card_entry: str | None = Field(None, description="EMV_CONTACT, EMV_CONTACTLESS, SWIPED, KEYED…")

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict) or "createdTime" not in data:
            return data
        txn = data.get("cardTransaction") if isinstance(data.get("cardTransaction"), dict) else {}
        refunds = [r for r in _elements(data.get("refunds")) if not r.get("voided")]
        refunded = [a for a in (_cents(r.get("amount")) for r in refunds) if a is not None]
        order = data.get("order") if isinstance(data.get("order"), dict) else {}
        return {
            "id": data.get("id"), "order_id": _id(order), "result": data.get("result"),
            **_times(data, info),
            "currency": _currency(info, txn.get("currency"), order.get("currency")),
            "amount_minor": _cents(data.get("amount")), "tip_minor": _cents(data.get("tipAmount")),
            "tax_minor": _cents(data.get("taxAmount")), "cashback_minor": _cents(data.get("cashbackAmount")),
            "refunded_minor": sum(refunded),
            "tender": _tender(data.get("tender")), "external_payment_id": data.get("externalPaymentId"),
            "offline": _flag(data, "offline"),
            **_card(txn), "card_entry": txn.get("entryType") if isinstance(txn.get("entryType"), str) else None,
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_minor(self) -> int | None:
        """What the card was charged: the amount plus the tip."""
        if self.amount_minor is None:
            return None
        return self.amount_minor + (self.tip_minor or 0)


class Refund(BaseModel):
    """One refund of a payment, to the card or tender that paid it."""

    id: str
    payment_id: str | None = None
    order_id: str | None = None
    status: str | None = Field(None, description="SUCCESS, FAIL or PENDING")
    voided: bool = False
    created_at: datetime | None = None
    created_on: date | None = Field(None, description="The day it happened, in the merchant's timezone")
    modified_at: datetime | None = None
    currency: str | None = None
    amount_minor: int | None = Field(None, description="Refunded, tax and tip included, a positive amount")
    tax_minor: int | None = None
    tip_minor: int | None = None
    reason: str | None = None
    card_brand: str | None = None
    card_last4: str | None = Field(None, description="The last four digits only")
    auth_code: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict) or "createdTime" not in data:
            return data
        txn = data.get("cardTransaction") if isinstance(data.get("cardTransaction"), dict) else {}
        card = _card(txn)
        auth = data.get("authCode") if isinstance(data.get("authCode"), str) else card["auth_code"]
        return {
            "id": data.get("id"), "payment_id": _id(data.get("payment")), "order_id": _id(data.get("orderRef")),
            "status": data.get("status"), "voided": bool(data.get("voided")), **_times(data, info),
            "currency": _currency(info, txn.get("currency")),
            "amount_minor": _cents(data.get("amount")), "tax_minor": _cents(data.get("taxAmount")),
            "tip_minor": _cents(data.get("tipAmount")),
            "reason": data.get("reason") if isinstance(data.get("reason"), str) else None,
            "card_brand": card["card_brand"], "card_last4": card["card_last4"], "auth_code": auth,
        }


class Order(BaseModel):
    """One Clover order: a ticket, paid by one payment or split across several."""

    id: str
    title: str | None = Field(None, description="The order's name on the ticket, when it has one")
    state: str | None = Field(None, description="open or locked; Clover leaves an abandoned cart without one")
    payment_state: str | None = Field(
        None, description="OPEN, PAID, PARTIALLY_PAID, REFUNDED, PARTIALLY_REFUNDED or CREDITED")
    created_at: datetime | None = None
    created_on: date | None = Field(None, description="The day it was opened, in the merchant's timezone")
    modified_at: datetime | None = None
    currency: str | None = None
    total_minor: int | None = Field(None, description="What the ticket comes to, tax in, tip out")
    manual_transaction: bool | None = Field(None, description="Rung up as an amount, not from inventory")
    test_mode: bool | None = None
    payment_ids: list[str] = Field(default_factory=list, description="The payments recorded on it")

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict) or "createdTime" not in data:
            return data
        return {
            "id": data.get("id"), "title": data.get("title") if isinstance(data.get("title"), str) else None,
            "state": data.get("state"), "payment_state": data.get("paymentState"), **_times(data, info),
            "currency": _currency(info, data.get("currency")), "total_minor": _cents(data.get("total")),
            "manual_transaction": _flag(data, "manualTransaction"), "test_mode": _flag(data, "testMode"),
            "payment_ids": [p["id"] for p in _elements(data.get("payments")) if isinstance(p.get("id"), str)],
        }


# ── inputs ──────────────────────────────────────────────────────────

class _Windowed(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000, description="Page size; Clover's own maximum is 1000")
    cursor: str | None = Field(default=None, description="Opaque cursor from a previous page's `next_cursor`")
    start_date: datetime | None = Field(
        default=None,
        description="Created at or after this instant; the last 90 days when unset (a reconciliation's "
                    "lookback sets it). Windows longer than Clover's 90-day limit are walked, not cut.")
    end_date: datetime | None = Field(default=None, description="Created before this instant; now when unset")


class ListPaymentsIn(_Windowed):
    result: str | None = Field(
        default=None, description="Only payments with this result (SUCCESS for money taken), asked of Clover")
    card_only: bool = Field(
        default=False, description="Only credit and debit card payments: what a card processor's export holds")


class ListRefundsIn(_Windowed):
    voided: bool | None = Field(default=None, description="false: leave out refunds that were voided")


class ListOrdersIn(_Windowed):
    state: str | None = Field(default=None, description="open or locked")
    skip_empty: bool = Field(default=False, description="Leave out orders with no total (an empty ticket)")


class PaymentPage(CursorPage[Payment]):
    pass


class RefundPage(CursorPage[Refund]):
    pass


class OrderPage(CursorPage[Order]):
    pass
