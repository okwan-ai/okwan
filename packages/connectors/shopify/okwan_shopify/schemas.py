"""Shopify connector schemas — canonical read-path models.

Shopify quotes money as decimal strings in major units, unlike the
payment rails which use integer minor units. Each amount is normalised
to both: `*_minor` for arithmetic and cross-rail comparison, `*_major`
for display. Reconciliation compares minor units so a Shopify order and
a Paystack transaction are the same kind of number.

An order carries four different money figures and they diverge the
moment a refund exists. All four are exposed rather than collapsed, so
a reconciliation declaration states which one it matches on.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, computed_field

from okwan_core.currency import minor_unit_factor, to_major
from okwan_core.pagination import CursorPage, CursorPageIn


def money_to_minor(amount: Any, currency: str | None) -> int:
    """Decimal-string major amount -> integer minor units.

    Decimal, not float: 299.10 * 100 is 29909.999... in binary floating
    point, and money that rounds the wrong way is the whole bug class
    reconciliation exists to catch.
    """
    if amount is None:
        return 0
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return 0
    return int(value * minor_unit_factor(currency))


class Order(BaseModel):
    id: str = Field(description="Shopify GID, e.g. gid://shopify/Order/123")
    name: str = Field(
        description="Merchant-facing order number (#1001); the reconciliation join key"
    )
    currency: str
    created_at: datetime | None = None
    financial_status: str | None = Field(
        default=None, description="PAID, PARTIALLY_REFUNDED, REFUNDED, PENDING..."
    )
    fulfillment_status: str | None = Field(
        default=None,
        description="Shipping state. Independent of payment — an unfulfilled "
        "order can be fully reconciled, and a fulfilled one can be unpaid.",
    )

    total_price_minor: int = Field(
        description="Current order value in minor units, after any edits"
    )
    total_received_minor: int = Field(
        description="Gross amount collected, before refunds"
    )
    total_refunded_minor: int = Field(default=0, description="Refunded, minor units")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_payment_minor(self) -> int:
        """Received less refunded — what the merchant actually kept.

        The default figure to reconcile against: it is what a rail's
        settlement will eventually reflect. Matching gross instead makes
        a legitimately refunded order look like an over-collection.
        """
        return self.total_received_minor - self.total_refunded_minor

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_payment_major(self) -> float:
        return to_major(self.net_payment_minor, self.currency)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_price_major(self) -> float:
        return to_major(self.total_price_minor, self.currency)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_reconcilable(self) -> bool:
        """False when nothing was collected — nothing for a rail to match."""
        return self.total_received_minor > 0


class Product(BaseModel):
    id: str
    title: str
    handle: str | None = None
    status: str | None = Field(default=None, description="ACTIVE, ARCHIVED, DRAFT")
    total_inventory: int | None = None
    created_at: datetime | None = None


# ── operation inputs ────────────────────────────────────────────────

class ListOrdersIn(CursorPageIn):
    financial_status: str | None = Field(
        default=None,
        description="Filter by payment state: paid, partially_refunded, refunded, pending",
    )
    created_at_min: datetime | None = Field(
        default=None, description="Only orders created at or after this time"
    )


class GetOrderIn(BaseModel):
    order_id: str = Field(
        description="Shopify order GID (gid://shopify/Order/123) or bare numeric ID"
    )


class ListProductsIn(CursorPageIn):
    pass


OrderPage = CursorPage[Order]
ProductPage = CursorPage[Product]
