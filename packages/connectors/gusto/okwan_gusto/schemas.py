"""Gusto records as Okwan reads them. Money is minor units (cents), from
Gusto's decimal strings through `money_to_minor`, never a float.

Per employee, Okwan reads tips and nothing else: Gusto's reference for the
employee and the two tip compensations, never a name, wage, rate, gross or
net pay, deduction or tax."""
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


#: Gusto's names for the two tip compensations ("the unique, immutable
#: identifier for this compensation"), matched without regard to case.
CASH_TIPS = "cash tips"
PAYCHECK_TIPS = "paycheck tips"


def _tip_sum(compensations: list[dict[str, Any]], name: str) -> int | None:
    """One tip compensation, summed over the employee's jobs; None when
    Gusto lists none of that name (the employee is not paid tips)."""
    amounts = [money_to_minor(c.get("amount"), "USD") for c in compensations
               if str(c.get("name", "")).strip().lower() == name]
    known = [a for a in amounts if a is not None]
    return sum(known) if known else None


class PayrollTip(BaseModel):
    """One employee's tips on one processed payroll, against the pay period
    the point of sale's tips are summed into. The employee is Gusto's uuid
    only: no name, pay or tax is read."""

    payroll_uuid: str
    employee_uuid: str
    check_date: dt.date | None = Field(None, description="The day the payroll paid")
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    off_cycle: bool = False
    currency: str = "USD"
    cash_tips_minor: int | None = Field(None, description="Cash tips the employee declared on this payroll")
    paycheck_tips_minor: int | None = Field(None, description="Tips paid to the employee through this paycheck")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tips_minor(self) -> int | None:
        """Every tip this payroll accounts for, for this employee."""
        parts = [v for v in (self.cash_tips_minor, self.paycheck_tips_minor) if v is not None]
        return sum(parts) if parts else None

    @classmethod
    def rows_from_gusto(cls, payroll: dict[str, Any]) -> list[PayrollTip]:
        """The tip rows of one payroll's detail: an employee left out of the
        payroll, or not paid tips at all, has none."""
        period = payroll.get("pay_period") or {}
        rows = []
        for comp in payroll.get("employee_compensations") or []:
            if comp.get("excluded") or not comp.get("employee_uuid"):
                continue
            fixed = comp.get("fixed_compensations") or []
            cash, paycheck = _tip_sum(fixed, CASH_TIPS), _tip_sum(fixed, PAYCHECK_TIPS)
            if cash is None and paycheck is None:
                continue
            rows.append(cls(
                payroll_uuid=str(payroll.get("payroll_uuid") or payroll.get("uuid") or ""),
                employee_uuid=str(comp["employee_uuid"]),
                check_date=_day(payroll.get("check_date")),
                period_start=_day(period.get("start_date")),
                period_end=_day(period.get("end_date")),
                off_cycle=bool(payroll.get("off_cycle")),
                cash_tips_minor=cash,
                paycheck_tips_minor=paycheck,
            ))
        return rows


class ListPayrollTipsIn(BaseModel):
    limit: int = Field(default=25, ge=1, le=100, description="Employee rows per page")
    cursor: str | None = Field(default=None, description="Opaque cursor from a previous page's `next_cursor`")
    start_date: dt.date | None = Field(
        default=None, description="Pay periods ending on or after this day; the last 90 days when unset")
    end_date: dt.date | None = Field(default=None, description="Pay periods up to this day; today when unset. "
                                                                "Gusto reads at most a year at a time")
    off_cycle: bool = Field(default=True, description="Include off-cycle payrolls with regular ones")


class PayrollTipPage(CursorPage[PayrollTip]):
    pass
