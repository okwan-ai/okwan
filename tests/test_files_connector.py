"""Imported files read back as a connector (Tier 1 step 4).

One declaration, every surface: the `files` connector's three Resources
are REST routes, SQL tables and MCP tools like any connector's, and
`batches_deposits` reconciles a processor's batches against a bank
statement through REST, SQL (`recon.batches_deposits`) and MCP. Saved
column mappings, a preview for the mapping screen, and each source's
status in the reconciliation listing.

What would fail silently: a list that pages past rows or repeats them;
a connector handed a tenant from the request instead of the key; a
local MCP server that reads someone's files with no account at all; a
deposit compared against gross instead of net; a bank fee taken for a
deposit nobody paid; a preview that echoes a card column; a listing that
says a source is ready when it has no files.
"""
from __future__ import annotations

import base64
import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from okwan_core import CredentialError, get
from okwan_core.connector import ConnectorContext
from okwan_files import Mapping, prepare
from okwan_files import connector as files_connector
from okwan_files.mapping import suggest
from okwan_vault import EnvMasterKey, MemoryStore, new_key

BATCHES = (
    b"Batch ID,Close Date,Gross,Fees,MID\n"
    b"B-1,09/04/2026,100.00,2.90,MID-1\n"     # Friday
    b"B-2,09/05/2026,200.00,5.80,MID-1\n"     # Saturday
    b"B-3,09/06/2026,50.00,1.45,MID-1\n"      # Sunday
    b"B-4,09/08/2026,80.00,2.32,MID-1\n"      # Tuesday, paid Wednesday
    b"B-5,09/08/2026,40.00,1.16,MID-1\n"      # never paid
)
BATCH_MAPPING = Mapping(
    kind="settlement_batches", currency="USD", name="processor-batches",
    columns={"batch_id": "Batch ID", "closed_on": "Close Date", "gross_minor": "Gross",
             "fees_minor": "Fees", "mid": "MID"},
)
STATEMENT = (
    b"Date,Description,Amount,Ref\n"
    b"09/08/2026,MERCHANT DEPOSIT,339.85,D-1\n"   # Fri+Sat+Sun nets, after Labor Day
    b"09/09/2026,MERCHANT DEPOSIT,77.68,D-2\n"
    b"09/09/2026,MONTHLY FEE,-25.00,D-3\n"        # money out: not a deposit
    b"09/10/2026,UNKNOWN CREDIT,12.00,D-4\n"      # paid by nobody's batch
)
BANK_MAPPING = Mapping(
    kind="bank_deposits", currency="USD", name="bank-csv",
    columns={"posted_on": "Date", "description": "Description", "amount_minor": "Amount",
             "fitid": "Ref"},
)


@pytest.fixture(autouse=True)
def _read_in_september_2026(as_of):
    """The files here are dated the first days of September 2026, and the
    check reads 120 days back: read from today, these tests would start
    failing in the first days of January 2027."""
    as_of(2026, 9, 20)


def _body(data: bytes, mapping: Mapping | None = None, name: str = "f.csv", **extra) -> dict:
    out = {"filename": name, "content_base64": base64.b64encode(data).decode(), **extra}
    if mapping is not None:
        out["mapping"] = mapping.model_dump(mode="json")
    return out


@pytest.fixture
async def api():
    from okwan_api.auth import set_store
    from okwan_api.main import app

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    root = await store.create_tenant("Dealer group")
    shop = await store.create_tenant("Store 12", parent_id=root.id)
    key, _ = await store.issue_key(shop.id, scope="admin")
    root_key, _ = await store.issue_key(root.id, scope="admin")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://okwan.test") as c:
        c.headers["Authorization"] = f"Bearer {key}"
        yield c, store, shop, root_key


async def _upload(client, shop, data, mapping, name="f.csv"):
    r = await client.post(f"/v1/tenants/{shop.id}/imports", json=_body(data, mapping, name))
    assert r.status_code == 201, r.text
    return r.json()


# ── the connector, defined once ─────────────────────────────────────

KINDS = {"settlement_batches", "settlement_transactions", "bank_deposits", "delivery_lines",
         "delivery_payouts", "tax_filings"}


def test_one_resource_per_kind_each_a_read_and_nothing_that_writes():
    from okwan_query.catalog import tables_for

    files = get("files")
    assert set(files.resources) == KINDS
    assert all(op.is_read_only for _, op in files.iter_operations())
    assert files.auth.required_fields == ()
    assert {f"{t.connector}.{t.resource}" for t in tables_for(files)} == {f"files.{k}" for k in KINDS}


async def test_the_generated_mcp_tools_include_the_files():
    from okwan_mcp.generator import list_tool_names

    names = set(await list_tool_names(get("files")))
    assert names == {f"files_{k}_list" for k in KINDS}


async def test_with_no_account_nothing_is_read():
    """A local MCP server, run from the environment, knows no tenant."""
    files = get("files")
    op = files.resources["bank_deposits"].operations["list"]
    ctx = files.context({})
    assert ctx.tenant_id is None
    with pytest.raises(CredentialError, match="hosted API"):
        await op.handler(ctx, op.input_model())


async def test_the_tenant_comes_from_the_context_the_platform_builds(monkeypatch):
    store = MemoryStore(EnvMasterKey(new_key()))
    a, b = await store.create_tenant("A"), await store.create_tenant("B")
    for t, data in ((a, STATEMENT), (b, STATEMENT.replace(b"D-", b"E-"))):
        p = prepare(data, "s.csv", BANK_MAPPING)
        await store.add_import(t.id, kind="bank_deposits", filename="s.csv", format=p.format,
                               layout="bank-csv", mapping={}, sha256=p.sha256,
                               rows=p.normalized.rows, skipped=[], card_columns=[],
                               first_date=p.first_date, last_date=p.last_date)
    monkeypatch.setattr(files_connector, "_store", lambda: store)
    files = get("files")
    op = files.resources["bank_deposits"].operations["list"]
    page = await op.handler(files.context({}, tenant_id=b.id), op.input_model())
    assert {r.fitid for r in page.items} == {"E-1", "E-2", "E-3", "E-4"}
    assert isinstance(files.context({}, tenant_id=a.id), ConnectorContext)


# ── REST ────────────────────────────────────────────────────────────

async def test_rows_page_without_gaps_or_repeats(api):
    client, _, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    seen, cursor = [], None
    while True:
        body = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        r = await client.post("/v1/files/settlement_batches/list", json=body)
        assert r.status_code == 200, r.text
        page = r.json()
        seen += [x["batch_id"] for x in page["items"]]
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert seen == ["B-1", "B-2", "B-3", "B-4", "B-5"]
    first = (await client.post("/v1/files/settlement_batches/list", json={"limit": 1})).json()
    item = first["items"][0]
    assert (item["layout"], item["row"], item["file_id"].startswith("imp_")) == ("processor-batches", 2, True)
    # Net is derived when the file has no column for it: gross less fees.
    assert (item["gross_minor"], item["fees_minor"], item["net_minor"]) == (10_000, 290, 9_710)


async def test_a_window_and_a_direction(api):
    client, _, shop, _ = api
    await _upload(client, shop, STATEMENT, BANK_MAPPING)
    r = await client.post("/v1/files/bank_deposits/list",
                          json={"since": "2026-09-09", "until": "2026-09-09"})
    assert [x["fitid"] for x in r.json()["items"]] == ["D-2", "D-3"]
    assert r.json()["span_start"].startswith("2026-09-09")
    money_in = await client.post("/v1/files/bank_deposits/list", json={"direction": "in"})
    assert [x["fitid"] for x in money_in.json()["items"]] == ["D-1", "D-2", "D-4"]
    out = await client.post("/v1/files/bank_deposits/list", json={"direction": "out"})
    assert [x["amount_minor"] for x in out.json()["items"]] == [-2_500]


async def test_a_cursor_from_elsewhere_is_refused(api):
    client, _, _, _ = api
    r = await client.post("/v1/files/bank_deposits/list", json={"cursor": "bm90IGEgY3Vyc29y"})
    assert r.status_code == 422


# ── SQL ─────────────────────────────────────────────────────────────

async def test_imported_rows_are_sql_tables(api):
    client, _, shop, _ = api
    await _upload(client, shop, STATEMENT, BANK_MAPPING)
    r = await client.post("/v1/query", json={
        "sql": "SELECT fitid, amount_minor FROM files.bank_deposits WHERE amount_minor > 0 ORDER BY fitid"})
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [["D-1", 33_985], ["D-2", 7_768], ["D-4", 1_200]] or \
        r.json()["rows"] == [{"fitid": "D-1", "amount_minor": 33_985}, {"fitid": "D-2", "amount_minor": 7_768},
                             {"fitid": "D-4", "amount_minor": 1_200}]


# ── batches against deposits, on every surface ──────────────────────

def _verdicts(rows: list[dict]) -> dict[str, list]:
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["status"], []).append(r)
    return out


async def test_batches_against_deposits_over_rest(api):
    client, _, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    r = await client.get("/v1/reconciliations/batches_deposits")
    assert r.status_code == 200, r.text
    rows = _verdicts(r.json()["data"])
    matched = {m["right"]["fitid"]: m for m in rows["matched"]}
    # The weekend's three batches are one deposit, the Tuesday after Labor Day.
    assert [x["batch_id"] for x in matched["D-1"]["left"]["members"]] == ["B-1", "B-2", "B-3"]
    assert matched["D-2"]["left"]["batch_id"] == "B-4"
    assert [x["left"]["batch_id"] for x in rows["unmatched_left"]] == ["B-5"]
    # The monthly fee is money out, not a deposit; the stray credit is unexplained.
    assert [x["right"]["fitid"] for x in rows["unmatched_right"]] == ["D-4"]


async def test_the_stored_run_says_what_each_batch_and_deposit_were(api):
    """What the dashboard's Files tab reads back. A stored row named an
    unpaid batch by its id alone, and a deposit with no batch by a
    reference a statement may not even have: nothing to act on. Each now
    carries the amount and the day, a deposit's batches carry theirs, and
    still no row carries a record."""
    client, _, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    ran = await client.post(f"/v1/tenants/{shop.id}/reconciliations/batches_deposits")
    assert ran.status_code == 200, ran.text
    latest = (await client.get(f"/v1/tenants/{shop.id}/runs/latest?kind=pair&name=batches_deposits")).json()
    stored = latest["data"][shop.id]
    assert (stored["id"], stored["status"], stored["has_more"]) == (ran.json()["run_id"], "ok", False)
    rows = _verdicts(stored["rows"])
    (unpaid,) = rows["unmatched_left"]
    assert (unpaid["left_ref"], unpaid["evidence"]["record"]) == (
        "B-5", {"amount_minor": 3_884, "currency": "USD", "when": "2026-09-08"})
    (stray,) = rows["unmatched_right"]
    assert (stray["right_ref"], stray["evidence"]["record"]) == (
        "D-4", {"amount_minor": 1_200, "currency": "USD", "when": "2026-09-10"})
    paid = {m["right_ref"]: m["evidence"] for m in rows["matched"]}
    assert paid["D-1"]["members"] == [
        {"ref": "B-1", "amount_minor": 9_710, "when": "2026-09-04"},
        {"ref": "B-2", "amount_minor": 19_420, "when": "2026-09-05"},
        {"ref": "B-3", "amount_minor": 4_855, "when": "2026-09-06"}]
    assert paid["D-1"]["dated"] == {"left": None, "right": "2026-09-08"}      # the Tuesday after Labor Day
    assert paid["D-2"]["dated"] == {"left": "2026-09-08", "right": "2026-09-09"}
    kept = json.dumps(stored["rows"])
    assert all(words not in kept for words in ("MERCHANT DEPOSIT", "UNKNOWN CREDIT", "description", "gross_minor"))


async def test_batches_against_deposits_as_a_sql_table(api):
    client, _, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    r = await client.post("/v1/query", json={
        "sql": "SELECT status, count(*) AS n FROM recon.batches_deposits GROUP BY status ORDER BY status"})
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body["rows"]
    counts = {row[0]: row[1] for row in rows} if rows and isinstance(rows[0], list) else \
        {row["status"]: row["n"] for row in rows}
    assert counts == {"matched": 2, "unmatched_left": 1, "unmatched_right": 1}


async def test_batches_against_deposits_over_hosted_mcp(api, monkeypatch):
    from okwan_query.mcp_http import build_server

    client, _, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    key = client.headers["Authorization"].split()[1]

    ctx = SimpleNamespace(headers={"authorization": f"Bearer {key}"})

    tools = build_server()._tool_manager
    out = await tools.get_tool("okwan_reconcile").fn(name="batches_deposits", ctx=ctx)
    assert out["summary"]["matched"] == 2, out


# ── saved mappings and the preview ──────────────────────────────────

async def test_a_mapping_is_saved_once_and_used_by_name(api):
    client, _, shop, _ = api
    base = f"/v1/tenants/{shop.id}"
    r = await client.put(f"{base}/import-layouts/bank-csv", json=BANK_MAPPING.model_dump(mode="json"))
    assert r.status_code == 200, r.text
    listed = (await client.get(f"{base}/import-layouts")).json()["layouts"]
    assert [(x["name"], x["kind"]) for x in listed] == [("bank-csv", "bank_deposits")]
    up = await client.post(f"{base}/imports", json=_body(STATEMENT, name="s.csv", layout="bank-csv"))
    assert up.status_code == 201, up.text
    assert up.json()["layout"] == "bank-csv"
    both = _body(STATEMENT, BANK_MAPPING, layout="bank-csv")
    assert (await client.post(f"{base}/imports", json=both)).status_code == 400
    neither = _body(STATEMENT)
    assert (await client.post(f"{base}/imports", json=neither)).status_code == 400
    missing = _body(STATEMENT, layout="nope")
    assert (await client.post(f"{base}/imports", json=missing)).status_code == 404
    assert (await client.delete(f"{base}/import-layouts/bank-csv")).status_code == 204
    assert (await client.get(f"{base}/import-layouts")).json()["layouts"] == []
    assert (await client.delete(f"{base}/import-layouts/bank-csv")).status_code == 404


async def test_a_saved_mapping_must_be_valid(api):
    client, _, shop, _ = api
    bad = {"kind": "bank_deposits", "columns": {}, "currency": "dollars"}
    r = await client.put(f"/v1/tenants/{shop.id}/import-layouts/x", json=bad)
    assert r.status_code == 422


async def test_the_preview_shows_columns_and_a_sample_and_suggests_a_mapping(api):
    client, store, shop, _ = api
    data = (b"Batch ID,Closed On,Gross,Fees,Card Number,MID\n"
            b"B-1,11/02/2026,100.00,2.90,************1234,MID-1\n")
    r = await client.post(f"/v1/tenants/{shop.id}/imports/preview", json=_body(data, name="b.csv"))
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["format"] == "csv" and p["rows"] == 1
    assert p["header"] == ["Batch ID", "Closed On", "Gross", "Fees", "Card Number", "MID"]
    assert p["card_columns"] == ["Card Number"]
    # A card column shows its last four digits at most, masked or not.
    assert p["sample"][0][4] == "•••• 1234" and "************" not in r.text
    assert p["suggested"]["settlement_batches"]["batch_id"] == "Batch ID"
    assert p["suggested"]["settlement_batches"]["closed_on"] == "Closed On"
    assert p["suggested"]["settlement_batches"]["gross_minor"] == "Gross"
    assert {f["field"] for f in p["fields"]["bank_deposits"]} >= {"posted_on", "amount_minor", "credit"}
    assert await store.list_imports(shop.id) == []  # a preview stores nothing


async def test_a_preview_refuses_a_card_number_like_an_upload(api):
    client, _, shop, _ = api
    data = b"Date,Note\n11/02/2026,4111 1111 1111 1111\n"
    r = await client.post(f"/v1/tenants/{shop.id}/imports/preview", json=_body(data))
    assert r.status_code == 422 and "4111" not in r.text


def test_suggestions_are_generic_not_presets():
    assert suggest("settlement_batches", ["Batch ID", "Close Date", "Gross Sales", "Net", "Thing"]) == {
        "batch_id": "Batch ID", "gross_minor": "Gross Sales", "net_minor": "Net"}
    # OFX is a standard, not a processor: its columns map one way.
    assert suggest("bank_deposits", ["TRNTYPE", "DTPOSTED", "TRNAMT", "FITID", "NAME", "MEMO",
                                     "CHECKNUM", "REFNUM", "CURRENCY", "ACCTID_LAST4"]) == {
        "type": "TRNTYPE", "posted_on": "DTPOSTED", "amount_minor": "TRNAMT", "fitid": "FITID",
        "description": "NAME", "currency": "CURRENCY", "account_last4": "ACCTID_LAST4"}
    # Two columns that could both be the amount: neither is guessed.
    assert "amount_minor" not in suggest("bank_deposits", ["Amount USD", "Amount EUR"])


# ── status in the reconciliation listing ────────────────────────────

async def test_the_listing_says_what_each_source_holds(api):
    client, _, shop, _ = api
    r = await client.get("/v1/reconciliations")
    status = {x["name"]: x for x in r.json()["data"]}["batches_deposits"]["status"]
    assert status["runnable"] is False
    assert [s["imports"]["files"] for s in status["sources"]] == [0, 0]
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    await _upload(client, shop, STATEMENT, BANK_MAPPING, "statement.csv")
    await client.get("/v1/reconciliations/batches_deposits")
    r = await client.get("/v1/reconciliations")
    status = {x["name"]: x for x in r.json()["data"]}["batches_deposits"]["status"]
    left, right = status["sources"]
    assert status["runnable"] is True
    assert left["source"] == "files.settlement_batches"
    assert left["imports"] == {"files": 1, "rows": 5, "first_date": "2026-09-04",
                               "last_date": "2026-09-08"}
    assert right["imports"]["first_date"] == "2026-09-08"
    assert status["last_run"]["status"] == "ok" and status["last_ok_at"]
    stripe = {x["name"]: x for x in r.json()["data"]}["shopify_stripe"]["status"]
    assert stripe["runnable"] is False and "stripe.secret_key" in stripe["missing_credentials"]


async def test_hosted_mcp_lists_the_same_status(api):
    from okwan_query.mcp_http import build_server

    client, _, shop, _ = api
    await _upload(client, shop, BATCHES, BATCH_MAPPING)
    key = client.headers["Authorization"].split()[1]

    ctx = SimpleNamespace(headers={"authorization": f"Bearer {key}"})

    out = await build_server()._tool_manager.get_tool("okwan_list_reconciliations").fn(ctx=ctx)
    entry = {x["name"]: x for x in out["reconciliations"]}["batches_deposits"]
    assert entry["runnable"] is False  # batches, but no statement yet
    assert [s["imports"]["files"] for s in entry["sources"]] == [1, 0]


def test_net_is_derived_only_when_the_file_has_no_column_for_it():
    with_net = Mapping(kind="settlement_batches", currency="USD",
                       columns={"batch_id": "B", "closed_on": "D", "gross_minor": "G", "net_minor": "N",
                                "fees_minor": "F", "chargebacks_minor": "C", "adjustments_minor": "A"})
    data = b"B,D,G,F,C,A,N\nB1,11/02/2026,100.00,3.00,10.00,-1.00,90.00\n"
    assert prepare(data, "x.csv", with_net).normalized.rows[0][2]["net_minor"] == 9_000
    without = with_net.model_copy(update={"columns": {k: v for k, v in with_net.columns.items()
                                                      if k != "net_minor"}})
    # 100.00 − 3.00 fees − 10.00 chargebacks − 1.00 adjustment
    assert prepare(data, "x.csv", without).normalized.rows[0][2]["net_minor"] == 8_600
    assert date(2026, 11, 2)
