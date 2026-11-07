"""The sample import files (docs/samples/imports) import and reconcile as
their README says.

They are what a dealer copies and what a demo uploads, so a change to
parsing, mapping or matching that breaks them, or changes what they
show, fails here first: the README's mappings are the ones used, and its
findings are the ones asserted.
"""
from __future__ import annotations

from pathlib import Path

import okwan_files.connector  # noqa: F401  (registers the connector)
from okwan_files import Mapping, prepare
from okwan_files.mapping import suggest
from okwan_files.parse import read
from okwan_recon import declarations
from okwan_recon.engine import match

SAMPLES = Path(__file__).resolve().parents[1] / "docs" / "samples" / "imports"

BATCHES = Mapping(kind="settlement_batches", currency="USD", name="sample-processor-batches", columns={
    "batch_id": "Batch ID", "closed_on": "Close Date", "gross_minor": "Gross Sales", "fees_minor": "Fees",
    "chargebacks_minor": "Chargebacks", "net_minor": "Net Deposit", "mid": "MID"})
TRANSACTIONS = Mapping(kind="settlement_transactions", currency="USD", name="sample-processor-transactions",
                       columns={"transacted_on": "Transaction Date", "amount_minor": "Amount",
                                "card_last4": "Card", "card_brand": "Card Brand", "auth_code": "Auth Code",
                                "transaction_id": "Transaction ID", "type": "Type", "fee_minor": "Fee"})
STATEMENT = Mapping(kind="bank_deposits", currency="USD", name="sample-bank-statement", columns={
    "posted_on": "Date", "description": "Description", "amount_minor": "Amount", "fitid": "Reference"})


def _rows(name: str, mapping: Mapping | None = None) -> list[dict]:
    data = (SAMPLES / name).read_bytes()
    if mapping is None:  # an OFX statement maps itself
        from okwan_files.mapping import OFX_LAYOUT

        mapping = Mapping(kind="bank_deposits", currency="USD", name="ofx", columns=dict(OFX_LAYOUT))
    rows = prepare(data, name, mapping).normalized.rows  # (row in the file, its day, fields)
    return [{**fields, "file_id": name, "row": n, "layout": mapping.name} for n, _day, fields in rows]


def test_every_sample_imports_with_the_readme_mapping_and_nothing_is_refused():
    assert len(_rows("processor-batches-2026-10.csv", BATCHES)) == 9
    assert len(_rows("bank-statement-2026-10.csv", STATEMENT)) == 7
    sales = _rows("processor-transactions-2026-10-09.csv", TRANSACTIONS)
    assert [r["card_last4"] for r in sales] == ["1111", "2222", "3333", "4444", "1111"]  # masked, last four kept
    assert [r["amount_minor"] for r in sales] == [1250, 1250, 4600, 875, -1250]


def test_the_ofx_statement_is_the_same_statement():
    def key(rows):
        return [(str(r["posted_on"]), r["amount_minor"]) for r in rows]
    assert key(_rows("bank-statement-2026-10.ofx")) == key(_rows("bank-statement-2026-10.csv", STATEMENT))


def test_the_readme_says_which_columns_map_themselves():
    sheet = read((SAMPLES / "processor-batches-2026-10.csv").read_bytes(), "b.csv")
    assert suggest("settlement_batches", sheet.header) == {
        "batch_id": "Batch ID", "gross_minor": "Gross Sales", "fees_minor": "Fees",
        "chargebacks_minor": "Chargebacks", "net_minor": "Net Deposit", "mid": "MID"}  # Close Date is picked
    sheet = read((SAMPLES / "processor-transactions-2026-10-09.csv").read_bytes(), "t.csv")
    assert suggest("settlement_transactions", sheet.header) == {  # the date and the card are picked
        "amount_minor": "Amount", "transaction_id": "Transaction ID", "type": "Type", "auth_code": "Auth Code",
        "card_brand": "Card Brand", "fee_minor": "Fee"}
    sheet = read((SAMPLES / "bank-statement-2026-10.csv").read_bytes(), "s.csv")
    assert suggest("bank_deposits", sheet.header) == {  # the date and the reference are picked
        "amount_minor": "Amount", "description": "Description"}


def test_the_week_reconciles_as_the_readme_says():
    batches = _rows("processor-batches-2026-10.csv", BATCHES)
    deposits = [r for r in _rows("bank-statement-2026-10.csv", STATEMENT) if r["amount_minor"] > 0]
    result = match(declarations.batches_deposits, batches, deposits)
    paid = {p.right["fitid"]: sorted(m["batch_id"] for m in (p.members or [p.left])) for p in result.matched}
    assert paid == {
        "20261006001": ["B-1005"], "20261007001": ["B-1006"], "20261008001": ["B-1007"],
        "20261009001": ["B-1008"],
        # Friday to Monday, Columbus Day, in the one deposit the Tuesday after.
        "20261013001": ["B-1009", "B-1010", "B-1011", "B-1012"],
    }
    assert all(p.discrepancy_minor == 0 for p in result.matched)
    assert [r["batch_id"] for r in result.unmatched_left] == ["B-1013"]        # never paid
    assert [r["fitid"] for r in result.unmatched_right] == ["20261016001"]     # no batch explains it
    assert not result.ambiguous
