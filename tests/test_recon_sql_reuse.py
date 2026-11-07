"""Reused runs and a rate limit for `recon.*` SQL tables (recon_reuse.py).

An agent querying `recon.across_rails` in a loop would re-run the check,
three provider reads at up to 5,000 records each, every few seconds. So a
query reads this account's run of the same reconciliation from the last
five minutes unless it asks for `fresh`, and live runs through SQL are
limited to 12 a minute per account.

What would fail silently: a reused result served to another tenant; a
reused result surviving a credential the customer just fixed; a failed run
cached and served as if it were a result; `fresh` ignored; a query counted
against the plan when it read nothing upstream; the limit counting reused
reads, or refusing a query only after it spent a run; the MCP tool
refusing without saying when to retry.

Only transport is fake: rows go through the real declarations, engine,
fold, store and routes.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import okwan_paypal.connector  # noqa: F401
import okwan_recon.declarations  # noqa: F401
import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector  # noqa: F401
import pytest
from fastapi.testclient import TestClient
from okwan_api.ratelimit import SQL_RECON_TENANT
from okwan_api.recon_reuse import cache
from okwan_core import UpstreamError
from okwan_recon import across, runner
from okwan_recon.coverage import Coverage
from okwan_vault import EnvMasterKey, MemoryStore, new_key

AT = "2026-08-27T15:00:00Z"
EPOCH = int(datetime(2026, 8, 27, 15, 1, tzinfo=UTC).timestamp())
VAULT = {"stripe": {"secret_key": "sk_caller"},
         "paypal": {"client_id": "pp_caller", "client_secret": "pp_caller_s"},
         "shopify": {"access_token": "shpat_caller", "shop_domain": "kofi.myshopify.com"}}
ROWS = {
    "shopify": [{"name": "#1002", "currency": "USD", "created_at": AT, "total_price_minor": 99900,
                 "total_received_minor": 99900, "total_refunded_minor": 0, "net_payment_minor": 99900}],
    "paypal": [{"invoice_id": "#1002", "currency": "USD", "initiated_at": AT,
                "amount_minor": 99900, "fee_minor": -300, "net_minor": 99600}],
    "stripe": [{"order_ref": "#1002", "currency": "usd", "created": EPOCH, "amount": 99900,
                "amount_refunded": 0, "fee_minor": 300, "net_minor": 99600}],
}
FOLD = "SELECT outcome FROM recon.across_rails"


@pytest.fixture
def rails(monkeypatch):
    """Fake transport; counts reads per connector."""
    reads: dict[str, int] = {}

    async def side(ref, resolver, cap, overrides):
        reads[ref.connector] = reads.get(ref.connector, 0) + 1
        rows = ROWS[ref.connector]
        return rows, Coverage(source=ref.qualified, records=len(rows), cap=cap, truncated=False)

    monkeypatch.setattr(across, "fetch_side", side)
    monkeypatch.setattr(runner, "fetch_side", side)
    return reads


@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
def client(store):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    set_store(store)
    return TestClient(app)


async def _tenant(store, name: str = "Kofi's Store"):
    t = await store.create_tenant(name)
    for connector, fields in VAULT.items():
        for field, value in fields.items():
            await store.put_credential(t.id, connector, field, value)
    key, _ = await store.issue_key(t.id)
    return t, {"Authorization": f"Bearer {key}"}


def _metered(store, tenant) -> int:
    return sum(n for (tid, _, _), n in store._usage.items() if tid == tenant.id)


def _query(client, auth, sql: str = FOLD, **body):
    return client.post("/v1/query", json={"sql": sql, **body}, headers=auth)


# ── reuse ───────────────────────────────────────────────────────────

async def test_a_second_query_reads_the_first_run_and_is_free(client, store, rails):
    tenant, auth = await _tenant(store)
    first = _query(client, auth)
    assert first.status_code == 200, first.text
    run = first.json()["runs"]["recon.across_rails"]
    assert run["reused"] is False and run["run_id"].startswith("run_")
    metered = _metered(store, tenant)

    second = _query(client, auth)
    assert second.status_code == 200
    again = second.json()["runs"]["recon.across_rails"]
    assert again["reused"] is True and again["run_id"] == run["run_id"]
    assert again["age_seconds"] >= 0
    assert second.json()["rows"] == first.json()["rows"]
    assert rails == {"shopify": 1, "paypal": 1, "stripe": 1}  # read once
    assert len(await store.list_runs(tenant.id)) == 1          # stored once
    assert _metered(store, tenant) == metered                   # reading a result is free


async def test_fresh_runs_it_again(client, store, rails):
    tenant, auth = await _tenant(store)
    _query(client, auth)
    r = _query(client, auth, fresh=True)
    assert r.json()["runs"]["recon.across_rails"]["reused"] is False
    assert rails["stripe"] == 2
    assert len(await store.list_runs(tenant.id)) == 2


async def test_the_window_ends(client, store, rails, monkeypatch):
    _, auth = await _tenant(store)
    _query(client, auth)
    now = cache.now()
    monkeypatch.setattr(cache, "_clock", lambda: now + cache.ttl + 1)
    assert _query(client, auth).json()["runs"]["recon.across_rails"]["reused"] is False
    assert rails["stripe"] == 2


async def test_one_tenants_run_never_answers_another(client, store, rails):
    _, a = await _tenant(store, "Kofi's Store")
    _, b = await _tenant(store, "Accra Coffee Co.")
    _query(client, a)
    r = _query(client, b)
    assert r.json()["runs"]["recon.across_rails"]["reused"] is False
    assert rails["stripe"] == 2


async def test_saving_a_credential_forgets_the_reused_runs(client, store, rails):
    """The customer fixes a key: the next query reads with it, not with
    the run made under the old one."""
    tenant, auth = await _tenant(store)
    _query(client, auth)
    cache.forget(tenant.id)  # what PUT /v1/tenants/{id}/credentials does
    assert _query(client, auth).json()["runs"]["recon.across_rails"]["reused"] is False


def test_the_credential_route_forgets_the_tenants_runs():
    import inspect

    from okwan_api import admin

    assert "cache.forget(tenant_id)" in inspect.getsource(admin)


async def test_a_failed_run_is_not_reused(client, store, monkeypatch):
    _, auth = await _tenant(store)
    calls = {"n": 0}

    async def flaky(ref, resolver, cap, overrides):
        calls["n"] += 1
        if calls["n"] == 1:  # the first run's first read fails, and the run with it
            raise UpstreamError(503, "stripe is down")
        rows = ROWS[ref.connector]
        return rows, Coverage(source=ref.qualified, records=len(rows), cap=cap, truncated=False)

    monkeypatch.setattr(across, "fetch_side", flaky)
    assert _query(client, auth).status_code == 503
    ok = _query(client, auth)
    assert ok.status_code == 200 and ok.json()["runs"]["recon.across_rails"]["reused"] is False


# ── the limit ───────────────────────────────────────────────────────

async def test_live_runs_are_limited_and_reused_reads_are_not(client, store, rails):
    _, auth = await _tenant(store)
    for _ in range(SQL_RECON_TENANT.limit):
        assert _query(client, auth, fresh=True).status_code == 200
    refused = _query(client, auth, fresh=True)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0
    assert "without fresh" in refused.json()["detail"]
    # Nothing was fetched for the refused query.
    assert rails["stripe"] == SQL_RECON_TENANT.limit
    # Reading the recent run is still allowed.
    assert _query(client, auth).json()["runs"]["recon.across_rails"]["reused"] is True


async def test_a_query_needing_two_runs_costs_two(client, store, rails):
    _, auth = await _tenant(store)
    for _ in range(SQL_RECON_TENANT.limit - 1):
        _query(client, auth, fresh=True)
    two = "SELECT count(*) FROM recon.across_rails, recon.shopify_stripe"
    assert _query(client, auth, sql=two, fresh=True).status_code == 429
    assert _query(client, auth, sql="SELECT status FROM recon.shopify_stripe", fresh=True).status_code == 200


async def test_a_missing_connection_is_401_and_spends_nothing(client, store, rails):
    t = await store.create_tenant("Half connected")
    await store.put_credential(t.id, "stripe", "secret_key", "sk")
    key, _ = await store.issue_key(t.id)
    for _ in range(SQL_RECON_TENANT.limit + 2):
        r = client.post("/v1/query", json={"sql": FOLD}, headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 401


# ── the hosted MCP tool ─────────────────────────────────────────────

def _okwan_query():
    from okwan_query.mcp_http import build_server

    return build_server()._tool_manager.get_tool("okwan_query").fn


async def test_mcp_reuses_and_says_when_to_retry(store, rails):
    from okwan_api.auth import set_store

    set_store(store)
    _, auth = await _tenant(store)
    ctx = SimpleNamespace(headers={"authorization": auth["Authorization"]})
    tool = _okwan_query()
    first = await tool(ctx=ctx, sql=FOLD)
    second = await tool(ctx=ctx, sql=FOLD)
    assert second["runs"]["recon.across_rails"]["reused"] is True
    assert second["runs"]["recon.across_rails"]["run_id"] == first["runs"]["recon.across_rails"]["run_id"]
    for _ in range(SQL_RECON_TENANT.limit - 1):
        await tool(ctx=ctx, sql=FOLD, fresh=True)
    refused = await tool(ctx=ctx, sql=FOLD, fresh=True)
    assert "live reconciliation runs" in refused["error"] and refused["retry_after_seconds"] > 0
