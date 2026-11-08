"""Avalara AvaTax: flattening, the walk, period sums (E6), and recorded against filed.

Fixtures are shaped like the rows AvaTax returned live on 2026-11-08 (§3.5
probe: a trial account, eight test sales in Wyoming), and the sample
returns in docs/samples/imports/tax. A fake transport replays pages and
records what was asked, so the walk is tested without a network.

What would fail silently: an AvaTax amount read through float; a customer's
email, business id or exemption number carried into a row; a status or
type that closes the filter's quote; a walk that asks for a page past the
end because the last page still carries @nextLink; a sale counted in two
overlapping returns; a return's correction for an earlier month read as a
shortfall; a sale in a state no return covers matched to another state's
return.
"""
from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import okwan_avalara.connector  # noqa: F401  (registers the connector)
import okwan_files.connector  # noqa: F401  (registers the connector)
import pytest
from okwan_avalara import connector as avalara_connector
from okwan_avalara.auth import CLIENT, HOSTS, AvalaraAuth, company_code, host_for
from okwan_avalara.connector import _decode, _encode, _quoted, list_transactions
from okwan_avalara.schemas import ListTransactionsIn, Transaction
from okwan_core import CredentialError, get
from okwan_core.connector import ConnectorContext
from okwan_files import Mapping, prepare
from okwan_query.catalog import tables_for
from okwan_recon import PeriodGroup, Reconciliation, ResourceRef, declarations
from okwan_recon.engine import match

CREDS = {"account_id": "2006584788", "license_key": "test-key", "company_code": "GlobalTechStartupLLC",
         "environment": "production"}
SAMPLES = Path(__file__).resolve().parents[1] / "docs" / "samples" / "imports" / "tax"
RETURNS = Mapping(kind="tax_filings", currency="USD", name="State returns", columns={
    "jurisdiction": "State", "period_start": "Period start", "period_end": "Period end",
    "taxable_minor": "Taxable sales", "tax_minor": "Tax collected", "adjustments_minor": "Prior period adjustments",
    "vendor_discount_minor": "Vendor discount", "paid_minor": "Amount paid", "filed_on": "Filed on",
    "return_id": "Confirmation"})


def doc(code: str, day: str, amount: float, tax: float, *, region: str = "WY", status: str = "Committed",
        type_: str = "SalesInvoice", **extra: Any) -> dict:
    """One transaction as AvaTax lists it, with the fields a row must drop."""
    return {"id": abs(hash(code)) % 10**15, "code": code, "companyId": 6691709, "type": type_, "status": status,
            "date": day, "taxDate": day, "region": region, "country": "US", "currencyCode": "USD",
            "totalAmount": amount, "totalTax": tax, "totalTaxable": amount, "totalExempt": 0.0,
            "totalTaxCalculated": tax, "locked": False, "modifiedDate": f"{day}T12:05:02.783Z",
            "customerCode": "OKWAN-TEST", "email": "buyer@example.com", "businessIdentificationNo": "99-1234567",
            "exemptNo": "EX-55", "lines": [], "summary": [], **extra}


#: Recorded live: the second test sale dated on the Wyoming nexus date.
LIVE = doc("OKW-T-1007", "2026-11-08", 47.25, 2.36)

#: What AvaTax committed: August, September and October in Wyoming, a refund,
#: and a Colorado sale no return covers.
DOCS = [
    doc("A-801", "2026-08-07", 375.00, 15.00), doc("A-802", "2026-08-21", 200.00, 8.00),
    doc("S-901", "2026-09-03", 300.00, 12.00), doc("S-902", "2026-09-15", 450.00, 18.00),
    doc("S-903", "2026-09-30", 250.00, 10.00),
    doc("O-1001", "2026-10-02", 500.00, 20.00), doc("O-1002", "2026-10-18", 412.50, 16.50),
    doc("R-1003", "2026-10-25", -50.00, -2.00, type_="ReturnInvoice"),
    doc("CO-1", "2026-10-12", 141.38, 4.10, region="CO"),
]


def _rows(raw: list[dict]) -> list[dict]:
    return [Transaction.model_validate(r).model_dump(mode="json") for r in raw]


def _returns() -> list[dict]:
    name = "sales-tax-returns-2026.csv"
    rows = prepare((SAMPLES / name).read_bytes(), name, RETURNS).normalized.rows
    return [{**f, "file_id": name, "row": n, "layout": RETURNS.name} for n, _day, f in rows]


# ── flattening ──────────────────────────────────────────────────────

def test_a_live_transaction_flattens_to_cents_and_keeps_no_customer_detail():
    t = Transaction.model_validate(LIVE)
    assert (t.total_amount_minor, t.total_tax_minor, t.tax_calculated_minor, t.region) == (4725, 236, 236, "WY")
    assert (t.committed, t.tax_override_minor, t.date) == (True, 0, date(2026, 11, 8))
    dumped = json.dumps(t.model_dump(mode="json"))
    for kept_out in ("buyer@example.com", "99-1234567", "EX-55", "OKWAN-TEST"):
        assert kept_out not in dumped


def test_decimals_never_round_through_float():
    # 299.10 * 100 is 29909.999… in binary floating point.
    assert Transaction.model_validate(doc("X", "2026-11-08", 299.10, 14.96)).total_amount_minor == 29910


def test_an_override_is_the_tax_charged_less_the_tax_calculated():
    t = Transaction.model_validate({**LIVE, "totalTax": 2.00})
    assert t.tax_override_minor == -36


# ── the credentials ─────────────────────────────────────────────────

def test_the_environment_picks_the_host():
    assert host_for({"environment": "production"}) == HOSTS["production"] == "https://rest.avatax.com"
    assert host_for({"environment": "Sandbox"}) == "https://sandbox-rest.avatax.com"
    with pytest.raises(CredentialError):
        host_for({"environment": "live"})


@pytest.mark.parametrize("bad", ["", "../companies", "Global Tech", "A'B", "x" * 60])
def test_a_company_code_that_could_steer_the_path_is_refused(bad):
    with pytest.raises(CredentialError):
        company_code({"company_code": bad})


def test_every_request_is_basic_auth_and_names_the_client():
    assert AvalaraAuth().required_fields == ("account_id", "license_key", "company_code", "environment")
    request = next(AvalaraAuth().bind(CREDS).auth_flow(httpx.Request("GET", "https://rest.avatax.com/api/v2/companies")))
    assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"2006584788:test-key").decode()
    assert request.headers["X-Avalara-Client"] == CLIENT
    with pytest.raises(CredentialError):
        AvalaraAuth().bind({**CREDS, "account_id": "not-a-number"})


def test_a_filter_word_cannot_close_its_quote():
    assert _quoted("Committed") == "'Committed'"
    for bad in ("Committed' or '1'='1", "Saved)", ""):
        with pytest.raises(ValueError):
            _quoted(bad)


# ── the walk, against a fake transport ──────────────────────────────

class _FakeClient:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = list(pages)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, dict(params or {})))
        return self.pages.pop(0) if self.pages else {"value": [], "@recordsetCount": 0}

    async def aclose(self) -> None:
        pass


def _page(raw: list[dict], total: int, skip: int) -> dict:
    out = {"@recordsetCount": total, "value": raw}
    out["@nextLink"] = f"/api/v2/companies/X/transactions?$skip={skip + len(raw)}"  # even on the last page
    return out


async def test_the_walk_filters_sorts_and_pages_by_offset_until_the_count():
    client = _FakeClient([_page(DOCS[:4], 9, 0), _page(DOCS[4:8], 9, 4), _page(DOCS[8:], 9, 8)])
    ctx = ConnectorContext(client=client, credentials=CREDS)
    params = {"status": "Committed", "type": "SalesInvoice,ReturnInvoice", "limit": 4,
              "start_date": datetime(2026, 8, 1, tzinfo=UTC), "end_date": datetime(2026, 10, 31, tzinfo=UTC)}
    seen, cursor = [], None
    while True:
        page = await list_transactions(ctx, ListTransactionsIn(**params, cursor=cursor))
        seen += [t.code for t in page.items]
        cursor = page.next_cursor
        if not page.has_more:
            break
    assert seen == [d["code"] for d in DOCS] and len(client.calls) == 3  # no empty fourth page
    path, first = client.calls[0]
    assert path == "/api/v2/companies/GlobalTechStartupLLC/transactions"
    assert first["$filter"] == ("date between '2026-08-01' and '2026-10-31' and status eq 'Committed' and "
                                "(type eq 'SalesInvoice' or type eq 'ReturnInvoice')")
    assert (first["$orderBy"], first["$top"], first["$skip"]) == ("date ASC, id ASC", 4, 0)
    assert [c[1]["$skip"] for c in client.calls] == [0, 4, 8]
    assert {c[1]["$filter"] for c in client.calls} == {first["$filter"]}  # the window pinned


def test_a_walk_cursor_round_trips_and_a_bad_one_restarts():
    state = {"s": "2026-08-01", "e": "2026-10-31", "k": 8}
    assert _decode(_encode(state)) == state
    assert _decode("not-a-cursor!") is None
    assert _decode(_encode({"s": "2026-11-01", "e": "2026-10-01", "k": 0})) is None


# ── the one definition ──────────────────────────────────────────────

def test_two_resources_every_one_a_read_and_each_a_sql_table():
    avalara = get("avalara")
    assert set(avalara.resources) == {"companies", "transactions"}
    assert all(op.is_read_only for _, op in avalara.iter_operations())
    tables = {f"{t.connector}.{t.resource}": dict(t.columns) for t in tables_for(avalara)}
    assert tables["avalara.transactions"]["total_tax_minor"] == "BIGINT"
    assert tables["avalara.transactions"]["committed"] == "BOOLEAN"
    assert not {"email", "business_identification_no", "exempt_no", "customer_code"} & set(tables["avalara.transactions"])


async def test_the_generated_mcp_tools_are_the_two_lists():
    from okwan_mcp.generator import list_tool_names

    assert set(await list_tool_names(get("avalara"))) == {"avalara_companies_list", "avalara_transactions_list"}


def test_the_check_reads_only_and_resolves():
    declarations.avatax_collected_filed.validate_against_registry()


# ── period sums (E6) ────────────────────────────────────────────────

def _spec(**rule: Any) -> Reconciliation:
    return Reconciliation(
        name="periods", left=ResourceRef(connector="files", resource="tax_filings"),
        right=ResourceRef(connector="avalara", resource="transactions"),
        keys=[PeriodGroup(start="period_start", end="period_end", timestamp_many="date",
                          share=(("jurisdiction", "region"),), **rule)],
        amount=declarations.avatax_collected_filed.amount)


def _filing(state: str, first: str, last: str, tax: int, **extra: Any) -> dict:
    return {"jurisdiction": state, "period_start": first, "period_end": last, "tax_minor": tax,
            "currency": "USD", **extra}


def test_a_period_takes_every_record_inside_it_and_nothing_outside():
    result = match(_spec(), [_filing("wy", "2026-09-01", "2026-09-30", 4000)], _rows(DOCS))
    (pair,) = result.matched
    assert [m["code"] for m in pair.members] == ["S-901", "S-902", "S-903"]  # 1 and 30 September included
    assert pair.discrepancy_minor == 0 and pair.evidence["period"] == {"start": "2026-09-01", "end": "2026-09-30"}
    assert pair.evidence["share"] == {"jurisdiction": "wy"}  # any case
    # August predates the only return imported and October's cannot be due
    # yet: both wait. Colorado has no return at all: a real miss.
    assert [r["code"] for r in result.unmatched_right] == ["CO-1"]
    waiting = {u.record["code"]: u.reason for u in result.unverifiable_right}
    assert set(waiting) == {"A-801", "A-802", "O-1001", "O-1002", "R-1003"}
    assert waiting["O-1001"].startswith("after the latest period imported for wy (to 2026-09-30)")
    assert waiting["A-801"].startswith("before the earliest period imported for wy (from 2026-09-01)")


def test_overlapping_periods_never_count_a_record_twice():
    month = _filing("WY", "2026-09-01", "2026-09-30", 4000)
    quarter = _filing("WY", "2026-07-01", "2026-09-30", 6300)
    result = match(_spec(), [month, quarter], _rows(DOCS))
    by = {p.left["period_start"]: p for p in result.matched}
    assert sorted(m["code"] for m in by["2026-07-01"].members) == ["A-801", "A-802", "S-901", "S-902", "S-903"]
    # The quarter came first and took September, so the month's period is
    # empty: it reports 40.00 with nothing behind it.
    assert (by["2026-09-01"].members, by["2026-09-01"].discrepancy_minor) == ([], 4000)


def test_an_empty_period_sums_to_zero():
    nothing = [_filing("WY", "2026-06-01", "2026-06-30", 0), _filing("WY", "2026-07-01", "2026-07-31", 1500)]
    by = {p.left["period_start"]: p for p in match(_spec(), nothing, _rows(DOCS)).matched}
    assert by["2026-06-01"].discrepancy_minor == 0  # a nil return for a quiet month agrees
    assert by["2026-07-01"].discrepancy_minor == 1500 and by["2026-07-01"].is_unexplained  # tax with no sales


def test_a_month_missing_between_two_returns_is_a_finding_not_a_wait():
    gap = [_filing("WY", "2026-08-01", "2026-08-31", 2300), _filing("WY", "2026-10-01", "2026-10-31", 3450)]
    result = match(_spec(), gap, _rows(DOCS))
    assert {r["code"] for r in result.unmatched_right} == {"S-901", "S-902", "S-903", "CO-1"}
    assert not result.unverifiable_right


def test_a_period_with_more_records_than_allowed_is_ambiguous():
    result = match(_spec(max_members=2), [_filing("WY", "2026-09-01", "2026-09-30", 4000)], _rows(DOCS))
    assert result.ambiguous and not result.matched


# ── recorded against filed ──────────────────────────────────────────

def test_each_return_against_what_the_engine_committed_for_its_state_and_days():
    result = match(declarations.avatax_collected_filed, _returns(), _rows(DOCS))
    by = {p.left["period_start"]: p for p in result.matched}
    august, september, october = by["2026-08-01"], by["2026-09-01"], by["2026-10-01"]
    assert september.discrepancy_minor == 0
    # August carried a 2.00 correction for July: explained, not a shortfall.
    assert (august.discrepancy_minor, august.explained_by) == (200, "return_adjustment")
    # October reported 31.50; the engine committed 36.50 less a 2.00 refund.
    assert (october.discrepancy_minor, october.explained_by) == (-300, None) and october.is_unexplained
    assert [r["code"] for r in result.unmatched_right] == ["CO-1"]  # Colorado: no return imported
    assert not result.unmatched_left and not result.ambiguous


# ── every surface, as a provisioned tenant ──────────────────────────

@pytest.fixture
async def tenant(monkeypatch):
    from okwan_api.auth import set_store
    from okwan_api.main import app
    from okwan_vault import EnvMasterKey, MemoryStore, new_key

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    shop = await store.create_tenant("Corner Café")
    key, _ = await store.issue_key(shop.id)
    for field, value in CREDS.items():
        await store.put_credential(shop.id, "avalara", field, value)
    seen: list[dict] = []

    def factory(connector, credentials):
        seen.append(dict(credentials))
        return ConnectorContext(client=_FakeClient([_page(DOCS, len(DOCS), 0)]), credentials=credentials)

    monkeypatch.setattr(avalara_connector.avalara, "context_factory", factory)
    name = "sales-tax-returns-2026.csv"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        c.headers["Authorization"] = f"Bearer {key}"
        r = await c.post(f"/v1/tenants/{shop.id}/imports", json={
            "filename": name, "content_base64": base64.b64encode((SAMPLES / name).read_bytes()).decode(),
            "mapping": RETURNS.model_dump(mode="json")})
        assert r.status_code == 201, r.text
        yield c, key, seen


async def test_sales_tax_is_queryable_for_a_provisioned_tenant_on_every_surface(tenant):
    client, key, seen = tenant
    listed = await client.post("/v1/avalara/transactions/list", json={"status": "Committed"})
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == len(DOCS) and all(c == CREDS for c in seen)

    run = await client.get("/v1/reconciliations/avatax_collected_filed")
    assert run.status_code == 200, run.text
    verdicts = {((r.get("left") or {}).get("period_start") or (r.get("right") or {}).get("code")): r["status"]
                for r in run.json()["data"]}
    assert verdicts == {"2026-08-01": "matched_explained", "2026-09-01": "matched",
                        "2026-10-01": "matched_discrepant", "CO-1": "unmatched_right"}

    sql = await client.post("/v1/query", json={
        "sql": "SELECT status, count(*) AS n FROM recon.avatax_collected_filed GROUP BY status ORDER BY status"})
    assert {r["status"]: r["n"] for r in sql.json()["rows"]} == {
        "matched": 1, "matched_discrepant": 1, "matched_explained": 1, "unmatched_right": 1}

    from okwan_query.mcp_http import build_server
    tool = build_server()._tool_manager.get_tool("okwan_reconcile")
    out = await tool.fn(name="avatax_collected_filed", ctx=SimpleNamespace(headers={"authorization": f"Bearer {key}"}))
    assert out["summary"]["matched"] == 3
