"""Gusto records as Okwan reads them. Money is minor units (cents), from
Gusto's decimal strings through `money_to_minor`, never a float."""
from __future__ import annotations

import datetime as dt
from typing import Any

from okwan_core.currency import money_to_minor
from okwan_core.pagination import CursorPage
from pydantic import BaseModel, Field, computed_field


def _day(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


class Payroll(BaseModel):
    """One processed payroll: when it paid, the period it covered, and its
    totals. Gusto's list carries no employee names; neither does this."""

    payroll_uuid: str
    company_uuid: str | None = None
    check_date: dt.date | None = Field(None, description="The day employees were paid")
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    processed: bool = False
    processed_date: dt.date | None = None
    off_cycle: bool = False
    currency: str = "USD"
    gross_pay_minor: int | None = None
    net_pay_minor: int | None = None
    cash_tips_minor: int | None = Field(None, description="Tips employees took in cash, declared on this payroll")
    paycheck_tips_minor: int | None = Field(None, description="Tips paid out through this payroll's paychecks")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tips_minor(self) -> int | None:
        """Every tip this payroll accounts for: cash and paycheck tips."""
        parts = [v for v in (self.cash_tips_minor, self.paycheck_tips_minor) if v is not None]
        return sum(parts) if parts else None

    @classmethod
    def from_gusto(cls, raw: dict[str, Any]) -> Payroll:
        totals = raw.get("totals") or {}
        period = raw.get("pay_period") or {}

        def minor(key: str) -> int | None:
            return money_to_minor(totals.get(key), "USD")

        return cls(
            payroll_uuid=str(raw.get("payroll_uuid") or raw.get("uuid") or ""),
            company_uuid=raw.get("company_uuid"),
            check_date=_day(raw.get("check_date")),
            period_start=_day(period.get("start_date")),
            period_end=_day(period.get("end_date")),
            processed=bool(raw.get("processed")),
            processed_date=_day(raw.get("processed_date")),
            off_cycle=bool(raw.get("off_cycle")),
            gross_pay_minor=minor("gross_pay"),
            net_pay_minor=minor("net_pay"),
            cash_tips_minor=minor("employee_cash_tips"),
            paycheck_tips_minor=minor("employee_paycheck_tips"),
        )


class ListPayrollsIn(BaseModel):
    limit: int = Field(default=25, ge=1, le=100, description="Page size; Gusto's own maximum is 100")
    cursor: str | None = Field(default=None, description="Opaque cursor from a previous page's `next_cursor`")
    start_date: dt.date | None = Field(
        default=None, description="Pay periods ending on or after this day; the last 90 days when unset "
                                  "(a reconciliation's lookback sets it)")
    end_date: dt.date | None = Field(default=None, description="Pay periods up to this day; today when unset. "
                                                                "Gusto reads at most a year at a time")
    off_cycle: bool = Field(default=True, description="Include off-cycle payrolls (bonuses, corrections) with regular ones")


class PayrollPage(CursorPage[Payroll]):
    pass
