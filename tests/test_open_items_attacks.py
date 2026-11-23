"""What an attacker's pass found in the open items as data, turned around.

The open items as data for API keys (okwan_api.open_items, the SQL table
`exceptions.open_items`, the MCP tool `okwan_open_items`) were attacked
before they were merged, by a fresh agent with an attacker's brief and none
of the author's conclusions (docs/audit/red-team.md, 2026-11-23). It read
nothing of another tenant's, and proved eight faults, each with an exploit
that ran. Each is closed and its exploit kept below, turned around: a test
passes only when the attack fails.

1. One SQL read of the open items held the event loop for seconds:
   DuckDB's row-at-a-time insert ran on it.
2. A query that failed once the table was loaded left no access-log row,
   though its error carried the rows.
3. An expired file's result was served on every surface.
4. A read went three times past its item bound, and a listing held far
   more than twice what it listed.
5. The per-account limit was the key's tenant's, so an account's merchants'
   keys multiplied it.
6. A suggestion made when the item put something else at stake was served
   as Okwan's current one.
7. A result cut at its row cap read as whole.
8. A read cost two statements a check a merchant.
"""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest
from okwan_api import open_items as oi
from okwan_recon.exceptions import open_items, terms
from okwan_vault import READ

from tests import test_open_items_data as data
from tests.test_open_items_data import NOW, _auth, _ctx, _sql, _stored, _tool

#: The fixtures, bound here so pytest finds them in this module.
world, client = data.world, data.client


async def _ticking(coro):
    """Run `coro` beside a 5 ms ticker; the longest the ticker waited."""
    gaps: list[float] = []
    done = False

    async def tick():
        last = time.perf_counter()
        while not done:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    t = asyncio.create_task(tick())
    try:
        out = await coro
    finally:
        done = True
        await t
    return out, max(gaps)


async def _wide(store, tenant, checks: int, rows: int, at=NOW):
    """`checks` of the batches check's results for `tenant`, each holding
    `rows` unpaid batches (results of one check stand in for many)."""
    from okwan_recon.exceptions import carry

    found = [data._unpaid(f"W-{tenant.id[-4:]}-{i:05d}", 100 + i, "2026-11-09") for i in range(rows)]
    return await store.add_run(tenant.id, kind="pair", name="batches_deposits", surface="dashboard",
                               status="ok", started_at=at, finished_at=at,
                               summary={"unmatched_left": rows * checks},
                               rows=carry(tenant.id, "batches_deposits", "pair", found, None, at),
                               error=None, reads_files=False)


# ── 1. a SQL read never holds the event loop ─────────────────────────

async def test_a_sql_read_of_the_open_items_never_holds_the_event_loop(world, client):
    """The exploit: 2,000 items read through SQL held the loop about 5 s."""
    await _wide(world.store, world.kofi, 1, 1000, at=NOW + timedelta(hours=1))
    await _wide(world.store, world.accra, 1, 1000, at=NOW + timedelta(hours=1))
    key = world.keys["acme"]
    rest, held = await _ticking(_sql(client, key, "SELECT count(*) AS n FROM exceptions.open_items"))
    assert rest.status_code == 200 and rest.json()["rows"][0]["n"] >= 2000
    assert held < 0.5, f"REST held the loop {held:.2f} s"
    mcp, held = await _ticking(_tool("okwan_query")(ctx=_ctx(key), sql="SELECT count(*) AS n FROM exceptions.open_items"))
    assert mcp["rows"][0]["n"] >= 2000 and held < 0.5, f"MCP held the loop {held:.2f} s"


# ── 2. a read is logged when it is read, whatever the query does ─────

async def test_a_query_that_fails_after_reading_is_still_in_the_access_log(world, client):
    """The exploit: `SELECT error(string_agg(...))` returned every row in
    its error, and no access row was written."""
    key = world.keys["kofi"]
    sql = "SELECT error(string_agg(left_ref, ' ~ ')) FROM exceptions.open_items"
    r = await _sql(client, key, sql)
    assert r.status_code == 400
    out = await _tool("okwan_query")(ctx=_ctx(key), sql=sql)
    assert "error" in out
    rows = [x for x in world.store._access if x["tenant_id"] == world.kofi.id]
    got = {(x["surface"], x["target"]) for x in rows}
    assert {("rest:query", "exceptions.open_items"), ("mcp:query", "exceptions.open_items")} <= got
    assert all(x["credential"] == key[:12] for x in rows)


# ── 3. an expired file is never read ─────────────────────────────────

async def test_an_expired_files_result_is_read_on_no_surface(world, client):
    long_ago = NOW - timedelta(days=4000)
    await world.store.add_import(
        world.accra.id, kind="settlement_batches", filename="old.csv", format="csv", layout="custom", mapping={},
        sha256="0" * 64, rows=[(2, long_ago.date(), {"batch_id": "B-5"})], skipped=[], card_columns=[],
        first_date=long_ago.date(), last_date=long_ago.date(), now=long_ago)
    await _stored(world.store, world.accra, [("EXPIRED-B-5", 3884, "2026-11-09")], at=NOW + timedelta(hours=1))
    key = world.keys["acme"]
    rest = await client.get("/v1/exceptions?state=all&limit=1000", headers=_auth(key))
    sql = await _sql(client, key, "SELECT left_ref FROM exceptions.open_items")
    mcp = await _tool("okwan_open_items")(ctx=_ctx(key), state="all", limit=1000)
    for text in (rest.text, sql.text, str(mcp)):
        assert "EXPIRED-B-5" not in text
    assert await world.store.newest_runs_all([world.accra.id]) == [], "the result went with its file"


# ── 4. a read keeps to its bound, and a listing to twice its limit ───

async def test_a_read_stops_within_one_merchant_of_its_bound(world, client, monkeypatch):
    monkeypatch.setattr(oi, "READ_ITEMS", 1500)
    for t in (world.acme, world.kofi, world.accra, world.deep):
        await _wide(world.store, t, 1, 1000, at=NOW + timedelta(hours=1))
    largest = [0]
    real_sorted = sorted

    def spy(items, **kw):
        if isinstance(items, list) and items and isinstance(items[0], dict):   # the listing's rows
            largest[0] = max(largest[0], len(items))
        return real_sorted(items, **kw)

    monkeypatch.setattr(oi, "sorted", spy, raising=False)
    r = (await client.get("/v1/exceptions?limit=1", headers=_auth(world.keys["acme"]))).json()
    assert r["total"] <= 1500 + 1000, "past the bound by one merchant's items at most"
    assert r["merchants_not_read"] >= 1 and r["listed"] == 1
    assert largest[0] <= 3, f"the listing held {largest[0]} items to list 1"


# ── 5. an account's merchants share its allowance ────────────────────

async def test_an_accounts_merchants_share_one_allowance(world, client):
    from okwan_api.ratelimit import OPEN_ITEMS_TENANT

    keys = [world.keys["acme"], world.keys["kofi"], world.keys["deep"]]
    extra, _ = await world.store.issue_key(world.accra.id, scope=READ)
    keys.append(extra)
    ok = 0
    for _ in range(OPEN_ITEMS_TENANT.limit):
        for key in keys:
            ok += (await client.get("/v1/exceptions", headers=_auth(key))).status_code == 200
    assert ok == OPEN_ITEMS_TENANT.limit, f"{ok} reads of one account in a minute"
    assert (await client.get("/v1/exceptions", headers=_auth(world.keys["other"]))).status_code == 200


# ── 6 and 7. a stale suggestion and a cut result say so ──────────────

async def test_a_suggestion_made_on_other_terms_says_it_is_stale(world, client):
    [run] = await world.store.newest_runs_all([world.kofi.id], ok_only=True, with_rows=True)
    first, second = open_items(world.kofi.id, run.name, run.kind, run.rows, NOW)
    for item, on in ((first, terms(first)), (second, '[1,"USD","2026-11-12"]')):
        await world.store.add_suggestion(
            world.kofi.id, kind="pair", name="batches_deposits", key=item["key"], terms=on, run_id=run.id,
            reads_files=True, status="explained", note="a 1.00 fee; nothing to fix", evidence=[],
            confidence="high", model="m", actor_tenant_id=world.acme.id, actor_name="Acme", credential="session",
            at=NOW)
    rows = (await client.get(f"/v1/exceptions?merchant={world.kofi.id}", headers=_auth(world.keys["kofi"]))).json()
    stale = {r["item_key"]: r["suggestion_changed"] for r in rows["data"]}
    assert stale == {first["key"]: False, second["key"]: True}


async def test_a_result_cut_at_its_row_cap_says_so_on_every_surface(world, client):
    await _wide(world.store, world.accra, 5, 1000, at=NOW + timedelta(hours=1))   # found 5,000, kept 1,000
    key = world.keys["acme"]
    rest = (await client.get(f"/v1/exceptions?merchant={world.accra.id}&limit=5", headers=_auth(key))).json()
    assert rest["not_kept"] == 4000 and all(r["result_cut"] for r in rest["data"])
    mcp = await _tool("okwan_open_items")(ctx=_ctx(key), merchant=world.accra.id, limit=5)
    assert mcp["not_kept"] == 4000
    sql = (await _sql(client, key, "SELECT bool_and(result_cut) AS cut FROM exceptions.open_items "
                                    f"WHERE merchant_id = '{world.accra.id}'")).json()
    assert sql["rows"] == [{"cut": True}] and sql["not_kept"] == {"exceptions.open_items": 4000}


# ── 8. a read costs a statement or two a merchant, one at a time ─────

async def test_a_read_is_two_statements_a_merchant_and_one_at_a_time(world, client, monkeypatch):
    calls = {"decisions": 0, "suggestions": 0}
    real_d, real_s = world.store.current_decisions, world.store.newest_suggestions

    async def d(items):
        calls["decisions"] += 1
        return await real_d(items)

    async def s(items):
        calls["suggestions"] += 1
        return await real_s(items)

    monkeypatch.setattr(world.store, "current_decisions", d)
    monkeypatch.setattr(world.store, "newest_suggestions", s)
    for t in (world.kofi, world.accra):            # a second check's result for two merchants
        await world.store.add_run(t.id, kind="across", name="rails", surface="rest", status="ok",
                                  started_at=NOW, finished_at=NOW, summary={}, rows=[], error=None)
    r = (await client.get("/v1/exceptions", headers=_auth(world.keys["acme"]))).json()
    assert calls == {"decisions": r["merchants_read"], "suggestions": r["merchants_read"]} == \
           {"decisions": 4, "suggestions": 4}
    # A second read of the same account while one is under way waits its turn.
    gate = asyncio.Event()

    acme = {world.acme.id, world.kofi.id, world.accra.id, world.deep.id}

    async def slow(items):
        if any(tid in acme for tid, _ in items):   # Acme's read waits; any other goes on
            await gate.wait()
        return await real_d(items)

    monkeypatch.setattr(world.store, "current_decisions", slow)
    first = asyncio.create_task(client.get("/v1/exceptions", headers=_auth(world.keys["acme"])))
    try:
        for _ in range(200):                         # until the first read has taken the account's turn
            if world.acme.id in oi._reading:
                break
            await asyncio.sleep(0.01)
        assert world.acme.id in oi._reading
        second = await asyncio.wait_for(client.get("/v1/exceptions", headers=_auth(world.keys["kofi"])), 5)
        assert second.status_code == 429 and "under way" in second.json()["detail"]
        other = await asyncio.wait_for(client.get("/v1/exceptions", headers=_auth(world.keys["other"])), 5)
        assert other.status_code == 200, "another account reads meanwhile"
    finally:
        gate.set()
    assert (await asyncio.wait_for(first, 5)).status_code == 200


async def test_the_mcp_tool_decrypts_no_credential(world, monkeypatch):
    """Found beside the pass: the tool decrypted every rail credential of the
    tenant for a read that needs none."""
    import okwan_vault

    async def refuse(*a, **kw):
        raise AssertionError("a credential was decrypted")

    monkeypatch.setattr(okwan_vault, "resolver_for", refuse)
    out = await _tool("okwan_open_items")(ctx=_ctx(world.keys["acme"]))
    assert "error" not in out and out["total"] == 8


@pytest.mark.parametrize("bad", ["exceptions.open_items_x", "EXCEPTIONS.OPEN_ITEMS"])
async def test_only_the_callers_items_under_any_spelling(world, client, bad):
    r = await _sql(client, world.keys["deep"], f"SELECT left_ref FROM {bad}")
    stamps = {s for s in world.stamp.values() if s in r.text}
    assert stamps <= {world.stamp[world.deep.id]}
