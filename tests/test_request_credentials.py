"""No hosted surface takes a credential from anywhere but the vault.

The vault replaced per-request credentials on 2026-08-28, but the
reconciliation REST routes kept reading `X-Okwan-<Connector>-Credential-*`
headers and fell back to this server's own OKWAN_<CONNECTOR>_* variables,
with no API key at all, until 2026-10-02. These tests make that class of
bug fail loudly on every data surface: a tenant holds one value in the
vault, while the environment and the request carry different ones, and
whatever reaches a connector must be the vault's.

They also hold the surfaces to the metering claims in §3.4 and §5: every
run is gated by quota and counted; listings are free.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from okwan_core import UpstreamError, get
from okwan_vault import EnvMasterKey, MemoryStore, new_key

VAULT = {"stripe": {"secret_key": "sk_from_vault"},
         "shopify": {"access_token": "shpat_from_vault",
                     "shop_domain": "vault-store.myshopify.com"}}
SMUGGLED = "smuggled_from_request"
FROM_ENV = "leaked_from_server_env"


@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
async def keyed(store):
    """A tenant with vault credentials for Stripe and Shopify, and its key."""
    tenant = await store.create_tenant("Acme")
    for connector, fields in VAULT.items():
        for field, value in fields.items():
            await store.put_credential(tenant.id, connector, field, value)
    full, _ = await store.issue_key(tenant.id)
    return tenant, full


@pytest.fixture
def client(store):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    set_store(store)
    return TestClient(app)


@pytest.fixture
def hostile_env(monkeypatch):
    """The server's own environment holds rail credentials. It must not matter."""
    for name in ("STRIPE_SECRET_KEY", "SHOPIFY_ACCESS_TOKEN"):
        monkeypatch.setenv(f"OKWAN_{name}", FROM_ENV)
    monkeypatch.setenv("OKWAN_SHOPIFY_SHOP_DOMAIN", "env-store.myshopify.com")


SMUGGLING_HEADERS = {
    "X-Okwan-Stripe-Credential-secret-key": SMUGGLED,
    "X-Okwan-Shopify-Credential-access-token": SMUGGLED,
    "X-Okwan-Shopify-Credential-shop-domain": "smuggled.myshopify.com",
    "X-Okwan-Credential-secret-key": SMUGGLED,
}


@pytest.fixture
def seen(monkeypatch):
    """Every credential set handed to a connector, then stop before the network."""
    calls: list[dict[str, str]] = []

    def factory(connector, creds):
        calls.append(dict(creds))
        raise UpstreamError(599, "stopped by test before any network call")

    for name in ("stripe", "shopify"):
        monkeypatch.setattr(get(name), "context_factory", factory)
    return calls


def _only_vault_values(calls: list[dict[str, str]]) -> None:
    assert calls, "no connector was reached; the test proves nothing"
    allowed = {v for fields in VAULT.values() for v in fields.values()}
    for creds in calls:
        assert set(creds.values()) <= allowed, creds


# ── the key is required ─────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/v1/reconciliations",
    "/v1/reconciliations/shopify_stripe",
    "/v1/reconciliations/across/rails",
])
def test_reconciliation_routes_need_a_key(client, path, hostile_env):
    r = client.get(path, headers=SMUGGLING_HEADERS)
    assert r.status_code == 401
    assert "API key" in r.json()["detail"]


def test_every_route_that_can_read_a_rail_requires_a_key():
    """Structural: a new data route without a tenant dependency fails here.

    Public routes are named explicitly, so adding one is a decision."""
    from okwan_api.auth import admin_actor, check_quota, current_tenant
    from okwan_api.main import app

    public = {"/healthz", "/v1/connectors", "/v1/signup", "/v1/signup/verify",
              "/v1/sessions", "/openapi.json", "/docs", "/docs/oauth2-redirect",
              "/redoc"}
    gates = {current_tenant, check_quota, admin_actor}

    def gated(dependant) -> bool:
        return any(d.call in gates or gated(d) for d in dependant.dependencies)

    def routes(container):
        # FastAPI 0.141 keeps an included router as a wrapper rather than
        # flattening its routes, so walk into each one.
        for r in container.routes:
            if isinstance(r, APIRoute):
                yield r
            elif hasattr(r, "original_router"):
                yield from routes(r.original_router)

    every = list(routes(app))
    assert any(r.path.startswith("/v1/reconciliations") for r in every)  # walk reached them
    open_routes = sorted(
        r.path for r in every if r.path not in public and not gated(r.dependant)
    )
    assert open_routes == []


# ── only the vault reaches a connector ──────────────────────────────

def test_connector_rest(client, keyed, hostile_env, seen):
    client.post("/v1/stripe/charges/list", json={"limit": 1},
                headers={"Authorization": f"Bearer {keyed[1]}", **SMUGGLING_HEADERS})
    _only_vault_values(seen)


def test_sql_rest(client, keyed, hostile_env, seen):
    client.post("/v1/query", json={"sql": "SELECT * FROM stripe.charges"},
                headers={"Authorization": f"Bearer {keyed[1]}", **SMUGGLING_HEADERS})
    _only_vault_values(seen)


@pytest.mark.parametrize("path", [
    "/v1/reconciliations/shopify_stripe",
    "/v1/reconciliations/across/rails",
])
def test_reconciliation_rest(client, keyed, hostile_env, seen, path):
    client.get(path, headers={"Authorization": f"Bearer {keyed[1]}", **SMUGGLING_HEADERS})
    _only_vault_values(seen)


def _ctx(key: str) -> SimpleNamespace:
    headers = {"authorization": f"Bearer {key}"}
    headers.update({k.lower(): v for k, v in SMUGGLING_HEADERS.items()})
    return SimpleNamespace(headers=headers)


def _hosted(name: str):
    from okwan_query.mcp_http import build_server

    return build_server()._tool_manager.get_tool(name).fn


async def test_hosted_mcp_query(store, keyed, hostile_env, seen):
    from okwan_api.auth import set_store

    set_store(store)
    await _hosted("okwan_query")(ctx=_ctx(keyed[1]), sql="SELECT * FROM stripe.charges")
    _only_vault_values(seen)


async def test_hosted_mcp_reconcile(store, keyed, hostile_env, seen):
    from okwan_api.auth import set_store

    set_store(store)
    await _hosted("okwan_reconcile")(ctx=_ctx(keyed[1]), name="shopify_stripe")
    _only_vault_values(seen)


def test_an_unconfigured_tenant_is_not_rescued_by_the_environment(
    client, store, hostile_env, seen
):
    """The old fallback: missing in the vault, present in the server env."""
    import asyncio

    async def bare():
        tenant = await store.create_tenant("Empty")
        return (await store.issue_key(tenant.id))[0]

    key = asyncio.run(bare())
    client.get("/v1/reconciliations/shopify_stripe",
               headers={"Authorization": f"Bearer {key}", **SMUGGLING_HEADERS})
    # Empty is the vault's answer; anything else came from env or the request.
    assert seen and all(v == "" for creds in seen for v in creds.values())


# ── metering and quota ──────────────────────────────────────────────

@pytest.fixture
def instant_run(monkeypatch):
    """Runs that succeed without upstream, so only the accounting is tested."""
    from okwan_query import mcp_http
    from okwan_recon.emitters import rest
    from okwan_recon.engine import ReconResult

    async def ok(spec, resolver):
        return ReconResult(name=spec.name)

    for module in (rest, mcp_http):
        monkeypatch.setattr(module, "run", ok, raising=False)
    monkeypatch.setattr(mcp_http, "run_recon", ok)


async def _usage(store, tenant) -> dict[str, int]:
    out: dict[str, int] = {}
    for (tid, _, surface), n in store._usage.items():
        if tid == tenant.id:
            out[surface] = out.get(surface, 0) + n
    return out


async def test_reconciliation_rest_is_metered_and_listing_is_not(
    client, store, keyed, instant_run
):
    tenant, key = keyed
    auth = {"Authorization": f"Bearer {key}"}
    assert client.get("/v1/reconciliations", headers=auth).status_code == 200
    assert client.get("/v1/reconciliations/shopify_stripe", headers=auth).status_code == 200
    assert await _usage(store, tenant) == {"rest:reconcile": 1}


async def test_hosted_reconcile_is_metered(store, keyed, instant_run):
    from okwan_api.auth import set_store

    set_store(store)
    tenant, key = keyed
    out = await _hosted("okwan_reconcile")(ctx=_ctx(key), name="shopify_stripe")
    assert "error" not in out
    await _hosted("okwan_list_reconciliations")(ctx=_ctx(key))
    assert await _usage(store, tenant) == {"mcp:reconcile": 1}


async def test_hosted_query_is_metered(store, keyed, monkeypatch):
    from okwan_api.auth import set_store
    from okwan_query import mcp_http

    set_store(store)
    tenant, key = keyed

    async def answered(self, sql):
        return {"rows": [], "row_count": 0}

    monkeypatch.setattr(mcp_http.QuerySession, "query", answered)
    await _hosted("okwan_query")(ctx=_ctx(key), sql="SELECT 1")
    await _hosted("okwan_describe_tables")(ctx=_ctx(key))
    assert await _usage(store, tenant) == {"mcp:query": 1}


async def test_a_failed_run_is_not_metered(client, store, keyed, hostile_env, seen):
    tenant, key = keyed
    client.get("/v1/reconciliations/shopify_stripe",
               headers={"Authorization": f"Bearer {key}"})
    assert await _usage(store, tenant) == {}


@pytest.fixture
async def exhausted(store, keyed):
    from okwan_vault.usage import PLANS

    tenant, _ = keyed
    for _ in range(PLANS["free"]):
        await store.record_request(tenant.id, "rest:query")


async def test_quota_gates_reconciliation_rest(client, keyed, exhausted, instant_run):
    r = client.get("/v1/reconciliations/shopify_stripe",
                   headers={"Authorization": f"Bearer {keyed[1]}"})
    assert r.status_code == 402


async def test_quota_gates_the_hosted_runs(store, keyed, exhausted, instant_run):
    from okwan_api.auth import set_store

    set_store(store)
    out = await _hosted("okwan_reconcile")(ctx=_ctx(keyed[1]), name="shopify_stripe")
    assert "monthly request limit" in out["error"]
    out = await _hosted("okwan_query")(ctx=_ctx(keyed[1]), sql="SELECT 1")
    assert "monthly request limit" in out["error"]
