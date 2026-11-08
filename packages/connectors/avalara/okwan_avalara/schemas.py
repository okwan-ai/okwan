"""Avalara connector schemas: flat, typed rows for REST, SQL and MCP.

AvaTax states money as JSON numbers in major units (12.5 for $12.50), so
every amount converts through `okwan_core.currency.money_to_minor`, which
reads a number through its shortest text form and never through float
arithmetic. A before-validator flattens one AvaTax transaction into one
row; validating a row that is already flat is a no-op.

**What stays out.** An AvaTax transaction can carry the customer's email
address, business identification number and exemption certificate number,
its lines and its address ids. None of it is needed to reconcile tax, so a
row keeps the document, its dates and status, the state it was taxed in,
and its totals.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from okwan_core.currency import money_to_minor
from okwan_core.pagination import CursorPage
from pydantic import BaseModel, Field, computed_field, model_validator

#: The status of a transaction that counts on a tax return.
COMMITTED = "Committed"


def _minor(value: Any, currency: str | None) -> int | None:
    return money_to_minor(value, currency or "USD")


class Company(BaseModel):
    id: str
    code: str
    name: str | None = None
    country: str | None = None
    is_default: bool | None = None
    is_active: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "companyCode" not in data:
            return data
        return {"id": str(data.get("id")), "code": data.get("companyCode"), "name": data.get("name"),
                "country": data.get("defaultCountry"), "is_default": data.get("isDefault"),
                "is_active": data.get("isActive")}


class Transaction(BaseModel):
    """One AvaTax document: a sale, a return, an inventory transfer."""

    id: str
    code: str | None = Field(None, description="The document code the selling system gave it: the join key")
    type: str | None = Field(None, description="SalesInvoice, ReturnInvoice, SalesOrder…")
    status: str | None = Field(
        None, description="Committed counts on a return; Saved was calculated but never committed")
    date: dt.date | None = Field(None, description="The document's date")
    tax_date: dt.date | None = Field(None, description="The date tax was calculated as of")
    region: str | None = Field(None, description="The state (or province) it was taxed in")
    country: str | None = None
    currency: str | None = None
    total_amount_minor: int | None = Field(None, description="The sale before tax")
    total_taxable_minor: int | None = None
    total_exempt_minor: int | None = None
    total_tax_minor: int | None = Field(None, description="The tax on the document")
    tax_calculated_minor: int | None = Field(
        None, description="The tax AvaTax calculated; differs from the tax when it was overridden")
    locked: bool | None = Field(None, description="Locked once it was reported on a filed return")
    location_code: str | None = None
    modified_at: dt.datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "totalTax" not in data and "companyId" not in data:
            return data
        cur = data.get("currencyCode")
        return {
            "id": str(data.get("id")), "code": data.get("code"), "type": data.get("type"),
            "status": data.get("status"), "date": data.get("date"), "tax_date": data.get("taxDate"),
            "region": data.get("region") or None, "country": data.get("country") or None, "currency": cur,
            "total_amount_minor": _minor(data.get("totalAmount"), cur),
            "total_taxable_minor": _minor(data.get("totalTaxable"), cur),
            "total_exempt_minor": _minor(data.get("totalExempt"), cur),
            "total_tax_minor": _minor(data.get("totalTax"), cur),
            "tax_calculated_minor": _minor(data.get("totalTaxCalculated"), cur),
            "locked": data.get("locked") if isinstance(data.get("locked"), bool) else None,
            "location_code": data.get("locationCode") or None, "modified_at": data.get("modifiedDate"),
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def committed(self) -> bool:
        """It counts on a tax return."""
        return self.status == COMMITTED

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tax_override_minor(self) -> int | None:
        """How far the tax charged differs from what AvaTax calculated: an override."""
        if self.total_tax_minor is None or self.tax_calculated_minor is None:
            return None
        return self.total_tax_minor - self.tax_calculated_minor


# ── inputs ──────────────────────────────────────────────────────────

class ListCompaniesIn(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000, description="Page size")


class ListTransactionsIn(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000, description="Page size; AvaTax's own maximum is 1000")
    cursor: str | None = Field(default=None, description="Opaque cursor from a previous page's `next_cursor`")
    start_date: dt.datetime | None = Field(
        default=None, description="Documents dated on or after this day; the last 90 days when unset "
                                  "(a reconciliation's lookback sets it)")
    end_date: dt.datetime | None = Field(default=None, description="Documents dated on or before this day; today when unset")
    status: str | None = Field(default=None, description="Committed (on a return), Saved, Cancelled…")
    type: str | None = Field(
        default="SalesInvoice",
        description="SalesInvoice by default; ReturnInvoice for refunds; several comma-separated; empty for all")


class CompanyPage(CursorPage[Company]):
    pass


class TransactionPage(CursorPage[Transaction]):
    pass
