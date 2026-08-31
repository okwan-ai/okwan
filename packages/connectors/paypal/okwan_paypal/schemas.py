"""PayPal connector schemas — canonical read-path models.

PayPal quotes money as a decimal string with a separate currency code,
so amounts convert through `Decimal` and are stored as integer minor
units like every other rail. Each amount carries an `*_major` computed
field.

The Transaction Search response nests fields under `transaction_info`,
`payer_info` and `cart_info`; a before-validator flattens that into one
row so the SQL catalog gets flat, typed columns.
"""
from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from okwan_core.currency import minor_unit_factor, to_major
from okwan_core.pagination import CursorPage, CursorPageIn
from pydantic import BaseModel, Field, computed_field, model_validator

#: Event codes for money moving *in* from a customer. A capture is
#: T0006; T0001 is a mass-payout send and T1900 a balance adjustment,
#: and neither is a sale. The prefix alone cannot separate them, since
#: T0001 and T0006 share a family — direction settles it, so a payment
#: is a T0-family code carrying a positive amount.
PAYMENT_EVENT_PREFIXES = ("T00", "T01", "T03", "T04", "T05", "T07")


def money_to_minor(value: str | None, currency: str | None) -> int | None:
    """Decimal string -> integer minor units.

    Never routes through float: `299.10 * 100` is 29909.999... in binary
    floating point, and money that rounds the wrong way is the bug class
    reconciliation exists to catch.
    """
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    scaled = amount * minor_unit_factor(currency)
    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))


class Transaction(BaseModel):
    """One row of the PayPal transaction ledger."""

    transaction_id: str = Field(
        description=(
            "PayPal transaction id. Not unique across balance-affecting and "
            "non-balance-affecting rows; list operations request "
            "balance-affecting rows only so this reads as a key."
        )
    )
    event_code: str | None = Field(
        default=None, description="Five-digit PayPal transaction event code"
    )
    status: str | None = Field(
        default=None, description="S success, P pending, V reversed, D denied"
    )
    currency: str | None = None
    amount_minor: int | None = Field(
        default=None, description="Gross amount in minor units; negative for outflows"
    )
    fee_minor: int | None = Field(
        default=None,
        description="PayPal fee in minor units, negative as PayPal reports it",
    )
    initiated_at: datetime | None = None
    updated_at: datetime | None = None
    invoice_id: str | None = Field(
        default=None, description="Merchant invoice id; a join key for reconciliation"
    )
    custom_field: str | None = Field(
        default=None,
        description="Merchant-supplied passthrough; the other reconciliation join key",
    )
    reference_id: str | None = Field(
        default=None, description="Id of a related pre-existing transaction"
    )
    subject: str | None = None
    note: str | None = None
    payer_email: str | None = None
    payer_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "transaction_info" not in data:
            return data
        info = data.get("transaction_info") or {}
        payer = data.get("payer_info") or {}
        amount = info.get("transaction_amount") or {}
        fee = info.get("fee_amount") or {}
        currency = amount.get("currency_code")
        name = payer.get("payer_name") or {}
        return {
            "transaction_id": info.get("transaction_id"),
            "event_code": info.get("transaction_event_code"),
            "status": info.get("transaction_status"),
            "currency": currency,
            "amount_minor": money_to_minor(amount.get("value"), currency),
            "fee_minor": money_to_minor(
                fee.get("value"), fee.get("currency_code") or currency
            ),
            "initiated_at": info.get("transaction_initiation_date"),
            "updated_at": info.get("transaction_updated_date"),
            "invoice_id": info.get("invoice_id"),
            "custom_field": info.get("custom_field"),
            "reference_id": info.get("paypal_reference_id"),
            "subject": info.get("transaction_subject"),
            "note": info.get("transaction_note"),
            "payer_email": payer.get("email_address"),
            "payer_name": name.get("alternate_full_name")
            or " ".join(
                p for p in (name.get("given_name"), name.get("surname")) if p
            )
            or None,
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amount_major(self) -> float | None:
        if self.amount_minor is None:
            return None
        return to_major(self.amount_minor, self.currency)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_minor(self) -> int | None:
        """Amount after PayPal's fee. The figure a payout settles to."""
        if self.amount_minor is None:
            return None
        return self.amount_minor + (self.fee_minor or 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_major(self) -> float | None:
        net = self.net_minor
        return None if net is None else to_major(net, self.currency)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_payment(self) -> bool:
        """True for customer payment movement, false for adjustments and
        transfers. Sandbox seed funding is T1900 and would otherwise read
        as an unmatched break forever."""
        code = self.event_code or ""
        if not code.startswith(PAYMENT_EVENT_PREFIXES):
            return False
        # Same code family covers money in and money out. A payout is a
        # T0001 with a negative amount and would otherwise read as a sale.
        return (self.amount_minor or 0) > 0


class ListTransactionsIn(CursorPageIn):
    """Window is required upstream and capped at 31 days; omitting it
    defaults to the last 30 days ending at PayPal's last refresh."""

    start_date: datetime | None = Field(
        default=None, description="Window start, UTC. Defaults to 30 days ago."
    )
    end_date: datetime | None = Field(
        default=None,
        description=(
            "Window end, UTC. Clamped to PayPal's last_refreshed_datetime, "
            "which lags real time by up to three hours."
        ),
    )
    status: str | None = Field(
        default=None, description="Filter by transaction status: S, P, V or D"
    )


class TransactionPage(CursorPage[Transaction]):
    pass
