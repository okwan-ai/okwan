"""Square connector schemas: flat, typed rows for REST, SQL and MCP.

Square quotes money as `{"amount": <integer>, "currency": "USD"}` in the
currency's smallest unit, so amounts are already integer minor units and
are kept as Square sends them; payouts write `currency_code` where every
other object writes `currency`, and both are read. A before-validator on
each model flattens Square's nesting into one row.

**Card data.** A Square payment carries the card's first six digits
(`bin`), its expiry and a fingerprint as well as the last four. None of
that is a card number, but none of it is needed to reconcile either, so
the rows keep the brand and the last four digits only, and nothing else
from `card_details` or an order's tenders ever leaves the validator.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from okwan_core.pagination import CursorPage
from pydantic import BaseModel, Field, computed_field, model_validator


def _money(obj: Any) -> tuple[int | None, str | None]:
    """(amount in minor units, ISO currency) from a Square Money object."""
    if not isinstance(obj, dict):
        return None, None
    amount = obj.get("amount")
    currency = obj.get("currency") or obj.get("currency_code")
    if isinstance(amount, bool) or not isinstance(amount, int):
        return None, currency
    return amount, currency


def _amount(obj: Any) -> int | None:
    return _money(obj)[0]


def _sum(items: Any, key: str = "amount_money") -> int | None:
    """The total of a list of fee objects, or None when there are none."""
    if not isinstance(items, list) or not items:
        return None
    amounts = [_amount(i.get(key)) for i in items if isinstance(i, dict)]
    amounts = [a for a in amounts if a is not None]
    return sum(amounts) if amounts else None


def _card(details: Any) -> dict[str, str | None]:
    card = (details or {}).get("card") if isinstance(details, dict) else None
    card = card if isinstance(card, dict) else {}
    last4 = card.get("last_4")
    return {"card_brand": card.get("card_brand"),
            "card_last4": last4 if isinstance(last4, str) and last4.isdigit() and len(last4) == 4 else None}


class Location(BaseModel):
    id: str
    name: str | None = None
    status: str | None = None
    currency: str | None = None
    country: str | None = None
    timezone: str | None = None


class Payment(BaseModel):
    """One Square payment: a card, cash or wallet payment on an order."""

    id: str
    status: str | None = Field(None, description="APPROVED, PENDING, COMPLETED, CANCELED or FAILED")
    created_at: datetime | None = None
    updated_at: datetime | None = None
    location_id: str | None = None
    order_id: str | None = None
    reference_id: str | None = Field(None, description="The seller's own reference; a join key")
    receipt_number: str | None = None
    source_type: str | None = Field(None, description="CARD, CASH, WALLET, BANK_ACCOUNT, EXTERNAL…")
    currency: str | None = None
    amount_minor: int | None = Field(None, description="Before the tip")
    tip_minor: int | None = None
    total_minor: int | None = Field(None, description="Amount plus tip: what the buyer paid")
    refunded_minor: int = Field(0, description="Refunded so far; Square omits it when nothing was refunded")
    fee_minor: int | None = Field(
        None, description="Square's processing fees, a positive amount; None until Square has posted them")
    app_fee_minor: int | None = Field(None, description="A platform's application fee, when one was taken")
    card_brand: str | None = None
    card_last4: str | None = Field(None, description="The last four digits only")

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "amount_money" not in data and "total_money" not in data:
            return data
        amount, currency = _money(data.get("amount_money"))
        total, total_currency = _money(data.get("total_money"))
        return {
            "id": data.get("id"), "status": data.get("status"),
            "created_at": data.get("created_at"), "updated_at": data.get("updated_at"),
            "location_id": data.get("location_id"), "order_id": data.get("order_id"),
            "reference_id": data.get("reference_id"), "receipt_number": data.get("receipt_number"),
            "source_type": data.get("source_type"), "currency": currency or total_currency,
            "amount_minor": amount, "tip_minor": _amount(data.get("tip_money")), "total_minor": total,
            "refunded_minor": _amount(data.get("refunded_money")) or 0,
            "fee_minor": _sum(data.get("processing_fee")),
            "app_fee_minor": _amount(data.get("app_fee_money")),
            **_card(data.get("card_details")),
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_minor(self) -> int | None:
        """What the seller keeps: the total less Square's fees, once posted."""
        if self.total_minor is None or self.fee_minor is None:
            return None
        return self.total_minor - self.fee_minor


class Refund(BaseModel):
    id: str
    payment_id: str | None = None
    order_id: str | None = None
    location_id: str | None = None
    status: str | None = Field(None, description="PENDING, COMPLETED, REJECTED or FAILED")
    created_at: datetime | None = None
    updated_at: datetime | None = None
    currency: str | None = None
    amount_minor: int | None = Field(None, description="Refunded to the buyer, a positive amount")
    fee_minor: int | None = Field(None, description="Square's fee on the refund, as Square reports it")
    reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "amount_money" not in data:
            return data
        amount, currency = _money(data.get("amount_money"))
        return {
            "id": data.get("id"), "payment_id": data.get("payment_id"), "order_id": data.get("order_id"),
            "location_id": data.get("location_id"), "status": data.get("status"),
            "created_at": data.get("created_at"), "updated_at": data.get("updated_at"),
            "currency": currency, "amount_minor": amount,
            "fee_minor": _sum(data.get("processing_fee")), "reason": data.get("reason"),
        }


class Payout(BaseModel):
    """One transfer from Square to the seller's bank account or card."""

    id: str
    status: str | None = Field(None, description="SENT, FAILED or PAID")
    location_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    arrival_date: date | None = Field(None, description="The day the money reaches the destination")
    type: str | None = Field(None, description="BATCH or SIMPLE")
    destination_type: str | None = Field(None, description="BANK_ACCOUNT, CARD…")
    currency: str | None = None
    amount_minor: int | None = Field(None, description="What lands in the account")
    payout_fee_minor: int | None = Field(None, description="Fees on the transfer itself (an instant deposit)")
    number_of_entries: int | None = None
    end_to_end_id: str | None = Field(None, description="The bank's own trace for the transfer")

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "amount_money" not in data:
            return data
        amount, currency = _money(data.get("amount_money"))
        dest = data.get("destination") if isinstance(data.get("destination"), dict) else {}
        return {
            "id": data.get("id"), "status": data.get("status"), "location_id": data.get("location_id"),
            "created_at": data.get("created_at"), "updated_at": data.get("updated_at"),
            "arrival_date": data.get("arrival_date"), "type": data.get("type"),
            "destination_type": dest.get("type"), "currency": currency, "amount_minor": amount,
            "payout_fee_minor": _sum(data.get("payout_fee")),
            "number_of_entries": data.get("number_of_entries"), "end_to_end_id": data.get("end_to_end_id"),
        }


class PayoutEntry(BaseModel):
    """One line of a payout: a charge, a refund, a fee or an adjustment."""

    id: str
    payout_id: str | None = None
    effective_at: datetime | None = None
    type: str | None = Field(None, description="CHARGE, REFUND, FEE, DEPOSIT_FEE, ADJUSTMENT…")
    currency: str | None = None
    gross_minor: int | None = None
    fee_minor: int | None = Field(None, description="Square's fee on this line, a positive amount")
    net_minor: int | None = Field(None, description="What this line adds to the payout; negative for money out")
    payment_id: str | None = Field(None, description="The payment this line settles, when it settles one")
    refund_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "gross_amount_money" not in data:
            return data
        gross, currency = _money(data.get("gross_amount_money"))
        details = next((v for k, v in data.items() if k.startswith("type_") and k.endswith("_details")
                        and isinstance(v, dict)), {})
        return {
            "id": data.get("id"), "payout_id": data.get("payout_id"), "effective_at": data.get("effective_at"),
            "type": data.get("type"), "currency": currency, "gross_minor": gross,
            "fee_minor": _amount(data.get("fee_amount_money")), "net_minor": _amount(data.get("net_amount_money")),
            "payment_id": details.get("payment_id"), "refund_id": details.get("refund_id"),
        }


class Order(BaseModel):
    """One Square order: a sale, or a return pointing at the sale it reverses."""

    id: str
    reference_id: str | None = None
    location_id: str | None = None
    state: str | None = Field(None, description="OPEN, COMPLETED, CANCELED or DRAFT")
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    currency: str | None = None
    total_minor: int | None = Field(None, description="What the buyer was charged, tip and tax included")
    tax_minor: int | None = None
    tip_minor: int | None = None
    discount_minor: int | None = None
    service_charge_minor: int | None = None
    returned_minor: int | None = Field(None, description="On a return: the amount returned")
    source_order_id: str | None = Field(None, description="On a return: the sale it reverses")
    payment_ids: list[str] = Field(default_factory=list, description="The payments that tendered it")

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "state" not in data:
            return data
        total, currency = _money(data.get("total_money"))
        returns = data.get("returns") if isinstance(data.get("returns"), list) else []
        returned, return_currency = _money((data.get("return_amounts") or {}).get("total_money"))
        tenders = data.get("tenders") if isinstance(data.get("tenders"), list) else []
        return {
            "id": data.get("id"), "reference_id": data.get("reference_id"),
            "location_id": data.get("location_id"), "state": data.get("state"),
            "created_at": data.get("created_at"), "updated_at": data.get("updated_at"),
            "closed_at": data.get("closed_at"), "currency": currency or return_currency,
            "total_minor": total, "tax_minor": _amount(data.get("total_tax_money")),
            "tip_minor": _amount(data.get("total_tip_money")),
            "discount_minor": _amount(data.get("total_discount_money")),
            "service_charge_minor": _amount(data.get("total_service_charge_money")),
            "returned_minor": returned if returns else None,
            "source_order_id": next((r.get("source_order_id") for r in returns if isinstance(r, dict)), None),
            "payment_ids": [t["payment_id"] for t in tenders if isinstance(t, dict) and t.get("payment_id")],
        }


# ── inputs ──────────────────────────────────────────────────────────

class _Paged(BaseModel):
    limit: int = Field(default=100, ge=1, le=100, description="Page size; Square's own maximum is 100")
    cursor: str | None = Field(default=None, description="Opaque cursor from a previous page's `next_cursor`")
    location_id: str | None = Field(default=None, description="One location; Square's default (main) location if unset")


class _Windowed(_Paged):
    start_date: datetime | None = Field(
        default=None, description="Created at or after this instant (a reconciliation's lookback sets it)")
    end_date: datetime | None = Field(default=None, description="Created before this instant")


class ListLocationsIn(BaseModel):
    limit: int = Field(default=100, ge=1, le=100, description="Ignored: Square returns every location")


class ListPaymentsIn(_Windowed):
    status: str | None = Field(
        default=None, description="Only payments in this status (COMPLETED, say), filtered page by page")


class ListRefundsIn(_Windowed):
    status: str | None = Field(default=None, description="PENDING, COMPLETED, REJECTED or FAILED")


class ListPayoutsIn(_Windowed):
    status: str | None = Field(default=None, description="SENT, FAILED or PAID")


class ListPayoutEntriesIn(_Windowed):
    payout_id: str | None = Field(
        default=None, description="One payout's entries; without it, every payout's in the window, in turn")
    type: str | None = Field(default=None, description="Only entries of this type (CHARGE, REFUND, FEE…)")


class ListOrdersIn(_Windowed):
    limit: int = Field(default=100, ge=1, le=500, description="Page size")
    state: str | None = Field(default=None, description="OPEN, COMPLETED, CANCELED or DRAFT")


class LocationPage(CursorPage[Location]):
    pass


class PaymentPage(CursorPage[Payment]):
    pass


class RefundPage(CursorPage[Refund]):
    pass


class PayoutPage(CursorPage[Payout]):
    pass


class PayoutEntryPage(CursorPage[PayoutEntry]):
    pass


class OrderPage(CursorPage[Order]):
    pass
