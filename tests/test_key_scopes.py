"""A read-only API key reads; it cannot administer.

Learned from Gusto: an integration there gets only the scopes its use
needs (Okwan's own Gusto app holds `payrolls:read`, so Gusto never sends
it an employee's name). Until now every Okwan key could do everything its
tenant's dashboard could: add merchants, issue and revoke keys, replace a
saved credential, delete an import. A key pasted into an AI client's
config file was an administrator's key.

Now a key is `read` or `admin`, stated where it is made:
1. A read key reaches the data routes and the hosted MCP server, and is
   refused with 403 at every administration route. The routes are found
   from the app itself, so a route added later is covered without a
   change here.
2. Keys are read-only unless `admin` is asked for, through the API, the
   dashboard and the vault CLI. The store refuses a key with no scope.
3. Keys issued before scopes existed stay admin: the column arrives as
   `admin` for them, and then has no default.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.routing import APIRoute
from okwan_vault import ADMIN, READ, EnvMasterKey, MemoryStore, PostgresStore, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
SCHEMA = os.path.join(os.path.dirname(__file__), "..", "packages", "vault", "okwan_vault", "schema.sql")


@pytest.fixture
async def api():
    from okwan_api.auth import set_store
    from okwan_api.main import app

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    shop = await store.create_tenant("Corner Café")
    read_key, _ = await store.issue_key(shop.id, scope=READ)
    admin_key, _ = await store.issue_key(shop.id, scope=ADMIN)
    session, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(shop.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        yield c, store, shop, {"read": read_key, "admin": admin_key, "session": session}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _depends_on(dependant, target) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def _api_routes(container, prefix: str = ""):
    """Every APIRoute with its full path, through included routers (walked
    as test_tenant_isolation walks them)."""
    for r in container.routes:
        if isinstance(r, APIRoute):
            yield (r.path if r.path.startswith(prefix) else prefix + r.path), r
        elif hasattr(r, "original_router"):
            inner = r.include_context.prefix if hasattr(r.include_context, "prefix") else ""
            yield from _api_routes(r.original_router, inner)


def _admin_routes() -> list[tuple[str, str]]:
    from okwan_api.auth import admin_actor
    from okwan_api.main import app

    return sorted({(m, path) for path, r in _api_routes(app) if _depends_on(r.dependant, admin_actor)
                   for m in r.methods if m != "HEAD"})


def _fill(path: str, tenant_id: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: tenant_id if m.group(1) == "tenant_id" else
                  ("stripe" if m.group(1) == "connector_name" else f"x-{m.group(1)}"), path)


# ── 1. a read key reads, and administers nothing ──────────────────────

def test_the_administration_routes_are_found_from_the_app():
    routes = _admin_routes()
    assert len(routes) >= 25, "found too few administration routes for the check to mean anything"
    for expected in [("POST", "/v1/tenants"), ("POST", "/v1/tenants/{tenant_id}/keys"),
                     ("DELETE", "/v1/tenants/keys/{key_id}"), ("PUT", "/v1/tenants/{tenant_id}/credentials"),
                     ("DELETE", "/v1/tenants/{tenant_id}/imports/{file_id}")]:
        assert expected in routes


async def test_a_read_key_is_refused_at_every_administration_route(api):
    from okwan_api.auth import READ_ONLY

    client, _, shop, keys = api
    told = 0
    for method, path in _admin_routes():
        r = await client.request(method, _fill(path, shop.id), headers=_bearer(keys["read"]),
                                 json={} if method in ("POST", "PUT", "PATCH") else None)
        assert r.status_code in (401, 403), f"a read key reached {method} {path}: {r.status_code}"
        told += r.status_code == 403 and r.json().get("detail") == READ_ONLY
    # Billing turns away every key before asking which kind; the rest say why.
    assert told >= len(_admin_routes()) - 3


async def test_an_admin_key_still_administers(api):
    client, _, shop, keys = api
    r = await client.post(f"/v1/tenants/{shop.id}/keys", headers=_bearer(keys["admin"]))
    assert r.status_code == 201 and r.json()["scope"] == READ
    assert (await client.post("/v1/tenants", headers=_bearer(keys["admin"]), json={"name": "Pop-up"})).status_code == 201


async def test_a_read_key_reads_data(api):
    client, store, shop, keys = api
    r = await client.post("/v1/query", headers=_bearer(keys["read"]), json={"sql": "SELECT 1 AS one"})
    assert r.status_code == 200, r.text
    assert (await client.get("/v1/reconciliations", headers=_bearer(keys["read"]))).status_code == 200
    # The hosted MCP server resolves a caller with tenant_for_key: a read key is its tenant.
    assert (await store.tenant_for_key(keys["read"])).id == shop.id


async def test_a_read_key_cannot_mint_itself_an_admin_key(api):
    client, _, shop, keys = api
    r = await client.post(f"/v1/tenants/{shop.id}/keys", headers=_bearer(keys["read"]), json={"scope": "admin"})
    assert r.status_code == 403


# ── 2. read-only unless admin is asked for ────────────────────────────

async def test_a_new_key_is_read_only_unless_admin_is_asked_for(api):
    client, store, shop, keys = api
    session = _bearer(keys["session"])
    plain = await client.post(f"/v1/tenants/{shop.id}/keys", headers=session)
    empty = await client.post(f"/v1/tenants/{shop.id}/keys", headers=session, json={})
    admin = await client.post(f"/v1/tenants/{shop.id}/keys", headers=session, json={"scope": "admin"})
    assert (plain.json()["scope"], empty.json()["scope"], admin.json()["scope"]) == (READ, READ, ADMIN)
    assert (await store.access_for_key(plain.json()["secret"]))[1] == READ
    assert (await store.access_for_key(admin.json()["secret"]))[1] == ADMIN
    bad = await client.post(f"/v1/tenants/{shop.id}/keys", headers=session, json={"scope": "owner"})
    assert bad.status_code == 422


async def test_the_store_refuses_a_key_without_a_stated_scope():
    store = MemoryStore(EnvMasterKey(new_key()))
    shop = await store.create_tenant("Corner Café")
    with pytest.raises(TypeError):
        await store.issue_key(shop.id)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        await store.issue_key(shop.id, scope="owner")


def test_the_vault_cli_issues_read_keys_unless_admin_is_named():
    from okwan_vault import cli

    assert cli.key_issue.__defaults__ == (READ,)
    assert "key issue <tenant_id> [read|admin]" in cli.USAGE


def test_the_dashboard_issues_read_keys_and_admin_only_when_ticked():
    root = os.path.join(os.path.dirname(__file__), "..", "apps", "dashboard")
    route = open(os.path.join(root, "app", "api", "key", "route.ts")).read()
    assert 'scope === "admin" ? "admin" : "read"' in route
    issue = open(os.path.join(root, "app", "_components", "issue-key.tsx")).read()
    assert "useState(false)" in issue.split("const [admin, setAdmin]")[1][:40], "admin starts unticked"
    setup = open(os.path.join(root, "app", "_components", "mcp-setup.tsx")).read()
    assert "allowAdmin" not in setup, "an AI client's key is always read-only"


# ── 3. Postgres: the scope is stored, checked, and old keys stay admin ─

needs_db = pytest.mark.skipif(not DSN, reason="no vault database configured")


@needs_db
async def test_postgres_stores_and_returns_the_scope():
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    try:
        shop = await store.create_tenant(f"Scope {uuid.uuid4().hex[:6]}")
        try:
            read_key, record = await store.issue_key(shop.id, scope=READ)
            admin_key, _ = await store.issue_key(shop.id, scope=ADMIN)
            assert record.scope == READ
            assert (await store.access_for_key(read_key))[1] == READ
            assert (await store.access_for_key(admin_key))[1] == ADMIN
            import asyncpg

            with pytest.raises(asyncpg.CheckViolationError):
                await store.pool.execute(
                    "INSERT INTO api_keys (id, tenant_id, prefix, hash_hex, scope) VALUES ($1, $2, 'okw_x', $3, 'owner')",
                    f"key_{uuid.uuid4().hex[:16]}", shop.id, uuid.uuid4().hex)
            with pytest.raises(asyncpg.NotNullViolationError):
                await store.pool.execute(
                    "INSERT INTO api_keys (id, tenant_id, prefix, hash_hex) VALUES ($1, $2, 'okw_x', $3)",
                    f"key_{uuid.uuid4().hex[:16]}", shop.id, uuid.uuid4().hex)
        finally:
            await store.pool.execute("DELETE FROM tenants WHERE id = $1", shop.id)
    finally:
        await store.close()


def _scope_migration() -> str:
    sql = open(SCHEMA).read()
    blocks = re.findall(r"DO \$\$.*?END \$\$;", sql, re.S)
    [block] = [b for b in blocks if "table_name = 'api_keys'" in b and "scope" in b]
    return block


@needs_db
async def test_keys_issued_before_scopes_stay_admin_and_later_inserts_must_state_one():
    """The migration, run on a key table as it was before this change, in
    a schema of its own so the shared one is untouched."""
    import asyncpg

    con = await asyncpg.connect(DSN)
    schema = f"scope_mig_{uuid.uuid4().hex[:8]}"
    try:
        await con.execute(f"CREATE SCHEMA {schema}")
        await con.execute(f"SET search_path TO {schema}")
        await con.execute("CREATE TABLE api_keys (id text PRIMARY KEY, tenant_id text NOT NULL, prefix text NOT NULL, "
                          "hash_hex text NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now(), "
                          "revoked_at timestamptz)")
        await con.execute("INSERT INTO api_keys (id, tenant_id, prefix, hash_hex) VALUES ('key_old', 't', 'okw_o', 'h1')")
        await con.execute(_scope_migration())
        await con.execute(_scope_migration())  # idempotent: a restart runs it again
        assert await con.fetchval("SELECT scope FROM api_keys WHERE id = 'key_old'") == ADMIN
        assert await con.fetchval(
            "SELECT column_default FROM information_schema.columns WHERE table_schema = $1 "
            "AND table_name = 'api_keys' AND column_name = 'scope'", schema) is None
        with pytest.raises(asyncpg.NotNullViolationError):
            await con.execute("INSERT INTO api_keys (id, tenant_id, prefix, hash_hex) VALUES ('key_new', 't', 'okw_n', 'h2')")
    finally:
        await con.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await con.close()
