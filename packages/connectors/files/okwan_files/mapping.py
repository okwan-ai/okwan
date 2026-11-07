"""A column mapping: which column of a file fills which field.

Generic only. A mapping names, for one of the three kinds, the column
each field comes from, plus the few choices a file cannot state for
itself: a currency when there is no column for it, whether dates are
day-first, whether the decimal mark is a comma, which amounts the file
signs the other way, and which row holds the column names. There are no
processor presets: those wait for real dealer files (§9 2026-11-07).

Applying a mapping refuses a file rather than guess. A required field
that is missing or unreadable on a row stops the import with the rows
named, unless the mapping says to skip such rows, in which case each one
skipped is counted and listed. Error messages name the row, the field and
the column, never the cell's value.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from okwan_core.currency import money_to_minor
from pydantic import BaseModel, Field, field_validator

from .guard import is_card_column, last4
from .parse import OFX_COLUMNS, Sheet
from .schemas import DEDUCTIONS, DERIVED, KINDS, SPLIT_AMOUNT, fields_of

KindName = Literal["settlement_batches", "settlement_transactions", "bank_deposits"]

#: The most row errors a refusal lists.
MAX_ERRORS = 10
TEXT_LIMIT = 200
#: Larger than any real amount, small enough for a 64-bit column anywhere
#: the rows go next.
MAX_MINOR = 10**15


class Mapping(BaseModel):
    kind: KindName = Field(description="What the file holds")
    columns: dict[str, str] = Field(
        description="Field → the column it comes from, by the column's name in the file. "
        "For a signed amount split over two columns, map `credit` and `debit` instead.")
    currency: str | None = Field(
        None, description="ISO 4217 code for every row, when the file has no currency column "
        "(or as the fallback when a row's currency cell is empty)")
    day_first: bool = Field(False, description="Read 03/04/2026 as 3 April, not March 4")
    decimal_comma: bool = Field(False, description="Read 1.234,56 as one thousand two hundred…")
    negate: list[str] = Field(
        default_factory=list, description="Amount fields the file signs the other way")
    header_row: int = Field(1, ge=1, le=50, description="The row holding the column names")
    skip_invalid_rows: bool = Field(
        False, description="Skip rows whose required fields cannot be read, and list them, "
        "instead of refusing the file")
    name: str = Field("custom", min_length=1, max_length=60,
                      description="The layout's name, recorded on every row it produces")

    @field_validator("currency")
    @classmethod
    def _iso(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError("currency must be a three-letter ISO 4217 code")
        return v


class MappingError(ValueError):
    """The mapping does not fit the file, or the file does not fit it."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(slots=True)
class Normalized:
    #: (row number in the file, the row's date, canonical fields as JSON values)
    rows: list[tuple[int, date, dict]] = field(default_factory=list)
    #: Rows left out because a required field could not be read.
    skipped: list[int] = field(default_factory=list)
    #: Card columns in the file, never stored (a masked one may feed card_last4).
    card_columns: list[str] = field(default_factory=list)


def _index(header: list[str]) -> dict[str, int]:
    return {h.strip().lower(): i for i, h in enumerate(header) if h.strip()}


def check(mapping: Mapping, header: list[str]) -> dict[str, int]:
    """The column index behind each mapped field, or every problem at once."""
    specs = fields_of(mapping.kind)
    at = _index(header)
    problems: list[str] = []
    cols: dict[str, int] = {}
    allowed = set(specs) | (set(SPLIT_AMOUNT) if "amount_minor" in specs else set())
    for name, column in mapping.columns.items():
        if name not in allowed:
            problems.append(f"{mapping.kind} has no field \"{name}\"")
            continue
        j = at.get(column.strip().lower())
        if j is None:
            problems.append(f"{name}: the file has no column \"{column}\"")
            continue
        if is_card_column(header[j]) and name != "card_last4":
            problems.append(f"{name}: \"{header[j]}\" is a card number column; it can only "
                            "fill card_last4")
            continue
        cols[name] = j
    split = [s for s in SPLIT_AMOUNT if s in mapping.columns]
    if split and "amount_minor" in mapping.columns:
        problems.append("map amount_minor or credit/debit, not both")
    for name, spec in specs.items():
        if not spec.required or name in mapping.columns or name in DERIVED:
            continue
        if name == "currency" and mapping.currency:
            continue
        if name == "amount_minor" and split:
            continue
        hint = " (or set a fixed currency)" if name == "currency" else ""
        problems.append(f"{name} is required: map it to a column{hint}")
    for name in mapping.negate:
        if name not in specs or specs[name].parse != "money":
            problems.append(f"negate: \"{name}\" is not an amount field")
    if problems:
        raise MappingError(problems)
    return cols


# ── reading one value ───────────────────────────────────────────────

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def parse_date(text: str, day_first: bool = False) -> date | None:
    t = text.strip()
    if not t:
        return None
    # ISO, with or without a time; OFX's YYYYMMDD[HHMMSS[.xxx][tz]].
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T ].*)?$", t) or \
        re.match(r"^(\d{4})(\d{2})(\d{2})(?:\d{0,6}(?:\.\d+)?(?:\[.*\])?)?$", t)
    if m:
        return _date(int(m[1]), int(m[2]), int(m[3]))
    # 03/04/2026, 3-4-26, with an optional time after.
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?:[ T].*)?$", t)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y = y + 2000 if y < 100 else y
        month, day = (b, a) if day_first else (a, b)
        return _date(y, month, day)
    # 04-Nov-2026, 4 Nov 2026, Nov 4, 2026, November 4 2026.
    m = re.match(r"^(\d{1,2})[-\s]([A-Za-z]{3,9})\.?[-\s,]+(\d{2}|\d{4})$", t)
    if m and m[2][:3].lower() in _MONTHS:
        y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        return _date(y, _MONTHS[m[2][:3].lower()], int(m[1]))
    m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$", t)
    if m and m[1][:3].lower() in _MONTHS:
        return _date(int(m[3]), _MONTHS[m[1][:3].lower()], int(m[2]))
    return None


def _date(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_money(text: str, currency: str | None, decimal_comma: bool = False) -> int | None:
    """A printed amount into minor units: $1,234.56, (12.00), 12.00-, 1.234,56, 5 CR."""
    t = text.strip()
    if not t:
        return None
    negative = False
    upper = t.upper()
    if upper.endswith(("DR", "CR")):
        negative = upper.endswith("DR")
        t = t[:-2].strip()
    if t.startswith("(") and t.endswith(")"):
        negative, t = True, t[1:-1]
    if t.endswith("-"):
        negative, t = not negative, t[:-1]
    t = re.sub(r"[^\d.,\-+eE]", "", t)
    if t.startswith("-"):
        negative, t = not negative, t[1:]
    elif t.startswith("+"):
        t = t[1:]
    if decimal_comma:
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+", t):
        return None
    minor = money_to_minor(t, currency)
    if minor is None or minor > MAX_MINOR:
        return None
    return -minor if negative else minor


def _int(text: str) -> int | None:
    t = re.sub(r"[,\s]", "", text)
    if re.fullmatch(r"-?\d+(?:\.0+)?", t):
        return int(t.split(".")[0])
    return None


# ── applying a mapping ──────────────────────────────────────────────

def apply(mapping: Mapping, sheet: Sheet) -> Normalized:
    """Every row of the sheet as canonical fields, or a refusal."""
    cols = check(mapping, sheet.header)
    specs = fields_of(mapping.kind)
    date_field = KINDS[mapping.kind].date_field
    out = Normalized(card_columns=[h for h in sheet.header if h and is_card_column(h)])
    errors: list[str] = []
    bad_rows = 0
    seen_fitids: dict[str, int] = {}
    for i, cells in enumerate(sheet.rows):
        if not any(cells[j] for j in cols.values()):
            continue
        row, problems = _row(mapping, specs, cols, cells)
        if not problems and mapping.kind == "bank_deposits" and not row.get("fitid"):
            row["fitid"] = _fitid(row, seen_fitids)
        if not problems and "net_minor" in specs and "net_minor" not in cols:
            row["net_minor"] = _net(row)
        if problems:
            bad_rows += 1
            if mapping.skip_invalid_rows:
                out.skipped.append(_number(sheet, i))
            elif len(errors) < MAX_ERRORS:
                errors.append(f"{sheet.where(i)}: {'; '.join(problems)}")
            continue
        out.rows.append((_number(sheet, i), date.fromisoformat(row[date_field]), row))
    if errors:
        more = bad_rows - len(errors)
        tail = [f"…and {more} more rows like these"] if more > 0 else []
        raise MappingError([*errors, *tail])
    if not out.rows:
        raise MappingError(["no rows could be read with this mapping"])
    return out


def _number(sheet: Sheet, i: int) -> int:
    return i + 1 if sheet.format == "ofx" else sheet.first_row + i


def _row(mapping: Mapping, specs, cols: dict[str, int], cells: list[str]) -> tuple[dict, list[str]]:
    row: dict = {}
    problems: list[str] = []
    currency = mapping.currency
    if "currency" in cols:
        raw = cells[cols["currency"]].strip().upper()
        if raw:
            if not re.fullmatch(r"[A-Z]{3}", raw):
                problems.append(f"currency is not a three-letter code (column \"{mapping.columns['currency']}\")")
            else:
                currency = raw
    row["currency"] = currency
    if not currency:
        problems.append("currency is empty and no fixed currency is set")

    def column(name: str) -> str:
        return mapping.columns[name]

    for name, j in cols.items():
        if name in ("currency", *SPLIT_AMOUNT):
            continue
        text = cells[j].strip()
        kind = specs[name].parse
        if not text:
            value = None
        elif kind == "date":
            d = parse_date(text, mapping.day_first)
            value = d.isoformat() if d else None
            if d is None:
                problems.append(f"{name} is not a date (column \"{column(name)}\")")
                continue
        elif kind == "money":
            value = parse_money(text, currency, mapping.decimal_comma)
            if value is None:
                problems.append(f"{name} is not an amount (column \"{column(name)}\")")
                continue
        elif kind == "int":
            value = _int(text)
            if value is None:
                problems.append(f"{name} is not a whole number (column \"{column(name)}\")")
                continue
        elif kind == "last4":
            value = last4(text)
        else:
            value = text[:TEXT_LIMIT]
        row[name] = value

    split = [s for s in SPLIT_AMOUNT if s in cols]
    if split:
        total = 0
        read_any = False
        for s in split:
            text = cells[cols[s]].strip()
            if not text:
                continue
            v = parse_money(text, currency, mapping.decimal_comma)
            if v is None:
                problems.append(f"{s} is not an amount (column \"{column(s)}\")")
                continue
            read_any = True
            total += abs(v) if s == "credit" else -abs(v)
        if read_any:
            row["amount_minor"] = total

    for name in specs:
        if name in DEDUCTIONS and row.get(name) is not None:
            row[name] = abs(row[name])
    for name in mapping.negate:
        if row.get(name) is not None:
            row[name] = -row[name]
    for name, spec in specs.items():
        missing = (spec.required and row.get(name) is None and name not in DERIVED
                   and name != "currency")
        if missing and not any(p.startswith(f"{name} ") for p in problems):
            problems.append(f"{name} is empty" + (
                f" (column \"{column(name)}\")" if name in mapping.columns else ""))
    return row, problems


def _net(row: dict) -> int | None:
    """What a batch paid out when the report does not say: gross less fees
    and chargebacks, plus adjustments as signed. A report that deposits
    gross maps its gross column to net_minor instead."""
    gross = row.get("gross_minor")
    if gross is None:
        return None
    return (gross - (row.get("fees_minor") or 0) - (row.get("chargebacks_minor") or 0)
            + (row.get("adjustments_minor") or 0))


def _fitid(row: dict, seen: dict[str, int]) -> str:
    """A bank line with no id of its own: one derived from what it says,
    stable across uploads of overlapping statements. Two identical lines
    on one statement are told apart by their order."""
    base = "|".join(str(row.get(k) or "") for k in ("posted_on", "amount_minor", "currency",
                                                    "description", "type"))
    n = seen.get(base, 0) + 1
    seen[base] = n
    return "h_" + hashlib.sha256(f"{base}|{n}".encode()).hexdigest()[:20]


def describe(kind: str) -> list[dict]:
    """The fields a mapping for this kind can fill, for a mapping screen."""
    specs = fields_of(kind)
    out = [{"field": s.name, "type": s.parse, "required": s.required and s.name not in DERIVED,
            "description": s.description} for s in specs.values()]
    if "amount_minor" in specs:
        out += [{"field": s, "type": "money", "required": False,
                 "description": f"Instead of amount_minor: money {'in' if s == 'credit' else 'out'}"}
                for s in SPLIT_AMOUNT]
    return out


def when(rows: list[tuple[int, date, dict]]) -> tuple[date | None, date | None]:
    days = [d for _, d, _ in rows]
    return (min(days), max(days)) if days else (None, None)


#: OFX is a standard, not a processor: its columns mean one thing.
OFX_LAYOUT = {"type": "TRNTYPE", "posted_on": "DTPOSTED", "amount_minor": "TRNAMT",
              "fitid": "FITID", "description": "NAME", "currency": "CURRENCY",
              "account_last4": "ACCTID_LAST4"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def suggest(kind: str, header: list[str]) -> dict[str, str]:
    """A starting mapping from column names alone: a column whose name is a
    field's name ("Batch ID" for batch_id), or starts with it at a word
    boundary ("Gross Sales" for gross). Never a processor's layout, and
    never a guess between two columns that could both be the field."""
    if kind == "bank_deposits" and header == OFX_COLUMNS:
        return dict(OFX_LAYOUT)
    specs = fields_of(kind)
    names = list(specs) + (list(SPLIT_AMOUNT) if "amount_minor" in specs else [])
    out: dict[str, str] = {}
    used: set[str] = set()
    for name in names:
        if name in SPLIT_AMOUNT and "amount_minor" in out:
            continue
        want = name.removesuffix("_minor").replace("_", "")
        exact = [h for h in header if h not in used and "".join(_words(h)) == want]
        prefix = [h for h in header if h not in used and h not in exact and any(
            "".join(_words(h)[:k]) == want for k in range(1, len(_words(h))))]
        found = exact if exact else prefix
        if name == "card_last4" and not found:
            found = [h for h in header if h not in used and is_card_column(h)]
        if len(found) == 1:
            out[name] = found[0]
            used.add(found[0])
    return out
