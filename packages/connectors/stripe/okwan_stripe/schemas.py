"""Stripe connector schemas — canonical read-path models.

Amounts are integer minor units (cents) exactly as Stripe returns
them; currency conversion is presentation-layer concern.
"""
from __future__ import annotations

from typing import Any

from okwan_core.currency import to_major
from okwan_core.pagination import CursorPage, CursorPageIn
from pydantic import BaseModel, Field, computed_field, model_validator


class Customer(BaseModel):
    id: str
    name: str | None = None
    email: str | None = None
    created: int = Field(description="Unix timestamp")
    currency: str | None = None
    delinquent: bool | None = None


class Charge(BaseModel):
    id: str
    amount: int = Field(description="Amount in minor units")
    currency: str
    status: str = Field(description="succeeded, pending, or failed")
    customer: str | None = Field(default=None, description="Customer ID")
    description: str | None = None
    created: int
    refunded: bool = False
    amount_refunded: int = Field(
        default=0, description="Amount refunded in minor units"
    )
    order_ref: str | None = Field(
        default=None,
        description=(
            "Merchant order reference from charge metadata. The join key for "
            "reconciling against an order ledger; Shopify Payments writes the "
            "order name here."
        ),
    )
    fee_minor: int | None = Field(
        default=None,
        description=(
            "Stripe's fee in minor units, positive as Stripe reports it. Lives "
            "on the balance transaction, not the charge, so the list operation "
            "expands it."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _lift_nested(cls, data: Any) -> Any:
        """Pull the fee off the expanded balance transaction and the order
        reference out of metadata, so both become flat SQL columns."""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        bt = data.get("balance_transaction")
        if isinstance(bt, dict):
            out["fee_minor"] = bt.get("fee")
        meta = data.get("metadata") or {}
        if isinstance(meta, dict):
            out["order_ref"] = (
                meta.get("order_id") or meta.get("order") or meta.get("order_name")
            )
        return out

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amount_major(self) -> float:
        """Amount in major units, subunit-correct for zero-decimal currencies."""
        return to_major(self.amount, self.currency)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_minor(self) -> int:
        """What the merchant receives: amount less refunds less Stripe's fee.
        Stripe reports the fee positive, so this subtracts where PayPal adds."""
        return self.amount - self.amount_refunded - (self.fee_minor or 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_major(self) -> float:
        return to_major(self.net_minor, self.currency)


class Subscription(BaseModel):
    id: str
    customer: str
    status: str = Field(
        description="active, trialing, past_due, canceled, unpaid..."
    )
    current_period_end: int | None = None
    cancel_at_period_end: bool = False


class BalanceEntry(BaseModel):
    currency: str
    amount: int = Field(description="Amount in minor units")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amount_major(self) -> float:
        return to_major(self.amount, self.currency)


class Balance(BaseModel):
    available: list[BalanceEntry] = Field(description="Available funds per currency")
    pending: list[BalanceEntry] = Field(description="Pending funds per currency")


# ── operation inputs ────────────────────────────────────────────────

class ListCustomersIn(CursorPageIn):
    email: str | None = Field(default=None, description="Filter by exact email")


class GetCustomerIn(BaseModel):
    customer_id: str = Field(description="Stripe customer ID (cus_...)")


class ListChargesIn(CursorPageIn):
    customer_id: str | None = Field(
        default=None, description="Only charges for this customer"
    )


class ListSubscriptionsIn(CursorPageIn):
    status: str = Field(
        default="all",
        description="Filter: active, canceled, trialing, past_due, all...",
    )


class GetBalanceIn(BaseModel):
    """Balance requires no parameters."""


CustomerPage = CursorPage[Customer]
ChargePage = CursorPage[Charge]
SubscriptionPage = CursorPage[Subscription]
