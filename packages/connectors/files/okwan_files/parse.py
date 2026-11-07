"""Reading an uploaded file into a header and rows of text.

CSV, XLSX and OFX, nothing else. The file is read in memory and its bytes
are never kept: what survives is the rows a mapping makes of it, or a
refusal (Tier 1 plan, §9 2026-11-06).

Every reader is defensive about what it is handed. Sizes, rows and
columns are capped before work grows with them; XLSX is read with the
standard library, a zip whose parts are checked for size before they are
inflated and whose XML is refused if it declares a document type, the
door to entity expansion; OFX is matched tag by tag, never evaluated.

OFX has no header row, so its reader supplies one (`OFX_COLUMNS`), and a
mapping names those columns the way it names a CSV's. An OFX account id
is cut to its last four digits as it is read: a card statement's account
id is the card number.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Context, Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from . import guard
from .guard import last4

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 200
#: The most an XLSX may inflate to across the parts read, and any one part.
MAX_XLSX_BYTES = 64 * 1024 * 1024

FORMATS = ("csv", "xlsx", "ofx")

OFX_COLUMNS = ["TRNTYPE", "DTPOSTED", "TRNAMT", "FITID", "NAME", "MEMO",
               "CHECKNUM", "REFNUM", "CURRENCY", "ACCTID_LAST4"]


class FileRefused(ValueError):
    """The file cannot be read as uploaded. The message says why."""


@dataclass(frozen=True, slots=True)
class Sheet:
    format: str
    header: list[str]
    rows: list[list[str]]
    #: The file's row number of the first data row (1-based, as a
    #: spreadsheet shows it); OFX numbers transactions from 1.
    first_row: int

    def where(self, i: int) -> str:
        if self.format == "ofx":
            return f"Transaction {i + 1}"
        return f"Row {self.first_row + i}"


def detect(data: bytes, filename: str) -> str:
    """The format, from the bytes first and the name second."""
    name = filename.lower()
    if data[:4] == b"PK\x03\x04":
        return "xlsx"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise FileRefused("This is an older Excel file (.xls). Save it as .xlsx or .csv and upload it again.")
    head = data[:4096].lstrip().upper()
    if head.startswith(b"OFXHEADER") or b"<OFX>" in head or name.endswith((".ofx", ".qfx")):
        return "ofx"
    return "csv"


def read(data: bytes, filename: str, header_row: int = 1) -> Sheet:
    if not data:
        raise FileRefused("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise FileRefused(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB. Split it and upload each part.")
    fmt = detect(data, filename)
    if fmt == "ofx":
        sheet = _ofx(data)
        guard.scan(sheet.rows, sheet.header, sheet.where)
        return sheet
    grid = _xlsx(data) if fmt == "xlsx" else _csv(data)
    # The whole grid, preamble and header included, before any of it is
    # taken apart: no Sheet ever exists with a card number in it.
    names = grid[header_row - 1] if len(grid) >= header_row else []
    guard.scan(grid, names, lambda i: f"Row {i + 1}")
    return _sheet(fmt, grid, header_row)


# ── a grid into a sheet ─────────────────────────────────────────────

def _sheet(fmt: str, grid: list[list[str]], header_row: int) -> Sheet:
    if len(grid) < header_row:
        raise FileRefused(f"The file has no row {header_row} to read the column names from.")
    header = [h.strip() for h in grid[header_row - 1]]
    while header and not header[-1]:
        header.pop()
    if not any(header):
        raise FileRefused(f"Row {header_row} is empty: it should hold the column names.")
    if len(header) > MAX_COLUMNS:
        raise FileRefused(f"The file has more than {MAX_COLUMNS} columns.")
    seen: set[str] = set()
    for h in header:
        key = h.lower()
        if h and key in seen:
            raise FileRefused(f"Two columns are both named \"{h}\". Rename one and upload again.")
        seen.add(key)
    width = len(header)
    rows: list[list[str]] = []
    first = header_row + 1
    for raw in grid[header_row:]:
        cells = [c.strip() for c in raw]
        # Cells past the named columns are kept for the card search only
        # when they hold something; a trailing comma is not a column.
        while len(cells) > width and not cells[-1]:
            cells.pop()
        cells += [""] * (width - len(cells))
        rows.append(cells)
    while rows and not any(rows[-1]):
        rows.pop()
    if not rows:
        raise FileRefused("The file has column names but no rows under them.")
    if len(rows) > MAX_ROWS:
        raise FileRefused(f"The file has more than {MAX_ROWS:,} rows. Split it and upload each part.")
    return Sheet(format=fmt, header=header, rows=rows, first_row=first)


# ── CSV ─────────────────────────────────────────────────────────────

def _text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _csv(data: bytes) -> list[list[str]]:
    text = _text(data)
    if "\x00" in text:
        raise FileRefused("This does not look like a CSV, XLSX or OFX file.")
    sample = text[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text, newline=""), dialect)
    grid: list[list[str]] = []
    try:
        for row in reader:
            grid.append(row)
            if len(grid) > MAX_ROWS + 60:
                raise FileRefused(f"The file has more than {MAX_ROWS:,} rows. Split it and upload each part.")
    except csv.Error as exc:
        raise FileRefused(f"The CSV could not be read past line {reader.line_num}: {exc}.") from None
    return grid


# ── XLSX, with the standard library ─────────────────────────────────

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "p": "http://schemas.openxmlformats.org/package/2006/relationships"}
_M = "{" + _NS["m"] + "}"
#: Built-in number formats that are dates (ECMA-376 18.8.30).
_DATE_FORMATS = frozenset({14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47})
_EPOCH = date(1899, 12, 30)


def _part(z: zipfile.ZipFile, name: str, budget: list[int]) -> bytes | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XLSX_BYTES or info.file_size > budget[0]:
        raise FileRefused("The workbook is too large once unpacked.")
    budget[0] -= info.file_size
    raw = z.read(info)
    head = raw[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in raw.upper():
        raise FileRefused("The workbook holds XML that Okwan does not read.")
    return raw


def _xml(raw: bytes) -> ET.Element:
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise FileRefused("The workbook could not be read: one of its parts is not valid XML.") from None


def _first_sheet(z: zipfile.ZipFile, budget: list[int]) -> str:
    book = _part(z, "xl/workbook.xml", budget)
    rels = _part(z, "xl/_rels/workbook.xml.rels", budget)
    if book is None:
        raise FileRefused("This is not an Excel workbook.")
    sheet = _xml(book).find("m:sheets/m:sheet", _NS)
    if sheet is not None and rels is not None:
        rid = sheet.get(f"{{{_NS['r']}}}id")
        for rel in _xml(rels).findall("p:Relationship", _NS):
            if rel.get("Id") == rid:
                target = rel.get("Target", "").lstrip("/")
                return target if target.startswith("xl/") else f"xl/{target}"
    return "xl/worksheets/sheet1.xml"


def _date_styles(z: zipfile.ZipFile, budget: list[int]) -> set[int]:
    raw = _part(z, "xl/styles.xml", budget)
    if raw is None:
        return set()
    root = _xml(raw)
    custom = {
        int(f.get("numFmtId", "0")) for f in root.findall("m:numFmts/m:numFmt", _NS)
        if re.search(r"[dy]", re.sub(r'"[^"]*"|\[[^\]]*\]', "", f.get("formatCode", "").lower()))
    }
    out = set()
    for i, xf in enumerate(root.findall("m:cellXfs/m:xf", _NS)):
        fmt = int(xf.get("numFmtId", "0"))
        if fmt in _DATE_FORMATS or fmt in custom:
            out.add(i)
    return out


def _shared(z: zipfile.ZipFile, budget: list[int]) -> list[str]:
    raw = _part(z, "xl/sharedStrings.xml", budget)
    if raw is None:
        return []
    return ["".join(t.text or "" for t in si.iter(f"{_M}t")) for si in _xml(raw).findall("m:si", _NS)]


def _column(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _number(text: str) -> str:
    """A stored number as Excel shows it: 15 significant digits, which is
    all Excel keeps, as plain text with no exponent and no trailing .0.
    (A workbook stores 99.9 as 99.900000000000006.)"""
    try:
        d = Context(prec=15).create_decimal(Decimal(text))
    except InvalidOperation:
        return text
    if not d.is_finite():
        return text
    if d == d.to_integral_value():
        return str(d.quantize(Decimal(1)))
    return format(d.normalize(), "f")


def _serial_date(text: str) -> str:
    try:
        serial = float(text)
    except ValueError:
        return text
    if not 1 <= serial < 2958466:
        return text
    day = _EPOCH + timedelta(days=int(serial))
    return day.isoformat()


def _xlsx(data: bytes) -> list[list[str]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise FileRefused("This is not an Excel workbook.") from None
    budget = [MAX_XLSX_BYTES]
    with z:
        sheet_name = _first_sheet(z, budget)
        dates = _date_styles(z, budget)
        strings = _shared(z, budget)
        raw = _part(z, sheet_name, budget)
        if raw is None:
            raise FileRefused("The workbook has no sheet to read.")
    grid: list[list[str]] = []
    for row in _xml(raw).iter(f"{_M}row"):
        if len(grid) > MAX_ROWS + 60:
            raise FileRefused(f"The file has more than {MAX_ROWS:,} rows. Split it and upload each part.")
        r = int(row.get("r", len(grid) + 1))
        while len(grid) < r - 1:
            grid.append([])
        cells: list[str] = []
        for c in row.findall(f"{_M}c"):
            j = _column(c.get("r", "")) if c.get("r") else len(cells)
            if j >= MAX_COLUMNS + 50:
                continue
            kind = c.get("t", "n")
            v = c.find(f"{_M}v")
            text = v.text if v is not None and v.text is not None else ""
            if kind == "s":
                try:
                    text = strings[int(text)]
                except (ValueError, IndexError):
                    text = ""
            elif kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{_M}t"))
            elif kind == "b":
                text = "TRUE" if text == "1" else "FALSE"
            elif kind == "e":
                text = ""
            elif kind == "n" and text:
                style = int(c.get("s", "0"))
                text = _serial_date(text) if style in dates else _number(text)
            while len(cells) < j:
                cells.append("")
            cells.append(text)
        grid.append(cells)
    return grid


# ── OFX, 1.x (SGML) and 2.x (XML) ───────────────────────────────────

_TAG = re.compile(r"<(/?)([A-Za-z0-9.]+)>([^<]*)")


def _ofx(data: bytes) -> Sheet:
    text = _text(data)
    if "<OFX>" not in text.upper():
        raise FileRefused("This does not look like an OFX file: it has no <OFX> section.")
    currency = ""
    account = ""
    rows: list[list[str]] = []
    current: dict[str, str] | None = None
    for close, tag, value in _TAG.findall(text):
        tag = tag.upper()
        value = value.strip()
        if tag == "STMTTRN":
            if close:
                if current is not None:
                    rows.append([*(current.get(c, "") for c in OFX_COLUMNS[:-2]),
                                 current.get("CURRENCY", "") or currency, account])
                    if len(rows) > MAX_ROWS:
                        raise FileRefused(f"The file has more than {MAX_ROWS:,} transactions.")
                current = None
            else:
                current = {}
            continue
        if close:
            continue
        if tag == "CURDEF":
            currency = value.upper()
        elif tag == "ACCTID" and current is None:
            # The statement's own account. Inside a transaction an ACCTID is
            # the other side of a transfer, and is not read.
            account = last4(value) or ""
        elif current is not None and tag in OFX_COLUMNS:
            current[tag] = value
        elif current is not None and tag == "CURSYM":
            current["CURRENCY"] = value.upper()
    if not rows:
        raise FileRefused("The OFX file has no transactions in it.")
    return Sheet(format="ofx", header=list(OFX_COLUMNS), rows=rows, first_row=1)
