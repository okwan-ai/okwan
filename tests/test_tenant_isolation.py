"""Tenant isolation: one customer never sees another customer's records.

Written for a buyer's security review, so each layer fails the suite the
moment a new surface appears that it does not cover:

1. **Inventory.** Every HTTP route and every hosted MCP tool is classified:
   public, data read through an API key, or administration of a named
   tenant. An unclassified surface fails here.
2. **Administration.** Every route that names a tenant answers 404 to a
   caller outside that tenant's subtree (another account, a sibling
   merchant, a merchant reaching its parent), exactly as for a tenant that
   does not exist, and changes nothing. The owner reaching its own
   merchants is the control that proves the guard is not simply closed.
3. **Data.** Five tenants (two accounts, three merchants) hold credentials
   stamped with their own word. The fake rails answer every request with
   records carrying the stamp of the credential that asked, the way a real
   rail answers with that account's data. Every data surface runs as each
   tenant: the response must carry the caller's stamp and no other.

Runs against three stores, because isolation also lives in the SQL:

* `memory`, the reference store;
* `postgres`, as deployed: every query filters on tenant_id, and every
  tenant-data query also runs under row-level security as okwan_tenant;
* `postgres_where_only`, the same with the restricted role switched off,
  so the WHERE clauses alone must keep tenants apart. The database wall
  alone is proven in tests/test_tenant_rls.py, so each wall is tested
  by itself and a silently weakened one is noticed (scripts/plant_leaks.py).

The Postgres runs skip without OKWAN_VAULT_DATABASE_URL, like
tests/test_vault_postgres.py.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute
from okwan_api.main import app  # registers every connector before collection reads them
from okwan_core import ConnectorContext, UpstreamError, all_connectors
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token
from starlette.routing import Mount, Route

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")

#: One word per tenant. None is a substring of another, and none appears
#: in Okwan's own response text, so finding one in a body means a record
#: of that tenant is in it.
STAMPS = {"isv_a": "ALPHA", "a1": "BRAVO", "a2": "CHARLIE", "isv_b": "DELTA", "b1": "ECHO"}

NOW = datetime.now(UTC)
AT = NOW - timedelta(days=3)
AT_ISO = AT.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── the world: two accounts, three merchants, every connector stamped ──

def _vault(stamp: str) -> dict[str, dict[str, str]]:
    """A value for every credential field of every connector, carrying the stamp."""
    return {c.name: {f: f"{stamp}-{c.name}-{f}" for f in c.auth.required_fields}
            for c in all_connectors()}


@pytest.fixture(params=["memory", "postgres", "postgres_where_only"])
async def store(request, monkeypatch):
    if request.param == "memory":
        yield MemoryStore(EnvMasterKey(new_key()))
        return
    if not DSN:
        pytest.skip("no vault database configured")
    if request.param == "postgres_where_only":
        from okwan_vault import postgres

        async def login_role_unscoped(con, tenant_ids):
            pass

        monkeypatch.setattr(postgres, "_enter_scope", login_role_unscoped)
    s = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    created: list[str] = []
    create = s.create_tenant

    async def tracked(name, parent_id=None):
        tenant = await create(name, parent_id=parent_id)
        created.append(tenant.id)
        return tenant

    s.create_tenant = tracked
    try:
        yield s
    finally:
        for tenant_id in reversed(created):
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await s.close()


async def _session(store, tenant_id: str) -> str:
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(tenant_id, token_hash, NOW + timedelta(hours=1))
    return full


@pytest.fixture
async def world(store):
    isv_a = await store.create_tenant("Acme ISV")
    a1 = await store.create_tenant("Merchant A1", parent_id=isv_a.id)
    a2 = await store.create_tenant("Merchant A2", parent_id=isv_a.id)
    isv_b = await store.create_tenant("Rival ISV")
    b1 = await store.create_tenant("Merchant B1", parent_id=isv_b.id)
    tenants = {"isv_a": isv_a, "a1": a1, "a2": a2, "isv_b": isv_b, "b1": b1}
    for key, tenant in tenants.items():
        for connector, fields in _vault(STAMPS[key]).items():
            for field, value in fields.items():
                await store.put_credential(tenant.id, connector, field, value)
    keys = {k: (await store.issue_key(t.id))[0] for k, t in tenants.items()}
    sessions = {k: await _session(store, tenants[k].id) for k in ("isv_a", "isv_b")}
    return SimpleNamespace(store=store, t=tenants, keys=keys, sessions=sessions)


@pytest.fixture
async def client(store):
    from okwan_api.auth import set_store

    set_store(store)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://okwan.test") as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _stamp_of(creds: dict[str, str]) -> str:
    """Which tenant's vault these credentials came from. Exactly one."""
    found = {s for s in STAMPS.values() if any(s in v for v in creds.values())}
    assert len(found) == 1, f"credentials not from exactly one tenant vault: {creds}"
    return found.pop()


def _only(stamp: str, body: str) -> None:
    """The caller's records are in the response and nobody else's are."""
    assert stamp in body, "the caller's own records are missing: the check would prove nothing"
    leaked = sorted(s for s in STAMPS.values() if s != stamp and s in body)
    assert not leaked, f"another tenant's records in {stamp}'s response: {leaked}"


def _none_but(stamp: str, body: str) -> None:
    leaked = sorted(s for s in STAMPS.values() if s != stamp and s in body)
    assert not leaked, f"another tenant's records in {stamp}'s response: {leaked}"


# ── fake rails: each answers with the asking account's records ─────────

class _Stripe:
    def __init__(self, stamp: str) -> None:
        self.stamp = stamp

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        s = self.stamp
        charge = {"id": f"ch_{s}_1", "amount": 99900, "currency": "usd", "status": "succeeded",
                  "created": int(AT.timestamp()), "amount_refunded": 0,
                  "metadata": {"order_id": f"#{s}-1002"}, "balance_transaction": {"fee": 2927}}
        return {"data": [charge], "has_more": False}

    async def aclose(self) -> None:
        pass


class _Shopify:
    def __init__(self, stamp: str) -> None:
        self.stamp = stamp

    async def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        s = self.stamp
        node = {"id": f"gid://shopify/Order/{s}1", "name": f"#{s}-1002", "createdAt": AT_ISO,
                "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "UNFULFILLED",
                "currentTotalPriceSet": {"shopMoney": {"amount": "999.00", "currencyCode": "USD"}},
                "totalReceivedSet": {"shopMoney": {"amount": "999.00"}},
                "totalRefundedSet": {"shopMoney": {"amount": "0.00"}}}
        return {"data": {
            "orders": {"edges": [{"node": node}], "pageInfo": {"hasNextPage": False, "endCursor": None}},
            "currentAppInstallation": {"accessScopes": [{"handle": "read_all_orders"}]},
        }}

    async def aclose(self) -> None:
        pass


class _PayPal:
    """Returns the account's capture only for the window it falls in."""

    def __init__(self, stamp: str) -> None:
        self.stamp = stamp

    async def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        s = self.stamp
        start = datetime.fromisoformat(params["start_date"])
        end = datetime.fromisoformat(params["end_date"])
        rows = []
        if start <= AT < end:
            rows = [{"transaction_info": {
                "transaction_id": f"{s}TXN1", "transaction_event_code": "T0006",
                "transaction_initiation_date": AT_ISO, "transaction_status": "S",
                "invoice_id": f"#{s}-1002",
                "transaction_amount": {"currency_code": "USD", "value": "999.00"},
                "fee_amount": {"currency_code": "USD", "value": "-35.36"},
            }, "payer_info": {"payer_name": {}}}]
        return {"transaction_details": rows, "page": 1, "total_pages": 1,
                "total_items": len(rows), "last_refreshed_datetime": "2099-01-01T00:00:00Z"}

    async def aclose(self) -> None:
        pass


RAILS = {"stripe": _Stripe, "shopify": _Shopify, "paypal": _PayPal}


def _install(monkeypatch, answer: bool) -> list[tuple[str, str]]:
    reached: list[tuple[str, str]] = []

    def factory_for(name: str):
        def factory(connector, creds):
            stamp = _stamp_of(creds)
            reached.append((name, stamp))
            if answer and name in RAILS:
                return ConnectorContext(client=RAILS[name](stamp), credentials=creds)
            raise UpstreamError(599, "stopped by test before any network call")
        return factory

    for c in all_connectors():
        # A connector with nothing in the vault (`files`) reads Okwan's own
        # store for the tenant the platform names; it keeps its real
        # transport and is proven by its own surfaces below.
        if c.auth.required_fields:
            monkeypatch.setattr(c, "context_factory", factory_for(c.name))
    return reached


@pytest.fixture
def rails(monkeypatch):
    """Every connector's transport comes from here. The three rails answer
    as the asking account; any other connector records whose credentials
    reached it and stops before the network."""
    return _install(monkeypatch, answer=True)


@pytest.fixture
def stopped(monkeypatch):
    """Every connector records whose credentials reached it, then stops."""
    return _install(monkeypatch, answer=False)


# ── 1. inventory: nothing is unclassified ──────────────────────────────

#: No tenant, no tenant data.
PUBLIC = {
    ("GET", "/healthz"), ("GET", "/v1/connectors"), ("GET", "/openapi.json"),
    ("GET", "/docs"), ("GET", "/redoc"),
    ("POST", "/v1/signup"), ("POST", "/v1/signup/verify"), ("POST", "/v1/sessions"),
    # Stripe's webhook: no credential, a signature instead. An event names a
    # tenant only through metadata Okwan itself wrote at Checkout, and only a
    # root account's plan can change: tests/test_billing.py.
    ("POST", "/v1/billing/webhook"),
}
#: The tenant is whoever holds the API key; data is read with its vault only.
KEY_DATA = {
    ("GET", "/v1/reconciliations"), ("GET", "/v1/reconciliations/{name}"),
    ("GET", "/v1/reconciliations/across/{name}"),
    ("GET", "/v1/query/tables"), ("POST", "/v1/query"),
}
INTRUDER_UPLOAD = {
    "filename": "intruder.csv",
    "content_base64": base64.b64encode(b"Date,Amount\n11/02/2026,1.00\n").decode(),
    "mapping": {"kind": "bank_deposits", "currency": "USD",
                "columns": {"posted_on": "Date", "amount_minor": "Amount"}},
}
#: Administration of a named tenant: the subtree guard decides, outsiders get 404.
#: Each entry is the request the cross-tenant test sends.
ADMIN_BY_TENANT: dict[tuple[str, str], tuple[str, dict | None]] = {
    ("POST", "/v1/tenants/{tenant_id}/keys"): ("/v1/tenants/{t}/keys", None),
    ("PUT", "/v1/tenants/{tenant_id}/credentials"):
        ("/v1/tenants/{t}/credentials", {"connector": "stripe", "field": "secret_key", "value": "sk_INTRUDER"}),
    ("GET", "/v1/tenants/{tenant_id}/credentials"): ("/v1/tenants/{t}/credentials", None),
    ("GET", "/v1/tenants/{tenant_id}/usage"): ("/v1/tenants/{t}/usage", None),
    ("GET", "/v1/tenants/{tenant_id}/access"): ("/v1/tenants/{t}/access", None),
    ("POST", "/v1/tenants/{tenant_id}/connectors/gusto/authorize"): ("/v1/tenants/{t}/connectors/gusto/authorize", None),
    ("POST", "/v1/tenants/{tenant_id}/connectors/gusto/callback"): (
        "/v1/tenants/{t}/connectors/gusto/callback", {"code": "c", "state": "s"}),
    ("POST", "/v1/tenants/{tenant_id}/connectors/{connector_name}/test"):
        ("/v1/tenants/{t}/connectors/stripe/test", None),
    ("GET", "/v1/tenants/{tenant_id}/runs/latest"): ("/v1/tenants/{t}/runs/latest", None),
    ("GET", "/v1/tenants/{tenant_id}/runs"): ("/v1/tenants/{t}/runs", None),
    ("GET", "/v1/tenants/{tenant_id}/runs/{run_id}"): ("/v1/tenants/{t}/runs/{run}", None),
    ("POST", "/v1/tenants/{tenant_id}/reconciliations/across/{name}"):
        ("/v1/tenants/{t}/reconciliations/across/rails", None),
    ("POST", "/v1/tenants/{tenant_id}/reconciliations/{name}"):
        ("/v1/tenants/{t}/reconciliations/shopify_stripe", None),
    # File imports: an intruder's upload, and the victim's own file by id.
    ("POST", "/v1/tenants/{tenant_id}/imports"): ("/v1/tenants/{t}/imports", INTRUDER_UPLOAD),
    ("GET", "/v1/tenants/{tenant_id}/imports"): ("/v1/tenants/{t}/imports", None),
    ("GET", "/v1/tenants/{tenant_id}/imports/{file_id}"): ("/v1/tenants/{t}/imports/{imp}", None),
    ("DELETE", "/v1/tenants/{tenant_id}/imports/{file_id}"): ("/v1/tenants/{t}/imports/{imp}", None),
    ("POST", "/v1/tenants/{tenant_id}/imports/preview"):
        ("/v1/tenants/{t}/imports/preview", {k: INTRUDER_UPLOAD[k] for k in ("filename", "content_base64")}),
    ("GET", "/v1/tenants/{tenant_id}/import-layouts"): ("/v1/tenants/{t}/import-layouts", None),
    ("PUT", "/v1/tenants/{tenant_id}/import-layouts/{name}"):
        ("/v1/tenants/{t}/import-layouts/victim-layout", INTRUDER_UPLOAD["mapping"]),
    ("DELETE", "/v1/tenants/{tenant_id}/import-layouts/{name}"):
        ("/v1/tenants/{t}/import-layouts/victim-layout", None),
}
#: Administration without a tenant in the path, each tested on its own below.
ADMIN_OTHER = {
    ("GET", "/v1/tenants"), ("POST", "/v1/tenants"),
    ("DELETE", "/v1/tenants/keys/{key_id}"), ("DELETE", "/v1/sessions/current"),
    # The support assistant: its tools take a merchant id in the body, guarded
    # like the routes above. Every tool from another account, and another
    # account's conversation id, are tested in tests/test_support.py.
    ("POST", "/v1/support/messages"),
    # Billing: the signed-in account's own plan only. No route takes a tenant
    # id; a merchant's session and any API key are refused (tests/test_billing.py).
    ("GET", "/v1/billing"), ("POST", "/v1/billing/checkout"), ("POST", "/v1/billing/portal"),
}
#: The operator console: Okwan's own staff, across accounts by design. Every
#: route answers a plain 404 to anyone but an allowlisted operator whose
#: session is unlocked with an authenticator code: tests/test_ops.py.
OPERATOR = {
    ("GET", "/v1/ops/me"), ("POST", "/v1/ops/unlock"), ("POST", "/v1/ops/lock"),
    ("GET", "/v1/ops/tickets"), ("GET", "/v1/ops/tickets/{ticket_id}"),
    ("POST", "/v1/ops/tickets/{ticket_id}/reply"), ("POST", "/v1/ops/tickets/{ticket_id}/status"),
    ("GET", "/v1/ops/log"), ("GET", "/v1/ops/accounts"), ("GET", "/v1/ops/accounts/{tenant_id}"),
    ("GET", "/v1/ops/health"),
}
#: Mounted apps. /mcp is the hosted MCP server; its tools are listed next.
MOUNTS = {("MOUNT", "/mcp")}
#: The hosted MCP server's tools, each run as every tenant in layer 3.
MCP_TOOLS = {"okwan_list_reconciliations", "okwan_reconcile", "okwan_describe_tables", "okwan_query"}


def _connector_routes() -> set[tuple[str, str]]:
    return {("POST", f"/v1/{c.name}/{r}/{o}")
            for c in all_connectors() for r, res in c.resources.items() for o in res.operations}


def _routes() -> set[tuple[str, str]]:
    def walk(container):
        for r in container.routes:
            if isinstance(r, APIRoute | Route):
                for method in r.methods - {"HEAD"}:
                    yield method, r.path
            elif isinstance(r, Mount):
                yield "MOUNT", r.path
            elif hasattr(r, "original_router"):
                prefix = r.include_context.prefix if hasattr(r.include_context, "prefix") else ""
                for method, path in walk(r.original_router):
                    yield method, path if path.startswith(prefix) else prefix + path

    return set(walk(app))


def test_every_route_is_classified():
    classified = (PUBLIC | KEY_DATA | set(ADMIN_BY_TENANT) | ADMIN_OTHER | OPERATOR | MOUNTS
                  | _connector_routes())
    unclassified = sorted(_routes() - classified)
    assert not unclassified, (
        f"routes no isolation test covers: {unclassified}. Classify each in "
        "tests/test_tenant_isolation.py, and add a cross-tenant request if it names a tenant."
    )


def test_the_inventory_has_no_stale_entries():
    stale = sorted((PUBLIC | KEY_DATA | set(ADMIN_BY_TENANT) | ADMIN_OTHER | OPERATOR | MOUNTS
                    | _connector_routes()) - _routes())
    assert not stale, f"classified routes that no longer exist: {stale}"


def test_every_hosted_mcp_tool_is_classified():
    from okwan_query.mcp_http import build_server

    tools = {t.name for t in build_server()._tool_manager.list_tools()}
    assert tools == MCP_TOOLS, f"hosted MCP tools changed: {sorted(tools ^ MCP_TOOLS)}"


# ── 2. administration: outsiders get 404 and change nothing ────────────

#: (who asks, about whom). Accounts use their session; merchants their key.
OUTSIDERS = [
    ("isv_b", "isv_a"), ("isv_b", "a1"), ("isv_b", "a2"),   # another account
    ("isv_a", "isv_b"), ("isv_a", "b1"),                     # and the other way
    ("a1", "a2"),                                            # a sibling merchant
    ("a1", "isv_a"),                                         # a merchant reaching its parent
]


def _caller(world, who: str) -> dict[str, str]:
    return _auth(world.sessions[who] if who in world.sessions else world.keys[who])


async def _victim_state(world, victim) -> dict[str, Any]:
    store = world.store
    return {
        "creds": await store.credentials_for(victim.id, "stripe", ("secret_key",)),
        "runs": [r.id for r in await store.list_runs(victim.id, limit=50)],
        "imports": [f.id for f in await store.list_imports(victim.id)],
        "layouts": await store.list_layouts(victim.id),
    }


@pytest.fixture
async def victim_runs(world):
    """A stored run for every tenant, so a run route has something real to hide."""
    out = {}
    for key, tenant in world.t.items():
        rec = await world.store.add_run(
            tenant.id, kind="across", name="rails", surface="rest", status="ok",
            started_at=NOW, finished_at=NOW, summary={"orders": 1, "stamp": STAMPS[key]},
            rows=[{"order": f"#{STAMPS[key]}-1002"}], error=None,
        )
        out[key] = rec.id
    return out


@pytest.fixture
async def victim_imports(world):
    """An imported file for every tenant, stamped, so an import route has
    something real to hide."""
    out = {}
    for key, tenant in world.t.items():
        f = await world.store.add_import(
            tenant.id, kind="bank_deposits", filename=f"{STAMPS[key]}.csv", format="csv",
            layout="custom", mapping={}, sha256=f"sha-{STAMPS[key]}", skipped=[], card_columns=[],
            rows=[(2, NOW.date(), {"fitid": f"{STAMPS[key]}-F1", "posted_on": NOW.date().isoformat(),
                                   "amount_minor": 100, "currency": "USD",
                                   "description": f"{STAMPS[key]} deposit"})],
            first_date=NOW.date(), last_date=NOW.date())
        await world.store.save_layout(tenant.id, "victim-layout", {
            "kind": "bank_deposits", "name": "victim-layout", "currency": "USD",
            "columns": {"posted_on": f"{STAMPS[key]} Date", "amount_minor": "Amount"}})
        out[key] = f.id
    return out


@pytest.mark.parametrize("route", sorted(ADMIN_BY_TENANT), ids=lambda r: f"{r[0]} {r[1]}")
@pytest.mark.parametrize("who,whom", OUTSIDERS, ids=[f"{a}->{b}" for a, b in OUTSIDERS])
async def test_an_outsider_gets_404_and_changes_nothing(
    client, world, victim_runs, victim_imports, rails, route, who, whom
):
    method, _ = route
    template, body = ADMIN_BY_TENANT[route]
    victim = world.t[whom]
    path = template.format(t=victim.id, run=victim_runs[whom], imp=victim_imports[whom])
    before = await _victim_state(world, victim)

    r = await client.request(method, path, json=body, headers=_caller(world, who))

    assert r.status_code == 404, f"{who} reached {whom} at {method} {path}: {r.status_code} {r.text[:200]}"
    assert victim.name not in r.text
    for stamp in STAMPS.values():
        assert stamp not in r.text, f"{whom}'s data in a refusal: {r.text[:200]}"
    assert await _victim_state(world, victim) == before
    assert rails == [], "a refused request still reached a rail"


@pytest.mark.parametrize("route", sorted(ADMIN_BY_TENANT), ids=lambda r: f"{r[0]} {r[1]}")
async def test_a_foreign_id_reads_exactly_like_one_that_does_not_exist(client, world, victim_runs, route):
    """404 for someone else's tenant, 404 for no tenant: nothing to enumerate."""
    method, _ = route
    template, body = ADMIN_BY_TENANT[route]
    foreign = await client.request(
        method, template.format(t=world.t["isv_a"].id, run="run_nope", imp="imp_nope"),
        json=body, headers=_caller(world, "isv_b"))
    missing = await client.request(
        method, template.format(t="ten_doesnotexist0", run="run_nope", imp="imp_nope"),
        json=body, headers=_caller(world, "isv_b"))
    assert foreign.status_code == missing.status_code == 404


@pytest.mark.parametrize("whom", ["a1", "a2"])
async def test_the_owner_reaches_its_own_merchants(client, world, victim_runs, whom):
    """The control: the guard opens for the account that owns the merchant."""
    t = world.t[whom]
    r = await client.get(f"/v1/tenants/{t.id}/runs/{victim_runs[whom]}", headers=_caller(world, "isv_a"))
    assert r.status_code == 200
    _only(STAMPS[whom], r.text)


@pytest.mark.parametrize("whom", ["a1", "a2"])
async def test_the_owner_reaches_its_own_merchants_imports_and_no_one_elses(
    client, world, victim_imports, whom
):
    t = world.t[whom]
    listed = await client.get(f"/v1/tenants/{t.id}/imports", headers=_caller(world, "isv_a"))
    one = await client.get(f"/v1/tenants/{t.id}/imports/{victim_imports[whom]}",
                           headers=_caller(world, "isv_a"))
    assert listed.status_code == one.status_code == 200
    _only(STAMPS[whom], listed.text)
    _only(STAMPS[whom], one.text)
    # Another merchant's file, asked for under this one's id, does not exist.
    other = "a2" if whom == "a1" else "a1"
    crossed = await client.get(f"/v1/tenants/{t.id}/imports/{victim_imports[other]}",
                               headers=_caller(world, "isv_a"))
    assert crossed.status_code == 404
    rows = await world.store.import_rows(t.id, "bank_deposits")
    _only(STAMPS[whom], json.dumps(rows))


async def test_the_tenant_list_shows_only_the_callers_subtree(client, world):
    for who, own in (("isv_a", {"isv_a", "a1", "a2"}), ("isv_b", {"isv_b", "b1"})):
        r = await client.get("/v1/tenants", headers=_caller(world, who))
        assert r.status_code == 200
        body = r.text
        for key, tenant in world.t.items():
            assert (tenant.id in body) == (key in own), f"{who} listing and {key}"


async def test_a_new_merchant_belongs_to_the_caller(client, world):
    r = await client.post("/v1/tenants", json={"name": "Merchant B2"}, headers=_caller(world, "isv_b"))
    assert r.status_code == 201
    created = await world.store.get_tenant(r.json()["id"])
    assert created.parent_id == world.t["isv_b"].id


async def test_another_accounts_key_cannot_be_revoked(client, world, store):
    victim_key = world.keys["isv_a"]
    victim_id = (await store.tenant_for_key(victim_key)).id
    owned = [k for k in await _keys(store, victim_id)]
    assert owned, "the victim must hold a key for this to mean anything"
    for key_id in owned:
        r = await client.delete(f"/v1/tenants/keys/{key_id}", headers=_caller(world, "isv_b"))
        assert r.status_code == 404
    assert (await store.tenant_for_key(victim_key)).id == victim_id


async def _keys(store, tenant_id: str) -> list[str]:
    if isinstance(store, MemoryStore):
        return [k.id for k in store._keys.values() if k.tenant_id == tenant_id]
    rows = await store.pool.fetch("SELECT id FROM api_keys WHERE tenant_id = $1", tenant_id)
    return [r["id"] for r in rows]


async def test_signing_out_ends_only_the_callers_session(client, world, store):
    r = await client.delete("/v1/sessions/current", headers=_caller(world, "isv_b"))
    assert r.status_code == 204
    still = await client.get("/v1/tenants", headers=_caller(world, "isv_a"))
    assert still.status_code == 200


# ── 3. data: every surface returns only the caller's records ───────────

CALLERS = list(STAMPS)

#: Every data surface over HTTP, as (method, path, body).
HTTP_SURFACES = [
    ("POST", "/v1/stripe/charges/list", {"limit": 10}),
    ("POST", "/v1/shopify/orders/list", {}),
    ("POST", "/v1/paypal/transactions/list", {}),
    ("POST", "/v1/query", {"sql": "SELECT * FROM stripe.charges"}),
    # fresh: each request runs live, so the rails' credentials are checked
    # every time; reused runs are checked below.
    ("POST", "/v1/query", {"sql": "SELECT * FROM recon.across_rails", "fresh": True}),
    ("POST", "/v1/query", {"sql": "SELECT o.name, c.id FROM shopify.orders o "
                                  "JOIN stripe.charges c ON c.order_ref = o.name"}),
    ("GET", "/v1/reconciliations/shopify_stripe", None),
    ("GET", "/v1/reconciliations/shopify_paypal", None),
    ("GET", "/v1/reconciliations/across/rails", None),
]


@pytest.mark.parametrize("who", CALLERS)
@pytest.mark.parametrize("surface", HTTP_SURFACES, ids=lambda s: f"{s[0]} {s[1]} {json.dumps(s[2]) if s[2] else ''}")
async def test_each_http_data_surface_returns_only_the_callers_records(client, world, rails, surface, who):
    method, path, body = surface
    r = await client.request(method, path, json=body, headers=_auth(world.keys[who]))
    assert r.status_code == 200, f"{method} {path}: {r.status_code} {r.text[:300]}"
    _only(STAMPS[who], r.text)
    assert {stamp for _, stamp in rails} == {STAMPS[who]}


@pytest.mark.parametrize("who", CALLERS)
@pytest.mark.parametrize("path", ["/v1/reconciliations", "/v1/query/tables"])
async def test_listings_name_no_other_tenant(client, world, path, who):
    r = await client.get(path, headers=_auth(world.keys[who]))
    assert r.status_code == 200
    _none_but(STAMPS[who], r.text)
    for key, tenant in world.t.items():
        if key != who:
            assert tenant.id not in r.text and tenant.name not in r.text


def _connector_requests() -> list[tuple[str, dict[str, Any]]]:
    """Every connector operation with a body that passes its input model."""
    sample = {"sql": "SELECT 1", "table": "orders", "to": "233200000000", "body": "hello",
              "phone_number_id": "1", "template_name": "hello_world", "waba_id": "1"}
    out = []
    for c in all_connectors():
        if not c.auth.required_fields:
            continue  # nothing in a vault to read: test_imported_files_answer_only_the_caller
        for rname, res in c.resources.items():
            for oname, op in res.operations.items():
                required = [n for n, f in op.input_model.model_fields.items() if f.is_required()]
                body = {n: 1 if op.input_model.model_fields[n].annotation is int else sample.get(n, "x")
                        for n in required}
                out.append((f"/v1/{c.name}/{rname}/{oname}", body))
    return out


@pytest.mark.parametrize("who", ["isv_a", "b1"])
@pytest.mark.parametrize("request_", _connector_requests(), ids=lambda r: r[0])
async def test_every_connector_operation_reads_only_the_callers_vault(client, world, stopped, request_, who):
    """All connector routes share one path from key to vault; this walks every one."""
    path, body = request_
    r = await client.post(path, json=body, headers=_auth(world.keys[who]))
    assert r.status_code != 422, f"{path} refused the generated body: {r.text[:300]}"
    assert stopped, f"{path} never reached its connector: the check would prove nothing"
    assert {stamp for _, stamp in stopped} == {STAMPS[who]}


#: Imported files on every surface a connector gets, and the reconciliation
#: over them. Nothing here touches a vault: the tenant is the caller's key.
FILE_SURFACES = [
    ("POST", "/v1/files/bank_deposits/list", {}),
    ("POST", "/v1/files/bank_deposits/list", {"direction": "in", "limit": 500}),
    ("POST", "/v1/query", {"sql": "SELECT * FROM files.bank_deposits"}),
    ("POST", "/v1/query", {"sql": "SELECT * FROM recon.batches_deposits", "fresh": True}),
    ("GET", "/v1/reconciliations/batches_deposits", None),
]


@pytest.mark.parametrize("who", CALLERS)
@pytest.mark.parametrize("surface", FILE_SURFACES, ids=lambda s: f"{s[0]} {s[1]} {json.dumps(s[2]) if s[2] else ''}")
async def test_imported_files_answer_only_the_caller(client, world, victim_imports, surface, who):
    method, path, body = surface
    r = await client.request(method, path, json=body, headers=_auth(world.keys[who]))
    assert r.status_code == 200, f"{method} {path}: {r.status_code} {r.text[:300]}"
    _only(STAMPS[who], r.text)


@pytest.mark.parametrize("who", CALLERS)
@pytest.mark.parametrize("call", [("okwan_query", {"sql": "SELECT * FROM files.bank_deposits"}),
                                  ("okwan_reconcile", {"name": "batches_deposits"})],
                         ids=lambda c: f"{c[0]} {c[1]}")
async def test_imported_files_answer_only_the_caller_over_hosted_mcp(store, world, victim_imports, call, who):
    from okwan_api.auth import set_store

    set_store(store)
    name, args = call
    ctx = SimpleNamespace(headers={"authorization": f"Bearer {world.keys[who]}"})
    _only(STAMPS[who], json.dumps(await _tool(name)(ctx=ctx, **args), default=str))


def _tool(name: str):
    from okwan_query.mcp_http import build_server

    return build_server()._tool_manager.get_tool(name).fn


MCP_CALLS = [
    ("okwan_query", {"sql": "SELECT * FROM stripe.charges"}),
    # A reconciliation read as a SQL table runs as the caller too.
    ("okwan_query", {"sql": "SELECT * FROM recon.across_rails"}),
    ("okwan_reconcile", {"name": "shopify_stripe"}),
    ("okwan_reconcile", {"name": "rails"}),
]


@pytest.mark.parametrize("who", CALLERS)
@pytest.mark.parametrize("call", MCP_CALLS, ids=lambda c: f"{c[0]} {c[1]}")
async def test_each_hosted_mcp_tool_returns_only_the_callers_records(store, world, rails, call, who):
    from okwan_api.auth import set_store

    set_store(store)
    name, args = call
    ctx = SimpleNamespace(headers={"authorization": f"Bearer {world.keys[who]}"})
    result = await _tool(name)(ctx=ctx, **args)
    _only(STAMPS[who], json.dumps(result, default=str))
    assert {stamp for _, stamp in rails} == {STAMPS[who]}


@pytest.mark.parametrize("who", CALLERS)
@pytest.mark.parametrize("name", ["okwan_list_reconciliations", "okwan_describe_tables"])
async def test_hosted_mcp_listings_name_no_other_tenant(store, world, name, who):
    from okwan_api.auth import set_store

    set_store(store)
    ctx = SimpleNamespace(headers={"authorization": f"Bearer {world.keys[who]}"})
    text = json.dumps(await _tool(name)(ctx=ctx), default=str)
    _none_but(STAMPS[who], text)
    for key, tenant in world.t.items():
        if key != who:
            assert tenant.id not in text


@pytest.mark.parametrize("owner,merchant", [("isv_a", "a1"), ("isv_a", "a2"), ("isv_b", "b1")])
async def test_a_dashboard_run_reads_that_merchant_and_stores_only_its_rows(
    client, world, rails, victim_runs, owner, merchant
):
    """Run from the dashboard as the merchant, then read the stored result
    back. Every tenant already holds a stored run, so a listing that drops
    its tenant filter shows up as someone else's run id."""
    t = world.t[merchant]
    run = await client.post(f"/v1/tenants/{t.id}/reconciliations/across/rails", headers=_caller(world, owner))
    assert run.status_code == 200, run.text[:300]
    _only(STAMPS[merchant], run.text)
    assert {stamp for _, stamp in rails} == {STAMPS[merchant]}

    listed = await client.get(f"/v1/tenants/{t.id}/runs", headers=_caller(world, owner))
    latest = await client.get(f"/v1/tenants/{t.id}/runs/latest", headers=_caller(world, owner))
    for r in (listed, latest):
        assert r.status_code == 200
        _none_but(STAMPS[merchant], r.text)
        assert victim_runs[merchant] in r.text or r is latest
        foreign = [k for k, run_id in victim_runs.items() if k != merchant and run_id in r.text]
        assert not foreign, f"{merchant}'s run history shows runs of {foreign}"
    run_id = run.json()["run_id"]
    assert run_id in listed.text
    one = await client.get(f"/v1/tenants/{t.id}/runs/{run_id}", headers=_caller(world, owner))
    assert one.status_code == 200
    _only(STAMPS[merchant], one.text)


async def test_a_run_id_from_another_tenant_is_404_under_your_own(client, world, victim_runs):
    """Owning the path is not enough: the run itself must be yours."""
    own = world.t["b1"].id
    r = await client.get(f"/v1/tenants/{own}/runs/{victim_runs['a1']}", headers=_caller(world, "isv_b"))
    assert r.status_code == 404
    assert STAMPS["a1"] not in r.text


async def test_usage_counts_only_the_callers_subtree(client, world, rails):
    await client.post("/v1/stripe/charges/list", json={"limit": 1}, headers=_auth(world.keys["a1"]))
    a = (await client.get(f"/v1/tenants/{world.t['isv_a'].id}/usage", headers=_caller(world, "isv_a"))).json()
    b = (await client.get(f"/v1/tenants/{world.t['isv_b'].id}/usage", headers=_caller(world, "isv_b"))).json()
    text_b = json.dumps(b)
    for key in ("isv_a", "a1", "a2"):
        assert world.t[key].id not in text_b
    assert world.t["a1"].id in json.dumps(a)


async def test_the_access_log_shows_only_the_callers_subtree_each_read_under_its_own_key(
        client, world, rails):
    """A read is logged under the tenant it read and the key that read it.
    Alternating callers in one process, each row names its own key: a
    caller remembered from the last request would be logged as this one."""
    for who in ("a1", "b1", "a1"):
        r = await client.post("/v1/stripe/charges/list", json={"limit": 1}, headers=_auth(world.keys[who]))
        assert r.status_code == 200
    a = (await client.get(f"/v1/tenants/{world.t['isv_a'].id}/access", headers=_caller(world, "isv_a"))).json()
    b = (await client.get(f"/v1/tenants/{world.t['isv_b'].id}/access", headers=_caller(world, "isv_b"))).json()
    assert [(e["tenant_id"], e["credential"]) for e in a["entries"]] == [
        (world.t["a1"].id, world.keys["a1"][:12])] * 2
    assert [(e["tenant_id"], e["credential"]) for e in b["entries"]] == [
        (world.t["b1"].id, world.keys["b1"][:12])]
    text_b = json.dumps(b)
    for key in ("isv_a", "a1", "a2"):
        assert world.t[key].id not in text_b
    assert world.keys["a1"][:12] not in text_b


# ── 4. nothing carries over between requests ───────────────────────────

@pytest.mark.parametrize("surface", HTTP_SURFACES[:5], ids=lambda s: f"{s[0]} {s[1]} {json.dumps(s[2])}")
async def test_back_to_back_callers_never_see_each_other(client, world, rails, surface):
    """One process, alternating tenants: a cached connection, a loaded
    table or a resolved credential left over from the last request would
    show up as the previous caller's stamp."""
    method, path, body = surface
    for who in ("isv_a", "b1", "isv_a", "a2", "b1"):
        rails.clear()
        r = await client.request(method, path, json=body, headers=_auth(world.keys[who]))
        assert r.status_code == 200
        _only(STAMPS[who], r.text)
        assert {stamp for _, stamp in rails} == {STAMPS[who]}


async def test_reused_runs_answer_only_the_tenant_that_ran_them(client, world, rails):
    """recon.* tables reuse a run from the last five minutes. Alternating
    tenants, each answer carries only its own stamp: a reused run is keyed
    by the tenant, never shared."""
    sql = {"sql": "SELECT * FROM recon.across_rails"}
    for who in ("isv_a", "b1", "isv_a", "a2", "b1", "a2"):
        r = await client.post("/v1/query", json=sql, headers=_auth(world.keys[who]))
        assert r.status_code == 200, r.text[:300]
        _only(STAMPS[who], r.text)


async def test_back_to_back_mcp_callers_never_see_each_other(store, world, rails):
    from okwan_api.auth import set_store

    set_store(store)
    query = _tool("okwan_query")
    for who in ("a1", "isv_b", "a1", "a2"):
        ctx = SimpleNamespace(headers={"authorization": f"Bearer {world.keys[who]}"})
        _only(STAMPS[who], json.dumps(await query(ctx=ctx, sql="SELECT * FROM stripe.charges"), default=str))


def test_the_process_wide_paypal_token_cache_is_keyed_by_the_secret():
    """The one cache of rail access that outlives a request. A PayPal client
    id is not secret (it ships in checkout pages), so a key on the id alone
    would let anyone who stores another merchant's id reuse that merchant's
    token. The key covers the secret, so a wrong secret never hits."""
    from okwan_paypal.auth import LIVE_HOST, _cache_key

    url = f"{LIVE_HOST}/v1/oauth2/token"
    assert _cache_key("AeShared", "victim-secret", url) != _cache_key("AeShared", "guess", url)


async def test_an_accounts_latest_runs_cover_its_merchants_and_no_one_else(client, world, victim_runs):
    """Overview reads the newest run of the account and each direct merchant."""
    for who, own in (("isv_a", {"isv_a", "a1", "a2"}), ("isv_b", {"isv_b", "b1"})):
        r = await client.get(f"/v1/tenants/{world.t[who].id}/runs/latest", headers=_caller(world, who))
        assert r.status_code == 200
        assert set(r.json()["data"]) == {world.t[k].id for k in own}
        for key, run_id in victim_runs.items():
            assert (run_id in r.text) == (key in own), f"{who} latest and {key}"


# ── 5. the query engine reaches nothing outside its tables ─────────────
#
# One tenant's SQL that could read a file could read every tenant's data:
# the server's environment holds the vault's master key and database
# address. Two walls again: the DuckDB connection is opened with file,
# network and extension access off and its configuration locked (the
# engine wall), and the statement guard refuses the readers by name (the
# coarse wall). Each is tested alone.

SENTINEL = "okwan-isolation-sentinel-7f3a"

#: SQL that tries to leave the engine: a file standing in for the master
#: key, the process environment, the file system, the network.
ESCAPES = [
    "SELECT content FROM read_text('{path}')",
    "SELECT content FROM read_blob('{path}')",
    "SELECT * FROM read_csv_auto('{path}')",
    "SELECT * FROM read_json_auto('{path}')",
    "SELECT * FROM '{path}'",
    "SELECT * FROM query('SELECT content FROM read_text(''{path}'')')",
    "SELECT content FROM read_text('/proc/self/environ')",
    "SELECT * FROM glob('/*')",
    "SELECT content FROM read_text('https://example.com/')",
]


@pytest.fixture
def secret_file(tmp_path):
    path = tmp_path / "vault.csv"
    path.write_text(f"master_key\n{SENTINEL}\n")
    return str(path)


@pytest.mark.parametrize("template", ESCAPES)
def test_the_engine_itself_refuses_to_leave_its_tables(secret_file, template):
    """Bypasses the guard on purpose: the connection must hold alone."""
    import duckdb
    from okwan_query.session import QuerySession

    session = QuerySession(max_records=5)
    try:
        with pytest.raises(duckdb.Error):
            leaked = session.connection.execute(template.format(path=secret_file)).fetchall()
            assert SENTINEL not in str(leaked)
    finally:
        session.close()


@pytest.mark.parametrize("setting", ["enable_external_access = true", "lock_configuration = false",
                                     "autoload_known_extensions = true"])
def test_the_engine_cannot_be_unlocked(setting):
    import duckdb
    from okwan_query.session import QuerySession

    session = QuerySession(max_records=5)
    try:
        with pytest.raises(duckdb.Error):
            session.connection.execute(f"SET {setting}")
    finally:
        session.close()


@pytest.mark.parametrize("template", ESCAPES)
def test_the_statement_guard_refuses_every_escape(secret_file, template):
    from okwan_query.guard import UnsafeStatement, check

    with pytest.raises(UnsafeStatement):
        check(template.format(path=secret_file))


@pytest.mark.parametrize("template", ESCAPES)
async def test_no_escape_works_over_rest(client, world, secret_file, template):
    r = await client.post("/v1/query", json={"sql": template.format(path=secret_file)},
                          headers=_auth(world.keys["b1"]))
    assert r.status_code == 400, r.text[:200]
    assert SENTINEL not in r.text and "PATH=" not in r.text


@pytest.mark.parametrize("template", ESCAPES)
async def test_no_escape_works_over_hosted_mcp(store, world, secret_file, template):
    from okwan_api.auth import set_store

    set_store(store)
    ctx = SimpleNamespace(headers={"authorization": f"Bearer {world.keys['b1']}"})
    out = json.dumps(await _tool("okwan_query")(ctx=ctx, sql=template.format(path=secret_file)), default=str)
    assert '"error"' in out
    assert SENTINEL not in out and "PATH=" not in out
