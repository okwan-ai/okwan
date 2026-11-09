"""The file importer (Tier 1 step 3): reading, the card guard, mapping,
storage and retention, and the upload routes.

Pure functions over fixture files first, then the store on both
implementations, then the routes. No new dependency: workbooks are built
here with zipfile, the way Excel lays them out.

What would fail silently: a card number stored because it was spaced or
dashed, or sat in a preamble row; a refusal that echoes the number it
refuses; a reference refused as a card number (Luhn alone does that one
time in ten); a date read month-first in a day-first file; a fee stored
negative and so added instead of subtracted; a 1,234.56 read as 1.23; an
expired file still served because no sweep ran; a refused file leaving
rows behind.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import random
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from okwan_files import (
    CardNumberFound,
    FileRefused,
    Mapping,
    MappingError,
    describe,
    prepare,
    read,
)
from okwan_files import parse as parse_mod
from okwan_files.guard import holds_card_number, is_card_column, is_card_number, luhn
from okwan_files.mapping import parse_date, parse_money
from okwan_vault import DuplicateImport, EnvMasterKey, MemoryStore, PostgresStore, new_key
from okwan_vault.models import add_months

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")

# ── fixtures: files as processors and banks write them ──────────────

BATCH_CSV = (
    b"Batch Report - Merchant 12345\n"
    b"Generated 11/05/2026\n"
    b"Batch ID,Close Date,Gross Sales,Fees,Chargebacks,Net Deposit,MID,Count\n"
    b'B-1001,11/02/2026,"$1,000.00",(29.00),,971.00,MID-1,12\n'
    b"B-1002,11/03/2026,250.50,-7.27,10.00,233.23,MID-1,3\n"
    b",,,,,,,\n"
    b"Totals,,1250.50,36.27,10.00,1204.23,,15\n"
)

BATCH_MAPPING = Mapping(
    kind="settlement_batches", header_row=3, currency="USD", skip_invalid_rows=True,
    columns={"batch_id": "Batch ID", "closed_on": "Close Date", "gross_minor": "Gross Sales",
             "fees_minor": "Fees", "chargebacks_minor": "Chargebacks", "net_minor": "Net Deposit",
             "mid": "MID", "transaction_count": "Count"},
)

BANK_CSV = (
    "Date;Description;Debit;Credit;Balance\r\n"
    "03/11/2026;DEPOSIT MID-1;;1.204,23;5.000,00\r\n"
    "04/11/2026;SERVICE FEE;15,00;;4.985,00\r\n"
    "04/11/2026;SERVICE FEE;15,00;;4.970,00\r\n"
).encode("cp1252")

BANK_MAPPING = Mapping(
    kind="bank_deposits", currency="EUR", day_first=True, decimal_comma=True,
    columns={"posted_on": "Date", "description": "Description", "credit": "Credit", "debit": "Debit"},
)

OFX_SGML = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
CHARSET:1252

<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>USD
<BANKACCTFROM><BANKID>021000021<ACCTID>000987654321<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261103120000.000[-5:EST]<TRNAMT>1204.23<FITID>2026110301<NAME>DEPOSIT MID-1</STMTTRN>
<STMTTRN><TRNTYPE>XFER<DTPOSTED>20261104<TRNAMT>-50.00<FITID>2026110402<NAME>TRANSFER
<BANKACCTTO><BANKID>1<ACCTID>5555000011112222<ACCTTYPE>SAVINGS</BANKACCTTO></STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

OFX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD</CURDEF>
<BANKACCTFROM><ACCTID>12345678</ACCTID></BANKACCTFROM>
<BANKTRANLIST><STMTTRN><TRNTYPE>DEP</TRNTYPE><DTPOSTED>20261105</DTPOSTED>
<TRNAMT>300.00</TRNAMT><FITID>X1</FITID><NAME>DEPOSIT</NAME>
<CURRENCY><CURRATE>1.0</CURRATE><CURSYM>CAD</CURSYM></CURRENCY></STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""

OFX_MAPPING = Mapping(
    kind="bank_deposits",
    columns={"fitid": "FITID", "posted_on": "DTPOSTED", "amount_minor": "TRNAMT",
             "description": "NAME", "type": "TRNTYPE", "currency": "CURRENCY",
             "account_last4": "ACCTID_LAST4"},
)


def xlsx(rows: list[list], date_cells: set[tuple[int, int]] = frozenset(), *,
         doctype: bool = False) -> bytes:
    """A workbook laid out the way Excel writes one: shared strings, a
    styles part with a date format, the sheet found through the workbook's
    relationships. `rows` holds str (shared), int/float (numbers) or None."""
    strings: list[str] = []
    xml_rows = []
    for i, row in enumerate(rows, 1):
        cells = []
        for j, v in enumerate(row):
            ref = f"{chr(65 + j)}{i}"
            if v is None:
                continue
            if isinstance(v, str):
                strings.append(v)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                style = ' s="1"' if (i, j) in date_cells else ""
                cells.append(f'<c r="{ref}"{style}><v>{v}</v></c>')
        xml_rows.append(f'<row r="{i}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    dt = '<!DOCTYPE x [<!ENTITY a "aaaa">]>' if doctype else ""
    parts = {
        "[Content_Types].xml": "<Types/>",
        "xl/workbook.xml": f'<workbook {ns} {rel}><sheets><sheet name="Report" sheetId="1" r:id="rId7"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId7" Target="worksheets/report.xml" Type="x"/></Relationships>'),
        "xl/styles.xml": f'<styleSheet {ns}><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
        "xl/sharedStrings.xml": f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>",
        "xl/worksheets/report.xml": f'{dt}<worksheet {ns}><sheetData>{"".join(xml_rows)}</sheetData></worksheet>',
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():
            z.writestr(name, body)
    return buf.getvalue()


# ── reading ─────────────────────────────────────────────────────────

def test_a_processor_report_with_a_preamble_and_a_totals_row():
    p = prepare(BATCH_CSV, "batches.csv", BATCH_MAPPING)
    rows = {r["batch_id"]: r for _, _, r in p.normalized.rows}
    assert rows["B-1001"] == {
        "batch_id": "B-1001", "closed_on": "2026-11-02", "gross_minor": 100_000, "currency": "USD",
        "fees_minor": 2_900, "chargebacks_minor": None, "net_minor": 97_100, "mid": "MID-1",
        "transaction_count": 12}
    # A fee printed negative is still a deduction: stored as a positive amount.
    assert rows["B-1002"]["fees_minor"] == 727 and rows["B-1002"]["gross_minor"] == 25_050
    # The totals row has no date: skipped and named, the blank row ignored.
    assert p.normalized.skipped == [7]
    assert [n for n, _, _ in p.normalized.rows] == [4, 5]
    assert (p.first_date, p.last_date) == (date(2026, 11, 2), date(2026, 11, 3))


def test_a_european_bank_csv_with_split_debit_and_credit_columns():
    p = prepare(BANK_CSV, "statement.csv", BANK_MAPPING)
    rows = [r for _, _, r in p.normalized.rows]
    assert [(r["posted_on"], r["amount_minor"], r["currency"]) for r in rows] == [
        ("2026-11-03", 120_423, "EUR"), ("2026-11-04", -1_500, "EUR"), ("2026-11-04", -1_500, "EUR")]
    # No id column: one is derived from the line, and two identical lines differ.
    assert all(r["fitid"].startswith("h_") for r in rows)
    assert rows[1]["fitid"] != rows[2]["fitid"]
    again = prepare(BANK_CSV, "statement.csv", BANK_MAPPING)
    assert [r["fitid"] for _, _, r in again.normalized.rows] == [r["fitid"] for r in rows]


def test_ofx_1_and_2_read_alike():
    sgml = prepare(OFX_SGML, "stmt.qfx", OFX_MAPPING).normalized.rows
    assert [(r["fitid"], r["posted_on"], r["amount_minor"], r["currency"]) for _, _, r in sgml] == [
        ("2026110301", "2026-11-03", 120_423, "USD"), ("2026110402", "2026-11-04", -5_000, "USD")]
    # The statement's account, cut to four digits as it is read; the
    # transfer's other account is not read at all.
    assert {r["account_last4"] for _, _, r in sgml} == {"4321"}
    xml = prepare(OFX_XML, "stmt.ofx", OFX_MAPPING).normalized.rows
    assert [(r["amount_minor"], r["currency"], r["type"]) for _, _, r in xml] == [(30_000, "CAD", "DEP")]


def test_an_ofx_account_number_is_never_held_whole():
    sheet = read(OFX_SGML, "stmt.ofx")
    assert "000987654321" not in repr(sheet) and "5555000011112222" not in repr(sheet)


def test_a_workbook_read_without_a_spreadsheet_library():
    data = xlsx([
        ["Settlement transactions"], [],
        ["Txn Date", "Amount", "Card Number", "Invoice", "Auth"],
        [46328, 99.900000000000006, "************1234", "#1002", 123456],
        [46329, -10, "XXXX-XXXX-XXXX-9876", None, "A9"],
    ], date_cells={(4, 0), (5, 0)})
    m = Mapping(kind="settlement_transactions", header_row=3, currency="USD",
                columns={"transacted_on": "Txn Date", "amount_minor": "Amount",
                         "card_last4": "Card Number", "order_ref": "Invoice", "auth_code": "Auth"})
    p = prepare(data, "export.xlsx", m)
    rows = [r for _, _, r in p.normalized.rows]
    assert p.format == "xlsx"
    assert [(r["transacted_on"], r["amount_minor"], r["card_last4"], r["auth_code"]) for r in rows] == [
        ("2026-11-02", 9_990, "1234", "123456"), ("2026-11-03", -1_000, "9876", "A9")]
    assert p.normalized.card_columns == ["Card Number"]


@pytest.mark.parametrize("data,name,message", [
    (b"", "x.csv", "empty"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100, "old.xls", "older Excel"),
    (b"PK\x03\x04garbage", "x.xlsx", "not an Excel workbook"),
    (b"A,B\n", "x.csv", "no rows"),
    (b"A,a\n1,2\n", "x.csv", "Two columns"),
    (b"<OFX><BANKMSGSRSV1></BANKMSGSRSV1></OFX>", "x.ofx", "no transactions"),
    (b"\x00\x01\x02binary", "x.bin", "does not look like"),
])
def test_files_that_cannot_be_read_are_refused_with_the_reason(data, name, message):
    with pytest.raises(FileRefused, match=message):
        read(data, name)


def test_a_workbook_declaring_a_document_type_is_refused():
    with pytest.raises(FileRefused, match="does not read"):
        read(xlsx([["A"], ["1"]], doctype=True), "x.xlsx")


def test_sizes_are_capped_before_work_grows_with_them(monkeypatch):
    with pytest.raises(FileRefused, match="larger than"):
        read(b"a" * (parse_mod.MAX_FILE_BYTES + 1), "big.csv")
    monkeypatch.setattr(parse_mod, "MAX_ROWS", 3)
    with pytest.raises(FileRefused, match="more than 3 rows"):
        read(b"A\n1\n2\n3\n4\n", "x.csv")
    monkeypatch.setattr(parse_mod, "MAX_XLSX_BYTES", 200)
    with pytest.raises(FileRefused, match="too large once unpacked"):
        read(xlsx([["A" * 300], ["1"]]), "x.xlsx")


# ── the card guard ──────────────────────────────────────────────────

@pytest.mark.parametrize("pan", [
    "4111111111111111", "4222222222222", "5555555555554444", "2223003122003222",
    "378282246310005", "6011111111111117", "6445644564456445", "3530111333300000",
    "36227206271667", "30569309025904", "6200000000000005", "6759649826438453",
])
def test_every_network_a_us_merchant_takes_is_recognised(pan):
    assert is_card_number(pan)


def test_luhn_alone_is_not_enough_to_refuse_a_reference():
    """One random number in ten passes Luhn. References that start where
    no card network does are never taken for card numbers."""
    rng = random.Random(7)
    refs = [str(rng.choice("01789")) + "".join(rng.choice("0123456789") for _ in range(15))
            for _ in range(5000)]
    assert sum(luhn(r) for r in refs) > 400
    assert not any(is_card_number(r) for r in refs)
    assert not is_card_number("4111111111111112")  # a Visa prefix, failing Luhn


@pytest.mark.parametrize("cell", [
    "4111111111111111", "4111 1111 1111 1111", "4111-1111-1111-1111",
    "Paid with 5555 5555 5555 4444 at till 3", "4111 1111 1111 1111 22",
])
def test_a_card_number_is_found_however_it_is_written(cell):
    assert holds_card_number(cell)


@pytest.mark.parametrize("cell", [
    "************1111", "XXXX-XXXX-XXXX-1111", "411111******1111", "2026-11-02 12:00:00",
    "Ref 98765432109876", "1234567812345670", "B-1001",
])
def test_masked_numbers_and_references_pass(cell):
    assert not holds_card_number(cell)


@pytest.mark.parametrize("where,header_row,expected", [
    (2, 1, 'Row 2, column "Note"'),      # a data row
    (1, 3, 'Row 2, column "Note"'),      # the preamble, above the header
])
def test_a_card_number_anywhere_refuses_the_file_and_is_never_echoed(where, header_row, expected):
    lines = ["Date,Note,Amount", "11/02/2026,ok,5.00"] if header_row == 1 else \
        ["Report", "x,4111 1111 1111 1111", "Date,Note,Amount", "11/02/2026,ok,5.00"]
    if header_row == 1:
        lines[1] = "11/02/2026,card 4111-1111-1111-1111,5.00"
    data = "\n".join(lines).encode()
    with pytest.raises(CardNumberFound) as caught:
        read(data, "x.csv", header_row=header_row)
    message = str(caught.value)
    assert message.startswith(expected)
    assert "4111" not in message and "1111" not in message
    assert "Nothing from this file was kept" in message


def test_a_card_number_in_a_workbook_or_a_statement_refuses_it_too():
    with pytest.raises(CardNumberFound, match='Row 2, column "Card"'):
        read(xlsx([["Date", "Card"], ["2026-11-02", "4012888888881881"]]), "x.xlsx")
    ofx = OFX_SGML.replace(b"<NAME>DEPOSIT MID-1", b"<NAME>REFUND 4012888888881881")
    with pytest.raises(CardNumberFound, match='Transaction 1, column "NAME"'):
        read(ofx, "x.ofx")


@pytest.mark.parametrize("header,card", [
    ("Card Number", True), ("card_no", True), ("CC #", True), ("PAN", True),
    ("Masked PAN", True), ("Credit Card", True), ("Primary Account Number", True),
    ("Card Type", False), ("Card Brand", False), ("Account Number", False), ("Panel", False),
])
def test_card_columns_are_known_by_name(header, card):
    assert is_card_column(header) is card


def test_a_card_column_can_only_fill_card_last4():
    data = b"Date,Amount,Card Number\n11/02/2026,5.00,************1234\n"
    m = Mapping(kind="settlement_transactions", currency="USD",
                columns={"transacted_on": "Date", "amount_minor": "Amount", "order_ref": "Card Number"})
    with pytest.raises(MappingError, match="card number column"):
        prepare(data, "x.csv", m)


# ── mapping ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,day_first,expected", [
    ("2026-11-02", False, date(2026, 11, 2)), ("2026/11/02 14:05", False, date(2026, 11, 2)),
    ("11/02/2026", False, date(2026, 11, 2)), ("02/11/2026", True, date(2026, 11, 2)),
    ("1/2/26", False, date(2026, 1, 2)), ("02-Nov-2026", False, date(2026, 11, 2)),
    ("Nov 2, 2026", False, date(2026, 11, 2)), ("20261102", False, date(2026, 11, 2)),
    ("20261102120000.000[-5:EST]", False, date(2026, 11, 2)),
    ("13/02/2026", False, None), ("31/02/2026", True, None), ("soon", False, None),
])
def test_dates(text, day_first, expected):
    assert parse_date(text, day_first) == expected


@pytest.mark.parametrize("text,comma,currency,expected", [
    ("$1,234.56", False, "USD", 123_456), ("(12.00)", False, "USD", -1_200),
    ("12.00-", False, "USD", -1_200), ("-12.5", False, "USD", -1_250),
    ("1.234,56", True, "EUR", 123_456), ("5 CR", False, "USD", 500), ("5.00 DR", False, "USD", -500),
    ("USD 99.10", False, "USD", 9_910), ("1500", False, "JPY", 1_500),
    ("abc", False, "USD", None), ("1.2.3", False, "USD", None), ("1" + "0" * 20, False, "USD", None),
])
def test_amounts_become_minor_units_without_float(text, comma, currency, expected):
    assert parse_money(text, currency, comma) == expected


def test_a_mapping_that_does_not_fit_says_everything_wrong_at_once():
    m = Mapping(kind="bank_deposits",
                columns={"posted_on": "When", "amount_minor": "Amount", "credit": "Credit",
                         "nonsense": "Date"}, negate=["description"])
    with pytest.raises(MappingError) as caught:
        prepare(BANK_CSV, "s.csv", m)
    problems = " | ".join(caught.value.problems)
    for part in ('no field "nonsense"', 'no column "When"', "not both",
                 "currency is required", '"description" is not an amount field'):
        assert part in problems


def test_rows_that_cannot_be_read_refuse_the_file_without_their_values():
    data = ("Date,Amount\n" + "\n".join(f"not-a-date-{i},5.00" for i in range(14))).encode()
    m = Mapping(kind="bank_deposits", currency="USD",
                columns={"posted_on": "Date", "amount_minor": "Amount"})
    with pytest.raises(MappingError) as caught:
        prepare(data, "x.csv", m)
    problems = caught.value.problems
    assert problems[0] == 'Row 2: posted_on is not a date (column "Date")'
    assert len(problems) == 11 and problems[-1] == "…and 4 more rows like these"
    assert "not-a-date" not in " ".join(problems)


def test_negate_flips_a_file_that_signs_refunds_positive():
    data = b"Date,Amount\n11/02/2026,25.00\n"
    m = Mapping(kind="settlement_transactions", currency="USD", negate=["amount_minor"],
                columns={"transacted_on": "Date", "amount_minor": "Amount"})
    assert prepare(data, "x.csv", m).normalized.rows[0][2]["amount_minor"] == -2_500


def test_a_currency_column_wins_over_the_fixed_one_and_an_empty_cell_falls_back():
    data = b"Date,Amount,Cur\n11/02/2026,1.00,gbp\n11/03/2026,2.00,\n"
    m = Mapping(kind="bank_deposits", currency="USD",
                columns={"posted_on": "Date", "amount_minor": "Amount", "currency": "Cur"})
    assert [r["currency"] for _, _, r in prepare(data, "x.csv", m).normalized.rows] == ["GBP", "USD"]


def test_describe_lists_what_a_mapping_screen_offers():
    fields = {f["field"]: f for f in describe("bank_deposits")}
    assert fields["posted_on"]["required"] and fields["amount_minor"]["type"] == "money"
    assert not fields["fitid"]["required"]  # derived when the file has none
    assert {"credit", "debit"} <= set(fields)
    assert "file_id" not in fields  # provenance is Okwan's, never mapped


def test_a_month_later_is_never_a_day_that_does_not_exist():
    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2027, 1, 31, tzinfo=UTC), 13) == datetime(2028, 2, 29, tzinfo=UTC)
    assert add_months(datetime(2026, 11, 7, 9, tzinfo=UTC), 13) == datetime(2027, 12, 7, 9, tzinfo=UTC)


# ── storage, on both stores ─────────────────────────────────────────

@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        yield MemoryStore(EnvMasterKey(new_key()))
        return
    if not DSN:
        pytest.skip("no vault database configured")
    s = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    created: list[str] = []
    create = s.create_tenant

    async def tracked(name, parent_id=None):
        t = await create(name, parent_id=parent_id)
        created.append(t.id)
        return t

    s.create_tenant = tracked
    try:
        yield s
    finally:
        for tenant_id in reversed(created):
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await s.close()


async def _save(store, tenant_id, data=BATCH_CSV, mapping=BATCH_MAPPING, now=None):
    p = prepare(data, "batches.csv", mapping)
    n = p.normalized
    return await store.add_import(
        tenant_id, kind=mapping.kind, filename="batches.csv", format=p.format, layout=mapping.name,
        mapping=mapping.model_dump(mode="json"), sha256=p.sha256, rows=n.rows, skipped=n.skipped,
        card_columns=n.card_columns, first_date=p.first_date, last_date=p.last_date, now=now)


async def test_an_import_is_stored_listed_read_and_deleted(store):
    t = await store.create_tenant("Dealer")
    f = await _save(store, t.id)
    assert (f.kind, f.row_count, f.skipped, f.skipped_rows) == ("settlement_batches", 2, 1, [7])
    assert f.expires_at.date() == add_months(f.created_at, 13).date()
    assert [x.id for x in await store.list_imports(t.id)] == [f.id]
    assert (await store.get_import(t.id, f.id)).mapping["columns"]["batch_id"] == "Batch ID"
    rows = await store.import_rows(t.id, "settlement_batches")
    assert [(r["batch_id"], r["file_id"], r["row"], r["layout"]) for r in rows] == [
        ("B-1001", f.id, 4, "custom"), ("B-1002", f.id, 5, "custom")]
    assert await store.import_rows(t.id, "bank_deposits") == []
    assert await store.delete_import(t.id, f.id) is True
    assert await store.import_rows(t.id, "settlement_batches") == []
    assert await store.delete_import(t.id, f.id) is False


async def test_rows_page_in_a_stable_order_and_filter_by_day(store):
    t = await store.create_tenant("Dealer")
    await _save(store, t.id)
    await _save(store, t.id, BANK_CSV, BANK_MAPPING)
    first = await store.import_rows(t.id, "settlement_batches", limit=1)
    last = first[-1]
    rest = await store.import_rows(
        t.id, "settlement_batches",
        after=(date.fromisoformat(last["closed_on"]), last["file_id"], last["row"]))
    assert [r["batch_id"] for r in first + rest] == ["B-1001", "B-1002"]
    on_the_4th = await store.import_rows(t.id, "bank_deposits", since=date(2026, 11, 4),
                                         until=date(2026, 11, 4))
    assert [r["amount_minor"] for r in on_the_4th] == [-1_500, -1_500]


async def test_the_same_file_twice_is_refused_not_doubled(store):
    t = await store.create_tenant("Dealer")
    f = await _save(store, t.id)
    with pytest.raises(DuplicateImport) as caught:
        await _save(store, t.id)
    assert caught.value.existing_id == f.id
    other = await store.create_tenant("Another dealer")
    assert (await _save(store, other.id)).id != f.id  # another tenant's copy is its own


async def test_an_expired_file_is_never_served_even_before_the_sweep(store):
    t = await store.create_tenant("Dealer")
    long_ago = datetime.now(UTC) - timedelta(days=400)
    old = await _save(store, t.id, now=long_ago)
    assert await store.list_imports(t.id) == []
    assert await store.get_import(t.id, old.id) is None
    assert await store.import_rows(t.id, "settlement_batches") == []
    # An upload prunes the tenant's expired files, so the same file is
    # accepted again rather than refused as a duplicate of dead data.
    fresh = await _save(store, t.id)
    assert [f.id for f in await store.list_imports(t.id)] == [fresh.id]


async def test_the_sweep_deletes_every_tenants_expired_files(store):
    a, b = await store.create_tenant("A"), await store.create_tenant("B")
    long_ago = datetime.now(UTC) - timedelta(days=400)
    kept = await _save(store, b.id)
    await _save(store, a.id, now=long_ago)
    await _save(store, b.id, BANK_CSV, BANK_MAPPING, now=long_ago)
    assert await store.sweep_imports() >= 2
    assert [f.id for f in await store.list_imports(b.id)] == [kept.id]
    assert await store.list_imports(a.id, now=long_ago + timedelta(days=1)) == []


async def test_deleting_a_tenant_deletes_its_imports(store):
    root = await store.create_tenant("Leaving")
    shop = await store.create_tenant("Leaving shop", parent_id=root.id)
    await _save(store, shop.id)
    await store.delete_tenant(root.id)
    assert await store.list_imports(shop.id) == []
    assert await store.import_rows(shop.id, "settlement_batches") == []


async def test_one_tenants_rows_are_never_another_tenants(store):
    a, b = await store.create_tenant("A"), await store.create_tenant("B")
    fa = await _save(store, a.id)
    assert await store.import_rows(b.id, "settlement_batches") == []
    assert await store.get_import(b.id, fa.id) is None
    assert await store.delete_import(b.id, fa.id) is False
    assert len(await store.import_rows(a.id, "settlement_batches")) == 2


# ── the routes ──────────────────────────────────────────────────────

def _body(data: bytes = BATCH_CSV, mapping: Mapping = BATCH_MAPPING, name="batches.csv") -> dict:
    return {"filename": name, "content_base64": base64.b64encode(data).decode(),
            "mapping": mapping.model_dump(mode="json")}


@pytest.fixture
async def api():
    from okwan_api.auth import set_store
    from okwan_api.main import app

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    root = await store.create_tenant("Dealer group")
    shop = await store.create_tenant("Store 12", parent_id=root.id)
    key, _ = await store.issue_key(root.id, scope="admin")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://okwan.test") as c:
        c.headers["Authorization"] = f"Bearer {key}"
        yield c, store, shop


async def test_upload_list_read_and_delete_over_rest(api):
    client, store, shop = api
    r = await client.post(f"/v1/tenants/{shop.id}/imports", json=_body())
    assert r.status_code == 201, r.text
    f = r.json()
    assert (f["kind"], f["rows"], f["skipped_rows"], f["first_date"]) == (
        "settlement_batches", 2, [7], "2026-11-02")
    listed = (await client.get(f"/v1/tenants/{shop.id}/imports")).json()["imports"]
    assert [x["id"] for x in listed] == [f["id"]]
    assert (await client.get(f"/v1/tenants/{shop.id}/imports", params={"kind": "nope"})).status_code == 400
    assert (await client.get(f"/v1/tenants/{shop.id}/imports/{f['id']}")).json()["layout"] == "custom"
    assert (await client.delete(f"/v1/tenants/{shop.id}/imports/{f['id']}")).status_code == 204
    assert (await client.get(f"/v1/tenants/{shop.id}/imports/{f['id']}")).status_code == 404
    assert await store.import_rows(shop.id, "settlement_batches") == []


async def test_a_file_with_a_card_number_is_refused_and_nothing_is_kept(api, caplog):
    client, store, shop = api
    data = BATCH_CSV.replace(b"MID-1,12", b"4111 1111 1111 1111,12")
    with caplog.at_level(logging.DEBUG):
        r = await client.post(f"/v1/tenants/{shop.id}/imports", json=_body(data))
    assert r.status_code == 422
    assert r.json()["detail"].startswith('Row 4, column "MID"')
    assert "4111" not in r.text and "4111" not in caplog.text
    assert await store.list_imports(shop.id) == []


async def test_refusals_say_what_is_wrong(api):
    client, _, shop = api
    url = f"/v1/tenants/{shop.id}/imports"
    bad = Mapping(kind="settlement_batches", columns={"batch_id": "Nope"})
    r = await client.post(url, json=_body(mapping=bad))
    assert r.status_code == 400 and 'no column "Nope"' in " ".join(r.json()["detail"]["problems"])
    assert (await client.post(url, json=_body(b"", name="x.csv") | {"content_base64": "!!"})).status_code == 400
    assert (await client.post(url, json=_body(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 9,
                                              name="x.xls"))).status_code == 400
    assert (await client.post(url, json=_body())).status_code == 201
    dup = await client.post(url, json=_body())
    assert dup.status_code == 409 and dup.json()["detail"]["existing_id"].startswith("imp_")


async def test_an_upload_may_be_up_to_8_mb_and_nothing_else_grows(api):
    client, _, shop = api
    big = b"Date,Amount\n" + b"11/02/2026,1.00\n" * 300_000  # ~4.8 MB, past the 1 MB default
    m = Mapping(kind="bank_deposits", currency="USD",
                columns={"posted_on": "Date", "amount_minor": "Amount"})
    r = await client.post(f"/v1/tenants/{shop.id}/imports", json=_body(big, m))
    assert r.status_code == 400 and "more than 50,000 rows" in r.json()["detail"]
    too_big = {"filename": "x.csv", "mapping": m.model_dump(mode="json"),
               "content_base64": "A" * (13 * 1024 * 1024)}
    assert (await client.post(f"/v1/tenants/{shop.id}/imports", json=too_big)).status_code == 413
    other = await client.post("/v1/query", json={"sql": "SELECT 1", "pad": "x" * (2 * 1024 * 1024)})
    assert other.status_code == 413


async def test_outside_development_uploads_wait_for_the_privacy_policy(api, monkeypatch):
    client, _, shop = api
    monkeypatch.setenv("OKWAN_ENV", "production")
    monkeypatch.delenv("OKWAN_IMPORTS_OPEN", raising=False)
    r = await client.post(f"/v1/tenants/{shop.id}/imports", json=_body())
    assert r.status_code == 503
    assert (await client.get(f"/v1/tenants/{shop.id}/imports")).status_code == 200
    monkeypatch.setenv("OKWAN_IMPORTS_OPEN", "true")
    assert (await client.post(f"/v1/tenants/{shop.id}/imports", json=_body())).status_code == 201


async def test_uploads_are_limited_per_tenant(api, monkeypatch):
    from okwan_api import imports
    from okwan_api.ratelimit import Rule

    client, _, shop = api
    monkeypatch.setattr(imports, "IMPORT_TENANT", Rule("import:tenant:test", 1, 3600))
    assert (await client.post(f"/v1/tenants/{shop.id}/imports", json=_body())).status_code == 201
    r = await client.post(f"/v1/tenants/{shop.id}/imports", json=_body(BANK_CSV, BANK_MAPPING))
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


async def test_the_sweeper_deletes_expired_files_and_survives_a_failure(monkeypatch):
    from okwan_api import imports
    from okwan_api.auth import set_store

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    t = await store.create_tenant("Dealer")
    await _save(store, t.id, now=datetime.now(UTC) - timedelta(days=400))
    calls = []
    real = store.sweep_imports

    async def flaky(now=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database away")
        return await real(now)

    monkeypatch.setattr(store, "sweep_imports", flaky)
    task = asyncio.create_task(imports.sweeper(interval=0.01))
    while len(calls) < 2:
        await asyncio.sleep(0.01)
    task.cancel()
    assert store._imports == {}
