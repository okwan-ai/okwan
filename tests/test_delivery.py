"""Delivery platforms: their reports imported, every payout against its lines and the bank.

Fixtures are the sample reports in docs/samples/imports/delivery: two weeks
of a café's DoorDash transactions and payouts and one week of Uber Eats,
shaped from each platform's documented report fields (real exports come
from dealers; until then the mapping is generic, so a renamed column is a
mapping change, never code), and a bank statement holding their deposits
beside a card processor's.

What would fail silently: a commission or error charge stored with the
file's minus sign, so a sum of deductions cancels out; a line's net left
empty when the report has no net column; a week's payout over a hundred
orders read as ambiguous; an instant payout fee read as money the platform
withheld unexplained; a line not paid out yet reported as a finding; a card
processor's deposit reported as one no delivery payout explains.
"""
from __future__ import annotations

import base64
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import okwan_files.connector  # noqa: F401  (registers the connector)
import pytest
from okwan_core import get
from okwan_files import Mapping, MappingError, prepare
from okwan_files.schemas import DeliveryLine, DeliveryPayout
from okwan_query.catalog import tables_for
from okwan_recon import declarations
from okwan_recon.engine import match

SAMPLES = Path(__file__).resolve().parents[1] / "docs" / "samples" / "imports" / "delivery"

DOORDASH_LINES = Mapping(kind="delivery_lines", currency="USD", name="DoorDash transactions", columns={
    "ordered_on": "Timestamp local date", "type": "Transaction type", "transaction_id": "Transaction ID",
    "order_id": "DoorDash order ID", "pos_order_ref": "Merchant delivery ID", "store_id": "Store ID",
    "status": "Final order status", "currency": "Currency", "subtotal_minor": "Subtotal",
    "tax_minor": "Subtotal tax passed to merchant", "commission_minor": "Commission",
    "processing_fee_minor": "Payment processing fee", "marketing_minor": "Marketing fees",
    "error_charges_minor": "Error charges", "adjustments_minor": "Adjustments", "net_minor": "Net total",
    "payout_on": "Payout date", "payout_id": "Payout ID"})
DOORDASH_PAYOUTS = Mapping(kind="delivery_payouts", currency="USD", name="DoorDash payouts", columns={
    "payout_id": "Payout ID", "paid_on": "Payout date", "status": "Payout status",
    "period_start": "Period start", "period_end": "Period end", "sales_minor": "Subtotal",
    "commission_minor": "Commission", "marketing_minor": "Marketing fees",
    "error_charges_minor": "Error charges", "adjustments_minor": "Adjustments",
    "payout_fee_minor": "Fast Pay fee", "net_minor": "Net payout", "currency": "Currency"})
UBER_LINES = Mapping(kind="delivery_lines", currency="USD", name="Uber Eats payment details", columns={
    "order_id": "Order ID", "ordered_on": "Order Date", "status": "Order Status",
    "subtotal_minor": "Sales (excl. tax)", "tax_minor": "Tax on Sales", "commission_minor": "Marketplace Fee",
    "refunds_minor": "Refunds", "net_minor": "Total Payout", "payout_on": "Payout Date",
    "payout_id": "Payout Reference"})
UBER_PAYOUTS = Mapping(kind="delivery_payouts", currency="USD", name="Uber Eats payouts", columns={
    "payout_id": "Payout Reference", "paid_on": "Payout Date", "status": "Status",
    "net_minor": "Total Payout", "currency": "Currency"})
STATEMENT = Mapping(kind="bank_deposits", currency="USD", name="Bank statement", columns={
    "posted_on": "Date", "description": "Description", "amount_minor": "Amount", "fitid": "Reference"})

FILES = [("doordash-transactions-2026-10.csv", DOORDASH_LINES),
         ("doordash-payouts-2026-10.csv", DOORDASH_PAYOUTS),
         ("uber-eats-payment-details-2026-10.csv", UBER_LINES),
         ("uber-eats-payouts-2026-10.csv", UBER_PAYOUTS),
         ("bank-statement-delivery-2026-10.csv", STATEMENT)]


def _prepared(name: str, mapping: Mapping) -> list[dict]:
    rows = prepare((SAMPLES / name).read_bytes(), name, mapping).normalized.rows
    return [{**fields, "file_id": name, "row": n, "layout": mapping.name} for n, _day, fields in rows]


def _lines(name: str = "doordash-transactions-2026-10.csv", mapping: Mapping = DOORDASH_LINES) -> list[dict]:
    """As the connector serves them: through the model, computed fields included."""
    return [DeliveryLine.model_validate(r).model_dump(mode="json") for r in _prepared(name, mapping)]


def _payouts(name: str = "doordash-payouts-2026-10.csv", mapping: Mapping = DOORDASH_PAYOUTS) -> list[dict]:
    return [DeliveryPayout.model_validate(r).model_dump(mode="json") for r in _prepared(name, mapping)]


# ── the kinds ───────────────────────────────────────────────────────

def test_a_doordash_report_imports_with_every_deduction_kept_apart_and_positive():
    by = {r["transaction_id"]: r for r in _lines()}
    assert len(by) == 11
    missing_item = by["T-1004"]
    assert (missing_item["subtotal_minor"], missing_item["commission_minor"], missing_item["error_charges_minor"],
            missing_item["net_minor"]) == (2700, 405, 1250, 1172)
    assert by["T-1003"]["marketing_minor"] == 500 and by["T-1006"]["adjustments_minor"] == 450  # a credit stays signed
    assert (by["T-1007"]["order_id"], by["T-1007"]["net_minor"]) == (None, -2500)  # a charge tied to no order
    assert by["T-1001"]["pos_order_ref"] == "SQ-ORD-881"


def test_each_line_says_what_the_platform_kept_and_its_commission_rate():
    by = {r["transaction_id"]: r for r in _lines()}
    # 42.50 of items and 3.40 of tax; 38.12 paid: 7.78 kept, the commission 15.01% of the items.
    assert (by["T-1001"]["withheld_minor"], by["T-1001"]["commission_rate_bps"]) == (778, 1501)
    assert by["T-1005"]["commission_rate_bps"] == 2500  # a quarter of the order, not the 15% on the others
    assert by["T-1004"]["withheld_minor"] == 2700 + 216 - 1172  # the missing item's error charge included
    assert by["T-1007"]["commission_rate_bps"] is None  # no sale, no rate


def test_net_is_derived_from_the_parts_when_the_report_has_no_column_for_it():
    columns = {k: v for k, v in DOORDASH_LINES.columns.items() if k != "net_minor"}
    derived = _lines(mapping=DOORDASH_LINES.model_copy(update={"columns": columns}))
    assert [r["net_minor"] for r in derived] == [r["net_minor"] for r in _lines()]


def test_a_line_with_nothing_to_pay_and_no_net_is_refused():
    data = b"Date,Order\n10/05/2026,A1\n"
    with pytest.raises(MappingError, match="net is empty"):
        prepare(data, "x.csv", Mapping(kind="delivery_lines", currency="USD",
                                       columns={"ordered_on": "Date", "order_id": "Order"}))


def test_the_same_kinds_take_uber_eats_through_a_generic_mapping():
    lines = _lines("uber-eats-payment-details-2026-10.csv", UBER_LINES)
    assert [(r["order_id"], r["commission_rate_bps"], r["refunds_minor"]) for r in lines] == [
        ("UE-7Q1", 3000, 0), ("UE-7Q2", 3000, 800), ("UE-7Q3", 3000, 0)]  # the file writes 0.00


def test_two_new_resources_each_a_read_and_a_sql_table():
    files = get("files")
    assert {"delivery_lines", "delivery_payouts"} <= set(files.resources)
    tables = {f"{t.connector}.{t.resource}": dict(t.columns) for t in tables_for(files)}
    assert tables["files.delivery_lines"]["withheld_minor"] == "BIGINT"
    assert tables["files.delivery_lines"]["commission_rate_bps"] == "BIGINT"
    assert tables["files.delivery_payouts"]["paid_on"] == "DATE"


# ── a payout against its lines ──────────────────────────────────────

def _paid(lines: list[dict]) -> list[dict]:
    return [r for r in lines if r["payout_id"]]  # what the check asks the connector for


def test_each_payout_is_its_lines_and_an_instant_payout_fee_explains_its_gap():
    lines = _paid(_lines()) + _paid(_lines("uber-eats-payment-details-2026-10.csv", UBER_LINES))
    payouts = _payouts() + _payouts("uber-eats-payouts-2026-10.csv", UBER_PAYOUTS)
    result = match(declarations.delivery_payout_lines, payouts, lines)
    by = {p.left["payout_id"]: p for p in result.matched}
    assert set(by) == {"DD-PO-1012", "DD-PO-1019", "UE-PAY-1012"}
    assert by["DD-PO-1012"].discrepancy_minor == 0 and by["DD-PO-1012"].right["member_count"] == 7
    fast = by["DD-PO-1019"]
    assert (fast.discrepancy_minor, fast.explained_by) == (-199, "instant_payout_fee")
    assert by["UE-PAY-1012"].discrepancy_minor == 0
    assert not result.unmatched_left and not result.unmatched_right and not result.ambiguous


def test_a_payout_short_of_its_lines_is_a_finding():
    payouts = [{**p, "net_minor": p["net_minor"] - 1000} if p["payout_id"] == "DD-PO-1012" else p
               for p in _payouts()]
    result = match(declarations.delivery_payout_lines, payouts, _paid(_lines()))
    short = next(p for p in result.matched if p.left["payout_id"] == "DD-PO-1012")
    assert short.discrepancy_minor == -1000 and short.is_unexplained


def test_a_weeks_payout_over_hundreds_of_orders_is_one_payout():
    line = {**_paid(_lines())[0], "payout_id": "BUSY"}
    lines = [{**line, "transaction_id": f"T-{i}", "row": i} for i in range(300)]
    payout = {**_payouts()[0], "payout_id": "BUSY", "net_minor": 300 * line["net_minor"], "payout_fee_minor": None}
    (pair,) = match(declarations.delivery_payout_lines, [payout], lines).matched
    assert pair.right["member_count"] == 300 and pair.discrepancy_minor == 0


# ── a payout against the bank ───────────────────────────────────────

def test_payouts_meet_their_deposits_and_a_missing_one_is_named():
    payouts = _payouts() + _payouts("uber-eats-payouts-2026-10.csv", UBER_PAYOUTS)
    statement = _prepared("bank-statement-delivery-2026-10.csv", STATEMENT)
    platforms = [r for r in statement if any(w in r["description"].casefold()
                                              for w in declarations.DELIVERY_PAYERS)]
    result = match(declarations.delivery_payouts_deposits, payouts, platforms)
    pairs = {p.left["payout_id"]: p.right["fitid"] for p in result.matched}
    # Uber paid on Columbus Day; the bank posted it the next business day.
    assert pairs == {"DD-PO-1012": "20261013101", "UE-PAY-1012": "20261013102"}
    assert [p["payout_id"] for p in result.unmatched_left] == ["DD-PO-1019"]   # never arrived
    assert [d["fitid"] for d in result.unmatched_right] == ["20261027101"]     # no payout imported for it


# ── every surface, as a provisioned tenant ──────────────────────────

@pytest.fixture
async def tenant():
    from okwan_api.auth import set_store
    from okwan_api.main import app
    from okwan_vault import EnvMasterKey, MemoryStore, new_key

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    shop = await store.create_tenant("Corner Café")
    key, _ = await store.issue_key(shop.id, scope="admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        c.headers["Authorization"] = f"Bearer {key}"
        for name, mapping in FILES:
            r = await c.post(f"/v1/tenants/{shop.id}/imports", json={
                "filename": name, "content_base64": base64.b64encode((SAMPLES / name).read_bytes()).decode(),
                "mapping": mapping.model_dump(mode="json")})
            assert r.status_code == 201, r.text
        yield c, key


def _verdicts(rows: list[dict], key: str) -> dict[str, str]:
    return {((r.get("left") or {}).get("payout_id") or (r.get("right") or {}).get(key)): r["status"] for r in rows}


async def test_delivery_is_queryable_for_a_provisioned_tenant_on_every_surface(tenant, monkeypatch):
    client, key = tenant
    unpaid = await client.post("/v1/files/delivery_lines/list", json={"paid": False})
    assert [r["transaction_id"] for r in unpaid.json()["items"]] == ["T-1011"]
    platform = await client.post("/v1/files/bank_deposits/list", json={"contains": ["doordash", "uber"]})
    assert "SQUARE INC TRANSFER" not in {r["description"] for r in platform.json()["items"]}

    lines = await client.get("/v1/reconciliations/delivery_payout_lines")
    assert lines.status_code == 200, lines.text
    assert _verdicts(lines.json()["data"], "transaction_id") == {
        "DD-PO-1012": "matched", "DD-PO-1019": "matched_explained", "UE-PAY-1012": "matched"}

    bank = await client.get("/v1/reconciliations/delivery_payouts_deposits")
    assert _verdicts(bank.json()["data"], "fitid") == {
        "DD-PO-1012": "matched", "UE-PAY-1012": "matched", "DD-PO-1019": "unmatched_left",
        "20261027101": "unmatched_right"}  # the card processor's deposit is not this check's

    kept = await client.post("/v1/query", json={"sql": (
        "SELECT sum(commission_minor) AS commission, sum(error_charges_minor) AS error_charges, "
        "sum(withheld_minor) AS withheld FROM files.delivery_lines")})
    assert kept.status_code == 200, kept.text
    (row,) = kept.json()["rows"]
    every = _lines() + _lines("uber-eats-payment-details-2026-10.csv", UBER_LINES)
    assert (row["commission"], row["error_charges"], row["withheld"]) == (
        sum(r["commission_minor"] or 0 for r in every), 1250 + 1990,
        sum(r["withheld_minor"] or 0 for r in every))
    assert row["commission"] == 5034 + 3045  # DoorDash's eleven lines and Uber's three

    over = await client.post("/v1/query", json={"sql": (  # the README's query, as written there
        "SELECT order_id, commission_rate_bps FROM files.delivery_lines "
        "WHERE layout = 'DoorDash transactions' AND commission_rate_bps > 1600")})
    assert [(r["order_id"], r["commission_rate_bps"]) for r in over.json()["rows"]] == [("3F2E2", 2500)]

    view = await client.post("/v1/query", json={
        "sql": "SELECT status, count(*) AS n FROM recon.delivery_payouts_deposits GROUP BY status ORDER BY status"})
    assert {r["status"]: r["n"] for r in view.json()["rows"]} == {
        "matched": 2, "unmatched_left": 1, "unmatched_right": 1}

    from okwan_query.mcp_http import build_server
    tool = build_server()._tool_manager.get_tool("okwan_reconcile")
    out = await tool.fn(name="delivery_payout_lines", ctx=SimpleNamespace(headers={"authorization": f"Bearer {key}"}))
    assert out["summary"]["matched"] == 3


# ── the database knows the new kinds, also when it was made before them ──

@pytest.mark.skipif(not os.environ.get("OKWAN_VAULT_DATABASE_URL"), reason="no vault database configured")
async def test_a_database_made_with_three_kinds_learns_the_delivery_kinds_on_start():
    from okwan_vault import EnvMasterKey, PostgresStore, new_key

    dsn = os.environ["OKWAN_VAULT_DATABASE_URL"]
    store = await PostgresStore(dsn, EnvMasterKey(new_key())).connect()
    await store.pool.execute(
        "ALTER TABLE import_files DROP CONSTRAINT import_files_kind_check, ADD CONSTRAINT import_files_kind_check "
        "CHECK (kind IN ('settlement_batches', 'settlement_transactions', 'bank_deposits'))")
    await store.close()
    store = await PostgresStore(dsn, EnvMasterKey(new_key())).connect()  # the schema runs again
    tenant = await store.create_tenant("Migrated café")
    try:
        rows = _prepared("doordash-payouts-2026-10.csv", DOORDASH_PAYOUTS)
        stored = await store.add_import(
            tenant.id, kind="delivery_payouts", filename="p.csv", format="csv", layout="DoorDash payouts",
            mapping=DOORDASH_PAYOUTS.model_dump(mode="json"), sha256="0" * 64,
            rows=[(r["row"], date.fromisoformat(r["paid_on"]), {k: v for k, v in r.items()
                                                                if k not in ("file_id", "row", "layout")})
                  for r in rows],
            skipped=[], card_columns=[], first_date=date(2026, 10, 13), last_date=date(2026, 10, 20))
        assert stored.kind == "delivery_payouts"
    finally:
        await store.pool.execute("DELETE FROM tenants WHERE id = $1", tenant.id)
        await store.close()
