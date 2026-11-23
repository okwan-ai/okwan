"""The open items as data an API key reads: one definition, three surfaces.

`OpenItem` is the row of `GET /v1/exceptions`, of the SQL table
`exceptions.open_items` and of the hosted MCP tool `okwan_open_items`
(okwan_api.open_items). What must hold:

- the three answer the same rows, and the SQL table's columns are the
  model's;
- a read-only key reads, open and closed items alike, each with its newest
  decision and suggestion, and decides nothing;
- a key reads its own tenant and those below it, never anything else, on
  every surface, one caller after another;
- reading stored results is not metered, is written to the access log, is
  limited per account, and stops at a bound it says it stopped at.

Merchants' rows are stored results with stamped references: nothing here
reads a rail.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import okwan_api.main  # noqa: F401  (registers the connectors and declarations)
import pytest
from okwan_api import open_items as oi
from okwan_api.auth import set_store
from okwan_recon.exceptions import OpenItem, carry, open_items, terms
from okwan_vault import ADMIN, READ, EnvMasterKey, MemoryStore, PostgresStore, new_key
from okwan_vault.authz import MAX_DEPTH

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")


def _unpaid(ref: str, amount: int, when: str) -> dict:
    return {"status": "unmatched_left", "left_ref": ref, "right_ref": None,
            "evidence": {"record": {"amount_minor": amount, "currency": "USD", "when": when}}}


async def _stored(store, tenant, refs: list[tuple[str, int, str]], at: datetime = NOW):
    """A stored result of the batches check holding these unpaid batches."""
    rows = carry(tenant.id, "batches_deposits", "pair", [_unpaid(*r) for r in refs], None, at)
    return await store.add_run(tenant.id, kind="pair", name="batches_deposits", surface="dashboard",
                               status="ok", started_at=at, finished_at=at,
                               summary={"unmatched_left": len(rows)}, rows=rows, error=None, reads_files=True)


@pytest.fixture
async def world():
    """Two accounts. Acme has two merchants and one below a merchant; each
    tenant's batches carry its own stamp in their references."""
    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)

    class W:
        pass

    w = W()
    w.store = store
    w.acme = await store.create_tenant("Acme ISV")
    w.kofi = await store.create_tenant("Kofi's Store", parent_id=w.acme.id)
    w.accra = await store.create_tenant("Accra Coffee", parent_id=w.acme.id)
    w.deep = await store.create_tenant("Deep Store", parent_id=w.kofi.id)
    w.other = await store.create_tenant("Other ISV")
    w.theirs = await store.create_tenant("Their Store", parent_id=w.other.id)
    w.stamp = {}
    for i, t in enumerate((w.acme, w.kofi, w.accra, w.deep, w.other, w.theirs)):
        stamp = f"STAMP{i}{t.id[-6:]}"
        w.stamp[t.id] = stamp
        await _stored(store, t, [(f"{stamp}-B1", 3884, "2026-11-09"), (f"{stamp}-B2", 120000, "2026-11-12")])
    w.keys = {}
    for name in ("acme", "kofi", "other", "deep"):
        full, _ = await store.issue_key(getattr(w, name).id, scope=READ)
        w.keys[name] = full
    w.admin, _ = await store.issue_key(w.acme.id, scope=ADMIN)
    return w


@pytest.fixture
async def client(world):
    from okwan_api.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        yield c


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _ctx(key: str) -> SimpleNamespace:
    """A tool call's context, as the HTTP transport hands it: lowercased headers."""
    return SimpleNamespace(headers={"authorization": f"Bearer {key}"})


def _tool(name: str):
    from okwan_query.mcp_http import build_server

    return build_server()._tool_manager.get_tool(name).fn


async def _sql(client, key: str, sql: str) -> httpx.Response:
    return await client.post("/v1/query", headers=_auth(key), json={"sql": sql})


# ── one definition, three surfaces ───────────────────────────────────

def test_the_sql_table_is_the_model():
    from okwan_query.catalog import find
    from okwan_query.types import columns_for

    table = find("exceptions.open_items")
    assert list(table.columns) == columns_for(OpenItem) and table.model is OpenItem


async def test_rest_sql_and_mcp_answer_the_same_rows(world, client):
    key = world.keys["acme"]
    rest = (await client.get("/v1/exceptions?limit=1000&state=all", headers=_auth(key))).json()
    sql = (await _sql(client, key, "SELECT * FROM exceptions.open_items")).json()
    mcp = await _tool("okwan_open_items")(ctx=_ctx(key), state="all", limit=1000)
    assert set(rest["data"][0]) == set(OpenItem.model_fields) == set(sql["columns"])
    as_rest = {(r["merchant_id"], r["item_key"]): r for r in rest["data"]}
    as_mcp = {(r["merchant_id"], r["item_key"]): r for r in mcp["data"]}
    as_sql = {(r["merchant_id"], r["item_key"]): r for r in sql["rows"]}
    assert as_rest == as_mcp and set(as_rest) == set(as_sql) and len(as_rest) == 8
    for k, row in as_rest.items():
        s = as_sql[k]
        assert (s["left_ref"], s["amount_minor"], s["state"], s["what"]) == \
               (row["left_ref"], row["amount_minor"], row["state"], row["what"])
        assert s["dated_on"] == row["dated_on"]


async def test_sql_counts_and_joins_over_the_open_items(world, client):
    r = await _sql(client, world.keys["acme"],
                   "SELECT merchant, count(*) AS n, sum(amount_minor) AS at_stake FROM exceptions.open_items "
                   "WHERE state = 'open' GROUP BY merchant ORDER BY merchant")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"merchant": m, "n": 2, "at_stake": 123884}
                                for m in sorted(["Acme ISV", "Kofi's Store", "Accra Coffee", "Deep Store"])]


# ── a read key reads, and decides nothing ────────────────────────────

async def test_a_read_key_reads_decisions_and_suggestions_and_makes_none(world, client):
    [run] = await world.store.newest_runs_all([world.kofi.id], ok_only=True, with_rows=True)
    first, second = open_items(world.kofi.id, run.name, run.kind, run.rows, NOW)
    await world.store.add_decision(
        world.kofi.id, kind="pair", name="batches_deposits", key=first["key"], status="explained", owner=None,
        note="held by the processor", actor_tenant_id=world.acme.id, actor_name="Acme ISV", credential="session",
        run_id=run.id, reads_files=True, idempotency_key="i1", request_hash="h1", at=NOW, terms=terms(first))
    await world.store.add_suggestion(
        world.kofi.id, kind="pair", name="batches_deposits", key=second["key"], terms=terms(second), run_id=run.id,
        reads_files=True, status="none", note="no deposit fits", evidence=[], confidence="low", model="m",
        actor_tenant_id=world.acme.id, actor_name="Acme ISV", credential="session", at=NOW)
    key = world.keys["kofi"]
    opened = (await client.get("/v1/exceptions", headers=_auth(key))).json()["data"]
    assert first["key"] not in {r["item_key"] for r in opened}, "an explained item is not open"
    [suggested] = [r for r in opened if r["item_key"] == second["key"]]
    assert (suggested["suggestion_status"], suggested["suggestion_note"], suggested["suggestion_answer"]) == \
           ("none", "no deposit fits", None)
    closed = (await client.get("/v1/exceptions?state=closed", headers=_auth(key))).json()["data"]
    [explained] = [r for r in closed if r["item_key"] == first["key"]]
    assert (explained["state"], explained["decision_status"], explained["decision_note"],
            explained["decided_by"], explained["decision_changed"]) == \
           ("closed", "explained", "held by the processor", "Acme ISV", False)
    # And none of it can be changed with the key that read it.
    decide = f"/v1/tenants/{world.kofi.id}/exceptions/pair/batches_deposits/{second['key']}/decisions"
    made = await client.post(decide, headers={**_auth(key), "Idempotency-Key": "x1"},
                             json={"status": "explained", "note": "an agent closed it"})
    asked = await client.post(f"/v1/tenants/{world.kofi.id}/exceptions/pair/batches_deposits/{second['key']}"
                              "/suggestions", headers=_auth(key))
    assert (made.status_code, asked.status_code) == (403, 403)
    assert len(await world.store.decision_history(world.kofi.id, "pair", "batches_deposits", second["key"])) == 0


async def test_a_decision_that_no_longer_holds_reads_as_open(world, client):
    [run] = await world.store.newest_runs_all([world.accra.id], ok_only=True, with_rows=True)
    item = open_items(world.accra.id, run.name, run.kind, run.rows, NOW)[0]
    await world.store.add_decision(
        world.accra.id, kind="pair", name="batches_deposits", key=item["key"], status="resolved", owner=None,
        note="paid", actor_tenant_id=world.acme.id, actor_name="Acme ISV", credential="session",
        run_id=run.id, reads_files=True, idempotency_key="i2", request_hash="h2", at=NOW,
        terms='[1,"USD","2026-11-09"]')                    # made on other terms
    rows = (await client.get(f"/v1/exceptions?merchant={world.accra.id}",
                             headers=_auth(world.keys["acme"]))).json()["data"]
    [row] = [r for r in rows if r["item_key"] == item["key"]]
    assert (row["state"], row["decision_status"], row["decision_changed"]) == ("open", "resolved", True)


# ── a key reads its own tenant and below, and nothing else ───────────

SEES = {"acme": ("acme", "kofi", "accra", "deep"), "kofi": ("kofi", "deep"), "deep": ("deep",),
        "other": ("other", "theirs")}


def _stamps(world, text: str) -> set[str]:
    return {s for s in world.stamp.values() if s in text}


@pytest.mark.parametrize("who", list(SEES))
async def test_each_surface_answers_only_the_keys_tenant_and_below(world, client, who):
    want = {world.stamp[getattr(world, t).id] for t in SEES[who]}
    key = world.keys[who]
    rest = await client.get("/v1/exceptions?state=all&limit=1000", headers=_auth(key))
    sql = await _sql(client, key, "SELECT * FROM exceptions.open_items")
    mcp = await _tool("okwan_open_items")(ctx=_ctx(key), state="all", limit=1000)
    for text in (rest.text, sql.text, json.dumps(mcp, default=str)):
        assert _stamps(world, text) == want


async def test_back_to_back_callers_never_see_each_other(world, client):
    for who in ("acme", "other", "kofi", "other", "deep", "acme"):
        sql = await _sql(client, world.keys[who], "SELECT left_ref FROM exceptions.open_items")
        want = {world.stamp[getattr(world, t).id] for t in SEES[who]}
        assert _stamps(world, sql.text) == want, who


async def test_a_merchant_outside_the_keys_reach_reads_as_missing(world, client):
    key = world.keys["kofi"]
    for outside in (world.acme.id, world.accra.id, world.other.id, world.theirs.id, "ten_0000000000000000"):
        r = await client.get(f"/v1/exceptions?merchant={outside}", headers=_auth(key))
        assert r.status_code == 404, outside
        out = await _tool("okwan_open_items")(ctx=_ctx(key), merchant=outside)
        assert out["data"] == [] and "no such tenant" in out["error"]
    inside = await client.get(f"/v1/exceptions?merchant={world.deep.id}", headers=_auth(key))
    assert inside.status_code == 200 and _stamps(world, inside.text) == {world.stamp[world.deep.id]}


async def test_no_key_no_session_and_no_other_way_in(world, client):
    from okwan_vault.accounts import SESSION_PREFIX, new_token

    assert (await client.get("/v1/exceptions")).status_code == 401
    session, token_hash = new_token(SESSION_PREFIX)
    await world.store.create_session(world.acme.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    assert (await client.get("/v1/exceptions", headers=_auth(session))).status_code == 401, \
        "the dashboard has its own list; a session is not a key"
    out = await _tool("okwan_open_items")(ctx=SimpleNamespace(headers={}))
    assert out["data"] == [] and "API key" in out["error"]


async def test_depth_is_the_accounts_reach(world, client):
    parent = world.deep
    for level in range(MAX_DEPTH):
        parent = await world.store.create_tenant(f"level {level}", parent_id=parent.id)
        await _stored(world.store, parent, [(f"LEVEL{level}-B", 100, "2026-11-10")])
    r = await client.get("/v1/exceptions?limit=1000", headers=_auth(world.keys["acme"]))
    levels = {row["left_ref"] for row in r.json()["data"] if row["left_ref"].startswith("LEVEL")}
    # deep is two below acme: MAX_DEPTH counts from the key's own tenant.
    assert levels == {f"LEVEL{i}-B" for i in range(MAX_DEPTH - 2)}


# ── free, logged, limited and bounded ────────────────────────────────

async def test_reading_is_not_metered_and_is_logged(world, client):
    key = world.keys["acme"]
    await client.get("/v1/exceptions", headers=_auth(key))
    await _sql(client, key, "SELECT count(*) FROM exceptions.open_items")
    await _tool("okwan_open_items")(ctx=_ctx(key))
    assert await world.store.usage_since(world.acme.id, NOW - timedelta(days=365)) == 0
    rows = world.store._access
    got = {(r["surface"], r["target"]) for r in rows if r["tenant_id"] == world.acme.id}
    assert {("rest:exceptions", "/v1/exceptions"), ("rest:query", "exceptions.open_items"),
            ("mcp:open_items", "okwan_open_items")} <= got
    assert all(r["credential"] == key[:12] for r in rows if r["tenant_id"] == world.acme.id)


async def test_reads_are_limited_per_account_on_every_surface(world, client):
    from okwan_api.ratelimit import OPEN_ITEMS_TENANT

    key = world.keys["kofi"]
    for _ in range(OPEN_ITEMS_TENANT.limit):
        assert (await client.get("/v1/exceptions", headers=_auth(key))).status_code == 200
    rest = await client.get("/v1/exceptions", headers=_auth(key))
    sql = await _sql(client, key, "SELECT * FROM exceptions.open_items")
    mcp = await _tool("okwan_open_items")(ctx=_ctx(key))
    assert (rest.status_code, sql.status_code) == (429, 429) and int(rest.headers["Retry-After"]) >= 1
    assert "retry_after_seconds" in mcp and mcp["data"] == []
    other = await client.get("/v1/exceptions", headers=_auth(world.keys["other"]))
    assert other.status_code == 200, "another account has its own allowance"


async def test_a_read_stops_at_its_bound_and_says_so(world, client, monkeypatch):
    monkeypatch.setattr(oi, "READ_CHUNK", 1)
    monkeypatch.setattr(oi, "READ_MERCHANTS", 2)
    key = world.keys["acme"]
    rest = (await client.get("/v1/exceptions", headers=_auth(key))).json()
    assert (rest["merchants_read"], rest["merchants_not_read"], rest["total"]) == (2, 2, 4)
    sql = (await _sql(client, key, "SELECT count(*) AS n FROM exceptions.open_items")).json()
    assert sql["rows"] == [{"n": 4}] and sql["merchants_not_read"] == {"exceptions.open_items": 2}
    one = (await client.get(f"/v1/exceptions?merchant={world.deep.id}", headers=_auth(key))).json()
    assert (one["total"], one["merchants_not_read"]) == (2, 0), "one merchant can be read on its own"


async def test_a_listing_holds_the_oldest_and_counts_the_rest(world, client):
    many = [(f"MANY-{i:04d}", 1000 + i, f"2026-10-{1 + i % 28:02d}") for i in range(300)]
    await _stored(world.store, world.accra, many, at=NOW + timedelta(hours=1))
    r = (await client.get(f"/v1/exceptions?merchant={world.accra.id}&limit=5",
                          headers=_auth(world.keys["acme"]))).json()
    assert (r["listed"], r["total"]) == (5, 300)
    days = [row["dated_on"] for row in r["data"]]
    assert days == sorted(days) and days[0] == "2026-10-01", "oldest first"


async def test_without_a_key_the_table_says_how_to_read_it():
    from okwan_core import CredentialError
    from okwan_query.session import QuerySession

    session = QuerySession()
    try:
        with pytest.raises(CredentialError, match="Okwan API key"):
            await session.query("SELECT * FROM exceptions.open_items")
    finally:
        session.close()


async def test_bad_parameters_are_refused_alike(world, client):
    key = world.keys["acme"]
    for q in ("state=deleted", "limit=0", "limit=1001", f"merchant={'x' * 65}"):
        assert (await client.get(f"/v1/exceptions?{q}", headers=_auth(key))).status_code == 422, q
    tool = _tool("okwan_open_items")
    for kw in ({"state": "deleted"}, {"limit": 0}, {"limit": 1001}):
        assert (await tool(ctx=_ctx(key), **kw))["data"] == []


# ── Postgres keeps the same promises ─────────────────────────────────

@pytest.mark.skipif(not DSN, reason="no vault database configured")
async def test_postgres_reads_the_subtree_and_each_items_newest_suggestion():
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    tag = uuid.uuid4().hex[:6]
    root = await store.create_tenant(f"Sub {tag}")
    try:
        chain = [root]
        for level in range(MAX_DEPTH + 2):
            chain.append(await store.create_tenant(f"level {level} {tag}", parent_id=chain[-1].id))
        side = await store.create_tenant(f"side {tag}", parent_id=root.id)
        got = await store.subtree(root.id, MAX_DEPTH)
        assert [t.id for t in got][:1] == [root.id] and len(got) == MAX_DEPTH + 2
        assert {t.id for t in got} == {t.id for t in chain[:MAX_DEPTH + 1]} | {side.id}
        assert await store.subtree(f"ten_{'0' * 16}", MAX_DEPTH) == []
        run = await _stored(store, side, [("B-1", 100, "2026-11-10"), ("B-2", 200, "2026-11-11")])
        a, b = open_items(side.id, run.name, run.kind, run.rows, NOW)
        for n, (item, at) in enumerate(((a, NOW), (a, NOW + timedelta(hours=1)), (b, NOW))):
            await store.add_suggestion(
                side.id, kind="pair", name="batches_deposits", key=item["key"], terms=terms(item), run_id=run.id,
                reads_files=True, status="none", note=f"note {n}", evidence=[], confidence="low", model="m",
                actor_tenant_id=root.id, actor_name="Sub", credential="session", at=at)
        newest = await store.newest_suggestions([(side.id, a["key"]), (side.id, b["key"]), (side.id, "none")])
        assert {k[3]: s.note for k, s in newest.items()} == {a["key"]: "note 1", b["key"]: "note 2"}
        assert await store.newest_suggestions([(chain[1].id, a["key"])]) == {}, "keyed by tenant"
    finally:
        await store.pool.execute("DELETE FROM tenants WHERE id = $1", root.id)
        await store.close()
