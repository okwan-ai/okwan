"""Reconciliations are SQL tables (§3, the one-definition rule).

Each registered reconciliation is `recon.<name>` and each fold is
`recon.across_<name>` in the same catalog as connector tables, on both
SQL surfaces: POST /v1/query and the hosted MCP's okwan_query. Naming one
in a query runs it live, as the caller, with the caller's credentials,
and stores the run like any other (surface `sql`).

What would fail silently: a view the catalog lists but the session cannot
load, a run with the server's credentials instead of the caller's, a
verdict that differs from REST because SQL capped it differently, a run
that is not stored, and a table that claims to be queryable when a rail
it reads has no credentials.

Only transport is fake: rows go through the real declarations, match
engine and fold.
"""
from __future__ import annotations

from datetime import UTC, datetime

import okwan_paypal.connector  # noqa: F401
import okwan_postgres.connector  # noqa: F401
import okwan_recon.declarations
import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401
import pytest
from fastapi.testclient import TestClient
from okwan_core import UpstreamError
from okwan_query import QuerySession, catalog, find
from okwan_query.catalog import missing_credentials
from okwan_recon import across, runner
from okwan_recon.coverage import Coverage
from okwan_recon.emitters.duckdb_view import ACROSS_COLUMNS, COLUMNS
from okwan_vault import EnvMasterKey, MemoryStore, new_key

AT = "2026-08-27T15:00:00Z"
EPOCH = int(datetime(2026, 8, 27, 15, 1, tzinfo=UTC).timestamp())

VAULT = {"stripe": {"secret_key": "sk_caller"},
         "paypal": {"client_id": "pp_caller", "client_secret": "pp_caller_s"},
         "shopify": {"access_token": "shpat_caller", "shop_domain": "kofi.myshopify.com"}}


def order(name: str, total: int) -> dict:
    return {"name": name, "currency": "USD", "created_at": AT,
            "total_price_minor": total, "total_received_minor": total,
            "total_refunded_minor": 0, "net_payment_minor": total}


ROWS = {
    "shopify": [order("#1002", 99900), order("#1003", 15000)],
    "paypal": [{"invoice_id": "#1002", "currency": "USD", "initiated_at": AT,
                "amount_minor": 99900, "fee_minor": -300, "net_minor": 99600}],
    "stripe": [{"order_ref": "#1002", "currency": "usd", "created": EPOCH, "amount": 99900,
                "amount_refunded": 0, "fee_minor": 300, "net_minor": 99600},
               {"order_ref": "#1003", "currency": "usd", "created": EPOCH, "amount": 15000,
                "amount_refunded": 0, "fee_minor": 300, "net_minor": 14700}],
}


def resolver_of(vault: dict) -> callable:
    def resolve(connector: str, fields) -> dict[str, str]:
        return {f: vault.get(connector, {}).get(f, "") for f in fields}
    return resolve


@pytest.fixture
def rails(monkeypatch):
    """Fake transport for both runners; records the credentials used."""
    seen: dict[str, dict[str, str]] = {}

    async def side(ref, resolver, cap, overrides):
        from okwan_core import get

        seen[ref.connector] = dict(resolver(ref.connector, get(ref.connector).auth.required_fields))
        rows = ROWS[ref.connector]
        return rows, Coverage(source=ref.qualified, records=len(rows), cap=cap, truncated=False)

    monkeypatch.setattr(across, "fetch_side", side)
    monkeypatch.setattr(runner, "fetch_side", side)
    return seen


# ── the catalog ─────────────────────────────────────────────────────

def test_every_reconciliation_is_a_table_with_its_view_columns():
    names = {t.qualified for t in catalog()}
    assert {"recon.shopify_stripe", "recon.shopify_paypal", "recon.shopify_orders",
            "recon.across_rails"} <= names
    assert find("recon.shopify_stripe").columns == COLUMNS
    assert find("recon.across_rails").columns == ACROSS_COLUMNS


def test_a_view_is_queryable_only_when_every_system_it_reads_is_connected():
    """A fold reads three systems: each missing field is named with its
    connector, so an agent knows which connection to ask for."""
    table = find("recon.across_rails")
    missing = missing_credentials(table, resolver_of({"stripe": VAULT["stripe"]}))
    assert "stripe.secret_key" not in missing
    assert {"paypal.client_id", "paypal.client_secret", "shopify.access_token"} <= set(missing)
    assert missing_credentials(table, resolver_of(VAULT)) == ()


# ── the session ─────────────────────────────────────────────────────

async def test_naming_a_reconciliation_runs_it_and_joins_like_any_table(rails):
    runs: list = []

    async def on_run(spec, started, *, result=None, error=None):
        runs.append((spec.name, result is not None, error))

    s = QuerySession(resolver=resolver_of(VAULT), on_run=on_run)
    try:
        out = await s.query(
            "SELECT outcome, count(*) AS n FROM recon.across_rails GROUP BY outcome ORDER BY outcome"
        )
        assert {r["outcome"]: r["n"] for r in out["rows"]} == {"collected": 1, "collected_twice": 1}
        assert out["sources"] == {"recon.across_rails": 2}
        # The caller's credentials, not the deployment's.
        assert rails["stripe"] == {"secret_key": "sk_caller"}
        assert runs == [("rails", True, None)]

        pair = await s.query(
            "SELECT status, json_extract_string(right_record, '$.order_ref') AS ref "
            "FROM recon.shopify_stripe ORDER BY ref"
        )
        assert [(r["status"], r["ref"]) for r in pair["rows"]] == [
            ("matched_explained", "#1002"), ("matched_explained", "#1003")]
    finally:
        s.close()


async def test_sql_gives_the_same_verdicts_as_rest(rails):
    """Same declaration, same runner, same caps: SQL is another view of
    the REST result, never a second opinion."""
    spec = okwan_recon.declarations.rails
    direct = await across.run_across(spec, resolver_of(VAULT))
    s = QuerySession(resolver=resolver_of(VAULT), max_records=1)  # a query limit is not a run cap
    try:
        out = await s.query("SELECT outcome FROM recon.across_rails")
        assert sorted(r["outcome"] for r in out["rows"]) == sorted(r["outcome"] for r in direct.rows())
    finally:
        s.close()


async def test_a_failed_run_is_reported_and_raised(monkeypatch):
    async def down(ref, resolver, cap, overrides):
        raise UpstreamError(503, "stripe is down")

    monkeypatch.setattr(across, "fetch_side", down)
    runs: list = []

    async def on_run(spec, started, *, result=None, error=None):
        runs.append((spec.name, result, type(error).__name__))

    s = QuerySession(resolver=resolver_of(VAULT), on_run=on_run)
    try:
        with pytest.raises(UpstreamError):
            await s.query("SELECT * FROM recon.across_rails")
        assert runs == [("rails", None, "UpstreamError")]
    finally:
        s.close()


# ── the REST surface, end to end ────────────────────────────────────

@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
def client(store):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    set_store(store)
    return TestClient(app)


async def _tenant_with_key(store, vault: dict) -> tuple:
    t = await store.create_tenant("Kofi's Store")
    for connector, fields in vault.items():
        for field, value in fields.items():
            await store.put_credential(t.id, connector, field, value)
    key, _ = await store.issue_key(t.id)
    return t, {"Authorization": f"Bearer {key}"}


async def test_post_query_runs_a_fold_and_stores_the_run(client, store, rails):
    tenant, auth = await _tenant_with_key(store, VAULT)
    r = client.post("/v1/query", json={"sql": "SELECT outcome FROM recon.across_rails"}, headers=auth)
    assert r.status_code == 200, r.text
    assert sorted(row["outcome"] for row in r.json()["rows"]) == ["collected", "collected_twice"]
    runs = await store.list_runs(tenant.id)
    assert [(x.name, x.surface, x.status) for x in runs] == [("rails", "sql", "ok")]


async def test_the_table_listing_names_what_each_view_still_needs(client, store):
    _, auth = await _tenant_with_key(store, {"stripe": VAULT["stripe"]})
    tables = {t["name"]: t for t in client.get("/v1/query/tables", headers=auth).json()["tables"]}
    fold = tables["recon.across_rails"]
    assert fold["queryable"] is False
    assert "shopify.access_token" in fold["missing_credentials"]
    assert "stripe.secret_key" not in fold["missing_credentials"]


async def test_a_view_over_a_missing_connection_answers_401_not_a_run(client, store, rails):
    tenant, auth = await _tenant_with_key(store, {"stripe": VAULT["stripe"]})
    r = client.post("/v1/query", json={"sql": "SELECT * FROM recon.across_rails"}, headers=auth)
    assert r.status_code == 401
    assert "shopify.access_token" in r.json()["detail"]
    assert rails == {}  # nothing was fetched
    assert await store.list_runs(tenant.id) == []
