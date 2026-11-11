"""Business days for settlement lag (§3.2, E2).

Money between a processor and a bank moves on bank business days: a batch
closed on Friday is deposited Monday (T+1) or Tuesday (T+2), and a Monday
holiday pushes both a day further. A symmetric calendar window gets this
wrong both ways: too short across a long weekend, and too permissive in
the wrong direction, since a deposit can never precede the batch it pays.

The calendar is the Federal Reserve's, which US banks settle on. Computed,
not tabulated, so it has no end date and no dependency. The Fed's rules:
a holiday on Sunday is observed on Monday; a holiday on Saturday is not
moved to Friday (banks are open that Friday).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

_ISO_DATE = 10  # len("2026-11-06")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Mon=0) of a month; n=-1 for the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date | None:
    """Sunday moves to Monday; Saturday is not observed on a weekday."""
    if day.weekday() == 6:
        return day + timedelta(days=1)
    if day.weekday() == 5:
        return None
    return day


@lru_cache(maxsize=64)
def us_bank_holidays(year: int) -> frozenset[date]:
    """The Federal Reserve's holidays, as observed, for one year."""
    fixed = [date(year, 1, 1), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    if year >= 2022:  # Juneteenth, first observed by the Fed in 2022
        fixed.append(date(year, 6, 19))
    days = {d for d in (_observed(f) for f in fixed) if d is not None}
    days |= {
        _nth_weekday(year, 1, 0, 3),   # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),   # Washington's Birthday
        _nth_weekday(year, 5, 0, -1),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),   # Labor Day
        _nth_weekday(year, 10, 0, 2),  # Columbus Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
    }
    return frozenset(days)


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in us_bank_holidays(day.year)


def business_days_after(start: date, end: date, at_most: int | None = None) -> int | None:
    """Business days in (start, end]: 0 on the same day, 1 for T+1.

    None when `end` precedes `start`, because settlement runs one way. A
    batch closed on a non-business day still counts from that day, so a
    Saturday batch deposited Monday is T+1.

    With `at_most`, counting stops one past it: the answer is then only
    "more than `at_most`", and the walk is a few days long however far
    apart the two dates are. The dates are a record's own, so without it a
    record dated a thousand years off costs a third of a million steps.
    """
    if end < start:
        return None
    count, day = 0, start
    while day < end:
        day += timedelta(days=1)
        count += is_business_day(day)
        if at_most is not None and count > at_most:
            break
    return count


def as_date(value: Any, timezone: str) -> date | None:
    """The calendar day a record happened on, in the merchant's timezone.

    A bare date ("2026-11-06", as settlement files and bank statements
    write them) is that day, never shifted by a timezone. A timestamp is
    converted to `timezone` first, so a 9 p.m. Pacific sale is not booked
    to the next day just because it is past midnight in UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.astimezone(ZoneInfo(timezone)).date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC).astimezone(ZoneInfo(timezone)).date()
    text = str(value).strip()
    try:
        if len(text) == _ISO_DATE:
            return date.fromisoformat(text)
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return as_date(moment, timezone)
