"""Stripe connector schemas — canonical read-path models.

Amounts are integer minor units (cents) exactly as Stripe returns
them; currency conversion is presentation-layer concern.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from okwan_core.pagination import CursorPage, CursorPageIn


class Customer(BaseModel):
    id: str
    name: str | None = None
    email: str | None = None
    created: int = Field(description="Unix timestamp")
    currency: str | None = None
    delinquent: bool | None = None


class Charge(BaseModel):
    id: str
    amount: int = Field(description="Amount in minor units (cents)")
    currency: str
    status: str = Field(description="succeeded, pending, or failed")
    customer: str | None = Field(default=None, description="Customer ID")
    description: str | None = None
    created: int
    refunded: bool = False


class Subscription(BaseModel):
    id: str
    customer: str
    status: str = Field(
        description="active, trialing, past_due, canceled, unpaid..."
    )
    current_period_end: int | None = None
    cancel_at_period_end: bool = False


class Balance(BaseModel):
    available: list[dict] = Field(description="Available funds per currency")
    pending: list[dict] = Field(description="Pending funds per currency")


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
