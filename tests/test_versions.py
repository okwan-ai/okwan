"""Dated API versions (okwan_core.versions, okwan_api.versions).

What must hold:

- the list is real dates, each after the one before, the first changing
  nothing; the schema's defaults and the dashboard name versions on it;
- every answer under /v1/ says which version answered: the one the
  `Okwan-Version` header names, else the account's, else (no key) the
  newest; a header naming no version, or two, is refused with 400 before
  any route runs, and a route outside /v1/ is left alone;
- an account is made on the newest version and a merchant on its parent's,
  so a release never moves an existing account or the merchants it adds;
  every way a key or session is resolved carries the version, in both
  stores, and rows from before the column are on the first;
- a change since the version answering gives a successful JSON answer back
  in the older shape, and nothing else: not an error, not another route,
  not a request that names the newer version;
- a webhook address keeps the version of the request that added it, and
  each delivery is in that version, signed over exactly what is sent; an
  address on the newest version is sent the stored body byte for byte.

The changes used here are made up for the test: none exists yet.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
import okwan_api.main  # noqa: F401  (registers the connectors and declarations)
import pytest
from okwan_api import webhooks as wh
from okwan_api.auth import set_store
from okwan_core import versions
from okwan_vault import ADMIN, READ, EnvMasterKey, MemoryStore, PostgresStore, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

from tests.test_webhooks import HOST, T0, _run, _verify, receiver  # noqa: F401  (a fixture)

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not DSN, reason="no vault database configured")
ROOT = Path(__file__).resolve().parents[1]
NEXT = "2026-12-01"


def _auth(key, version=None):
    h = {"Authorization": f"Bearer {key}"}
    if version is not None:
        h["Okwan-Version"] = version
    return h


@pytest.fixture
def next_version(monkeypatch):
    """A second version, as a release would add one. `add(change)` puts a
    change in it; the list is checked as it would be at import."""
    changes: list[versions.Change] = []

    def add(change: versions.Change) -> None:
        changes.append(change)
        monkeypatch.setattr(versions, "VERSIONS", (
            versions.VERSIONS[0], versions.Version(NEXT, "made up for a test", tuple(changes))))
        versions.check()

    monkeypatch.setattr(versions, "VERSIONS", (versions.VERSIONS[0], versions.Version(NEXT, "made up for a test")))
    return add


class World:
    pass


@pytest.fixture(params=["memory", pytest.param("postgres", marks=needs_db)])
async def world(request):
    if request.param == "memory":
        store = MemoryStore(EnvMasterKey(new_key()))
    else:
        store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    set_store(store)
    w = World()
    w.store = store
    w.made: list[str] = []
    from okwan_api.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        w.client = c
        yield w
    if request.param == "postgres":
        for t in w.made:
            await store.pool.execute("DELETE FROM tenants WHERE id = $1", t)
        await store.close()


async def _account(w, name="Acme ISV"):
    t = await w.store.create_tenant(name)
    w.made.append(t.id)
    admin, _ = await w.store.issue_key(t.id, scope=ADMIN)
    read, _ = await w.store.issue_key(t.id, scope=READ)
    return t, admin, read


# ── the list ───────────────────────────────────────────────────────────

def test_the_list_is_real_dates_in_order_and_the_first_changes_nothing():
    versions.check()
    dates = versions.dates()
    assert dates[0] == versions.FIRST and versions.current() == dates[-1]
    assert list(dates) == sorted(set(dates))
    assert not versions.VERSIONS[0].changes


@pytest.mark.parametrize("broken, why", [
    ((versions.Version("2026-11-24", "x"), versions.Version("2026-11-24", "y")), "not after"),
    ((versions.Version("2026-11-24", "x"), versions.Version("2026-11-01", "y")), "not after"),
    ((versions.Version("2026-11-24", "x"), versions.Version("2026-1-5", "y")), "YYYY-MM-DD"),
    ((versions.Version("2026-11-24", "x"), versions.Version("2026-02-30", "y")), "day is out of range"),
    ((versions.Version("2026-11-23", "x"),), "FIRST"),
    ((versions.Version("2026-11-24", "x"),
      versions.Version("2026-12-01", "y", (versions.Change("nothing named", lambda b: b),))), "names no route"),
    ((versions.Version("2026-11-24", "x"),
      versions.Version("2026-12-01", "y", (versions.Change("a path alone", lambda b: b,
                                                           routes=frozenset({"/v1/webhooks"})),))), "not a method"),
])
def test_a_broken_list_does_not_start(monkeypatch, broken, why):
    monkeypatch.setattr(versions, "VERSIONS", broken)
    with pytest.raises(ValueError, match=why):
        versions.check()


def test_the_schema_defaults_and_the_dashboard_name_versions_on_the_list():
    schema = (ROOT / "packages/vault/okwan_vault/schema.sql").read_text()
    defaults = re.findall(r"ADD COLUMN api_version text NOT NULL DEFAULT '([0-9-]+)'", schema)
    assert defaults == [versions.FIRST, versions.FIRST]
    dashboard = (ROOT / "apps/dashboard/lib/api.ts").read_text()
    named = re.search(r'export const API_VERSION = "([0-9-]+)";', dashboard)
    assert named and versions.known(named.group(1))
    assert re.search(r'"Okwan-Version": API_VERSION,', dashboard)


def test_undoing_goes_newest_first_and_never_makes_a_body_newer(monkeypatch):
    r = "GET /v1/r"
    v2 = versions.Version("2026-12-01", "b", (versions.Change("b", lambda x: x + ["b"], routes=frozenset({r})),))
    v3 = versions.Version("2027-01-01", "c", (versions.Change("c", lambda x: x + ["c"], routes=frozenset({r})),))
    monkeypatch.setattr(versions, "VERSIONS", (versions.VERSIONS[0], v2, v3))
    versions.check()
    assert versions.back([], to=versions.FIRST, route=r) == ["c", "b"]
    assert versions.back([], to="2026-12-01", route=r) == ["c"]
    assert versions.back([], to="2027-01-01", route=r) == []
    # A body made in the second version is only taken back past the second.
    assert versions.back([], to=versions.FIRST, route=r, made="2026-12-01") == ["b"]
    assert versions.back([], to=versions.FIRST, route="POST /v1/r") == []
    assert not versions.changed("2027-01-01", route=r) and versions.changed(versions.FIRST, route=r)


def test_every_route_a_change_names_is_mounted_and_every_event_is_one_okwan_sends():
    from okwan_api.main import app

    mounted = {f"{m} {r.path}" for r in app.routes for m in getattr(r, "methods", None) or ()}
    for v in versions.VERSIONS:
        for c in v.changes:
            assert c.routes <= mounted, (v.date, c.what, c.routes - mounted)
            assert c.events <= set(wh.EVENT_TYPES), (v.date, c.what)


# ── the header ─────────────────────────────────────────────────────────

async def test_every_answer_under_v1_says_which_version_answered(world):
    w = world
    _, admin, _ = await _account(w)
    # No key: the newest, or the one named.
    r = await w.client.get("/v1/connectors")
    assert r.status_code == 200 and r.headers["okwan-version"] == versions.current()
    r = await w.client.get("/v1/connectors", headers={"Okwan-Version": versions.FIRST})
    assert r.headers["okwan-version"] == versions.FIRST
    # A key: its account's; a refused key still says which version refused it.
    r = await w.client.get("/v1/webhooks", headers=_auth(admin))
    assert r.status_code == 200 and r.headers["okwan-version"] == versions.current()
    r = await w.client.get("/v1/webhooks", headers=_auth("okw_not_a_key"))
    assert r.status_code == 401 and r.headers["okwan-version"] == versions.current()
    # Outside /v1/ nothing is said.
    r = await w.client.get("/healthz")
    assert r.status_code == 200 and "okwan-version" not in r.headers


@pytest.mark.parametrize("sent", [
    ["2026-11-25"], ["latest"], [""], ["2026-11-24; drop"], [b"2026-11-24\xa0"], [b"2026-11-24\x00"], ["2026-11-24", "2026-11-23"],
    ["9999-99-99"], ["2026-11-24,2026-11-24"],
])
async def test_a_header_that_names_no_version_is_refused_before_any_route_runs(world, sent):
    w = world
    t, admin, _ = await _account(w)
    headers = [("authorization", f"Bearer {admin}"), *[("okwan-version", v) for v in sent]]
    r = await w.client.post("/v1/webhooks", headers=headers, json={"url": f"https://{HOST}/x"})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "Okwan-Version names no version" in detail and versions.FIRST in detail
    # What was sent is not said back.
    for v in sent:
        if isinstance(v, str) and v.strip() and not versions.known(v):
            assert v not in detail
    assert r.headers["okwan-version"] == versions.current()
    # Refused before the route: nothing was added.
    assert await w.store.webhook_endpoints([t.id]) == []


async def test_the_same_version_twice_or_with_spaces_is_one_version(world):
    w = world
    _, admin, _ = await _account(w)
    headers = [("authorization", f"Bearer {admin}"), ("Okwan-Version", f" {versions.FIRST} "),
               ("okwan-version", versions.FIRST)]
    r = await w.client.get("/v1/webhooks", headers=headers)
    assert r.status_code == 200 and r.headers["okwan-version"] == versions.FIRST


async def test_an_answer_says_its_version_once(world):
    w = world
    r = await w.client.get("/v1/connectors", headers={"Okwan-Version": versions.FIRST})
    assert r.headers.get_list("okwan-version") == [versions.FIRST]


# ── the account's version ──────────────────────────────────────────────

async def test_an_account_is_made_on_the_newest_and_a_merchant_on_its_account(world, next_version):
    w = world
    # An account made before the release that added the second version.
    first = await w.store.create_tenant("On the first")
    w.made.append(first.id)
    await _set_version(w, first.id, versions.FIRST)
    new = await w.store.create_tenant("Made after the release")
    w.made.append(new.id)
    assert new.api_version == NEXT
    under_first = await w.store.create_tenant("Shop of the first", parent_id=first.id)
    under_new = await w.store.create_tenant("Shop of the new", parent_id=new.id)
    deeper = await w.store.create_tenant("Branch", parent_id=under_first.id)
    assert (under_first.api_version, deeper.api_version, under_new.api_version) == (
        versions.FIRST, versions.FIRST, NEXT)
    # And so through the API: a merchant an account on the first adds is on the first.
    admin, _ = await w.store.issue_key(first.id, scope=ADMIN)
    r = await w.client.post("/v1/tenants", headers=_auth(admin), json={"name": "Added by API"})
    assert r.status_code == 201, r.text
    added = await w.store.get_tenant(r.json()["id"])
    assert added.api_version == versions.FIRST


async def _set_version(w, tenant_id, version):
    if isinstance(w.store, MemoryStore):
        w.store._tenants[tenant_id] = replace(w.store._tenants[tenant_id], api_version=version)
    else:
        await w.store.pool.execute("UPDATE tenants SET api_version = $2 WHERE id = $1", tenant_id, version)


async def test_every_way_a_caller_is_resolved_carries_the_version(world, next_version):
    w = world
    t, admin, read = await _account(w)
    child = await w.store.create_tenant("Shop", parent_id=t.id)
    for key in (admin, read):
        found, _ = await w.store.access_for_key(key)
        assert found.api_version == versions.current()
    full, token_hash = new_token(SESSION_PREFIX)
    await w.store.create_session(t.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    assert (await w.store.tenant_for_session(token_hash)).api_version == versions.current()
    assert (await w.store.get_tenant(child.id)).api_version == versions.current()
    assert {x.api_version for x in await w.store.children_of(t.id)} == {versions.current()}
    assert {x.api_version for x in await w.store.subtree(t.id, 8)} == {versions.current()}
    # A session answers in its account's version, as a key does.
    await _set_version(w, t.id, versions.FIRST)
    r = await w.client.get("/v1/webhooks", headers=_auth(full))
    assert r.status_code == 200 and r.headers["okwan-version"] == versions.FIRST
    r = await w.client.get("/v1/webhooks", headers=_auth(admin))
    assert r.headers["okwan-version"] == versions.FIRST


# ── an older version's shape ───────────────────────────────────────────

def _say_old(body):
    body["shape"] = "old"
    return body


async def test_an_account_on_an_older_version_is_answered_in_its_shape(world, next_version):
    w = world
    next_version(versions.Change("the list's shape", _say_old, routes=frozenset({"GET /v1/webhooks"})))
    old, old_admin, _ = await _account(w, "On the first")
    await _set_version(w, old.id, versions.FIRST)
    _, new_admin, _ = await _account(w, "On the second")

    r = await w.client.get("/v1/webhooks", headers=_auth(old_admin))
    assert r.headers["okwan-version"] == versions.FIRST and r.json() == {"data": [], "shape": "old"}
    assert int(r.headers["content-length"]) == len(r.content)
    # The account asks for the newer version: the newer shape.
    r = await w.client.get("/v1/webhooks", headers=_auth(old_admin, NEXT))
    assert r.headers["okwan-version"] == NEXT and r.json() == {"data": []}
    # An account made after the release, and one that asks for the older.
    assert (await w.client.get("/v1/webhooks", headers=_auth(new_admin))).json() == {"data": []}
    r = await w.client.get("/v1/webhooks", headers=_auth(new_admin, versions.FIRST))
    assert r.json() == {"data": [], "shape": "old"}


async def test_errors_and_other_routes_are_never_reshaped(world, next_version):
    w = world
    next_version(versions.Change("the list's shape", _say_old,
                                 routes=frozenset({"GET /v1/webhooks", "DELETE /v1/webhooks/{endpoint_id}"})))
    old, admin, _ = await _account(w, "On the first")
    await _set_version(w, old.id, versions.FIRST)
    # An error on a changed route: as made.
    r = await w.client.delete("/v1/webhooks/whe_0000000000000000", headers=_auth(admin))
    assert r.status_code == 404 and "shape" not in r.json()
    # The same path with another method: as made.
    r = await w.client.post("/v1/webhooks", headers=_auth(admin), json={"url": "http://not-https"})
    assert r.status_code == 422 and "shape" not in r.json()
    # Another route: byte for byte what the newest version answers.
    a = await w.client.get("/v1/connectors", headers={"Okwan-Version": versions.FIRST})
    b = await w.client.get("/v1/connectors", headers={"Okwan-Version": NEXT})
    assert a.content == b.content and a.headers["okwan-version"] == versions.FIRST


# ── webhooks ───────────────────────────────────────────────────────────

async def test_an_address_keeps_the_version_of_the_request_that_added_it(world, receiver, next_version):  # noqa: F811
    w = world
    _, admin, _ = await _account(w)
    a = await w.client.post("/v1/webhooks", headers=_auth(admin), json={"url": f"https://{HOST}/a"})
    b = await w.client.post("/v1/webhooks", headers=_auth(admin, versions.FIRST), json={"url": f"https://{HOST}/b"})
    assert a.json()["api_version"] == versions.current() and b.json()["api_version"] == versions.FIRST
    listed = (await w.client.get("/v1/webhooks", headers=_auth(admin))).json()["data"]
    assert {e["id"]: e["api_version"] for e in listed} == {a.json()["id"]: versions.current(),
                                                         b.json()["id"]: versions.FIRST}
    # A ping says the address's version, signed.
    await w.client.post(f"/v1/webhooks/{b.json()['id']}/test", headers=_auth(admin))
    assert _verify(b.json()["secret"], receiver.requests[-1])["api_version"] == versions.FIRST


async def test_each_address_is_sent_its_own_version_signed_over_what_is_sent(world, receiver, next_version):  # noqa: F811
    w = world

    def without_currency(body):
        body["data"].pop("currency", None)
        body["data"]["old"] = True
        return body

    next_version(versions.Change("an open item's currency", without_currency,
                                 events=frozenset({"open_item.opened"})))
    acme, admin, read = await _account(w)
    shop = await w.store.create_tenant("Kofi's Store", parent_id=acme.id)
    old = (await w.client.post("/v1/webhooks", headers=_auth(admin, versions.FIRST),
                               json={"url": f"https://{HOST}/old"})).json()
    new = (await w.client.post("/v1/webhooks", headers=_auth(admin), json={"url": f"https://{HOST}/new"})).json()
    assert old["api_version"] == versions.FIRST and new["api_version"] == NEXT

    await _run(shop, [("B1", 3884, "2026-11-09")], T0)
    assert await wh.deliver_due() == 2
    by_path = {r["path"]: r for r in receiver.requests}
    to_old = _verify(old["secret"], by_path["/old"])
    to_new = _verify(new["secret"], by_path["/new"])
    assert to_old["api_version"] == versions.FIRST and to_old["data"]["old"] is True
    assert "currency" not in to_old["data"]
    assert to_new["api_version"] == NEXT and "old" not in to_new["data"] and "currency" in to_new["data"]
    # The newest is the stored body, byte for byte.
    stored = (await w.store.webhook_events(acme.id, after=None, since=T0 - timedelta(days=400), limit=5))[0]
    assert by_path["/new"]["body"] == stored.body
    assert by_path["/old"]["headers"]["webhook-id"] == by_path["/new"]["headers"]["webhook-id"]

    # GET /v1/events is in the version it is answered in.
    listed = (await w.client.get("/v1/events", headers=_auth(read, versions.FIRST))).json()["data"]
    assert listed[0]["body"]["api_version"] == versions.FIRST and listed[0]["body"]["data"]["old"] is True
    listed = (await w.client.get("/v1/events", headers=_auth(read))).json()["data"]
    assert listed[0]["body"] == json.loads(stored.body)


def test_an_event_stored_before_versions_were_named_is_sent_as_it_was_stored():
    body = json.dumps({"id": "evt_x", "type": "open_item.opened", "data": {}}, separators=(",", ":"))
    assert wh.in_version(body, versions.FIRST) == body
    assert wh.in_version(body, versions.current()) == body


# ── rows from before the column ────────────────────────────────────────

def _block(table: str) -> str:
    schema = (ROOT / "packages/vault/okwan_vault/schema.sql").read_text()
    for block in re.findall(r"DO \$\$.*?END \$\$;", schema, re.DOTALL):
        if f"table_name = '{table}'" in block and "column_name = 'api_version'" in block:
            return block
    raise AssertionError(f"no api_version block for {table}")


@needs_db
async def test_rows_from_before_the_column_are_on_the_first_version():
    con = await asyncpg.connect(DSN)
    schema = f"version_mig_{uuid.uuid4().hex[:8]}"
    try:
        await con.execute(f"CREATE SCHEMA {schema}")
        await con.execute(f"SET search_path TO {schema}")
        await con.execute("CREATE TABLE tenants (id text PRIMARY KEY, name text NOT NULL, parent_id text, "
                          "created_at timestamptz NOT NULL DEFAULT now())")
        await con.execute("CREATE TABLE webhook_endpoints (id text PRIMARY KEY, tenant_id text NOT NULL)")
        await con.execute("INSERT INTO tenants (id, name) VALUES ('ten_old', 'Old')")
        await con.execute("INSERT INTO webhook_endpoints (id, tenant_id) VALUES ('whe_old', 'ten_old')")
        for _ in range(2):  # a restart runs it again
            await con.execute(_block("tenants"))
            await con.execute(_block("webhook_endpoints"))
        assert await con.fetchval("SELECT api_version FROM tenants WHERE id = 'ten_old'") == versions.FIRST
        assert await con.fetchval("SELECT api_version FROM webhook_endpoints") == versions.FIRST
        # An instance still on the previous release, which names no version.
        await con.execute("INSERT INTO tenants (id, name) VALUES ('ten_during', 'During the deploy')")
        assert await con.fetchval("SELECT api_version FROM tenants WHERE id = 'ten_during'") == versions.FIRST
        with pytest.raises(asyncpg.CheckViolationError):
            await con.execute("INSERT INTO tenants (id, name, api_version) VALUES ('ten_bad', 'x', 'latest')")
    finally:
        await con.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await con.close()


async def test_a_store_refuses_an_address_on_no_version(world):
    w = world
    t, _, _ = await _account(w)
    with pytest.raises(ValueError, match="not a version"):
        await w.store.add_webhook_endpoint(t.id, url=f"https://{HOST}/x", secret=wh.new_secret(),
                                           created_by="s", at=datetime.now(UTC), api_version="latest")
