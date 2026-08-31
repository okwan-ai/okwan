"""Paystack connector schemas — canonical read-path models.

Amounts are integer minor units exactly as Paystack returns them.
Each amount carries an `*_major` computed field so agents never have
to guess the subunit factor — critical for XOF, which has none.
"""
from __future__ import annotations

from datetime import datetime

from okwan_core.currency import to_major
from okwan_core.pagination import CursorPage, CursorPageIn
from pydantic import BaseModel, Field, computed_field


class TransactionCustomer(BaseModel):
    id: int | None = None
    customer_code: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = Field(
        default=None, description="Customer MSISDN as supplied to Paystack"
    )


class Transaction(BaseModel):
    id: int
    reference: str = Field(
        description="Merchant-supplied reference; the join key for reconciliation"
    )
    amount: int = Field(description="Amount in minor units")
    currency: str
    status: str = Field(description="success, failed, abandoned, reversed...")
    channel: str | None = Field(default=None, description="card, bank, mobile_money...")
    gateway_response: str | None = None
    fees: int | None = Field(default=None, description="Paystack fees in minor units")
    paid_at: datetime | None = None
    created_at: datetime | None = None
    customer: TransactionCustomer | None = None
    metadata: dict | list | str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amount_major(self) -> float:
        """Amount in major units, subunit-correct for zero-decimal currencies."""
        return to_major(self.amount, self.currency)


class Settlement(BaseModel):
    id: int
    status: str = Field(description="pending, processing, success, failed")
    currency: str
    total_amount: int = Field(description="Gross settled amount, minor units")
    effective_amount: int | None = Field(
        default=None, description="Net of fees and deductions, minor units"
    )
    total_fees: int | None = None
    total_processed: int | None = None
    settlement_date: datetime | None = None
    created_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_amount_major(self) -> float:
        return to_major(self.total_amount, self.currency)


class Customer(BaseModel):
    id: int
    customer_code: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    risk_action: str | None = None
    created_at: datetime | None = None


class BalanceEntry(BaseModel):
    currency: str
    balance: int = Field(description="Balance in minor units")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def balance_major(self) -> float:
        return to_major(self.balance, self.currency)


class Balance(BaseModel):
    balances: list[BalanceEntry] = Field(description="Available balance per currency")


# ── operation inputs ────────────────────────────────────────────────

class ListTransactionsIn(CursorPageIn):
    status: str | None = Field(
        default=None, description="Filter: success, failed, abandoned"
    )
    customer_id: int | None = Field(
        default=None, description="Only transactions for this Paystack customer ID"
    )


class GetTransactionIn(BaseModel):
    transaction_id: int = Field(description="Paystack numeric transaction ID")


class ListSettlementsIn(CursorPageIn):
    status: str | None = Field(
        default=None, description="Filter: pending, processing, success, failed"
    )


class ListSettlementTransactionsIn(CursorPageIn):
    settlement_id: int = Field(
        description="Settlement whose constituent transactions to list"
    )


class ListCustomersIn(CursorPageIn):
    pass


class GetCustomerIn(BaseModel):
    customer_code: str = Field(description="Customer code (CUS_...) or email address")


class GetBalanceIn(BaseModel):
    """Balance requires no parameters."""


TransactionPage = CursorPage[Transaction]
SettlementPage = CursorPage[Settlement]
CustomerPage = CursorPage[Customer]
