"""An account's sandbox (okwan_api/sandbox.py).

What must hold:

- a live account, and only a live account, makes its sandbox, once; it is
  on the account's API version and goes with the account;
- a sandbox's keys, and its merchants' keys, are test keys (`okw_test_`),
  and a live tenant's never are; the tenant decides, not the caller;
- a test key reaches the sandbox and nothing live, and a live key never
  reaches the sandbox, on every surface that names a tenant or lists one;
- the sandbox and each merchant made in it start with the published sample
  week, moved by whole weeks to end in the last seven days, and the check
  over it finds what the samples' README says, on any day;
- a sandbox holds no rail credential: saving one and connecting Gusto are
  refused, and a credential stored anyway is read as empty;
- its requests count against the sandbox, not the account's plan;
- the live account revokes its test keys, and no one else's.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
import okwan_api.main  # noqa: F401  (registers the connectors and declarations)
import pytest
from okwan_api import sandbox
from okwan_api import webhooks as wh
from okwan_api.auth import set_store
from okwan_files import prepare
from okwan_recon import declarations
from okwan_recon.engine import match
from okwan_vault import (
    ADMIN,
    READ,
    EnvMasterKey,
    MemoryStore,
    PostgresStore,
    SandboxRefused,
    apikey,
    new_key,
)
from okwan_vault.accounts import SESSION_PREFIX, new_token
from okwan_vault.usage import quota

from tests.test_webhooks import HOST, _verify, receiver  # noqa: F401  (a fixture)

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not DSN, reason="no vault database configured")
SAMPLES = Path(__file__).resolve().parents[1] / "docs" / "samples" / "imports"


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
    w.acme = await store.create_tenant("Acme ISV")
    w.shop = await store.create_tenant("Kofi's Store", parent_id=w.acme.id)
    w.other = await store.create_tenant("Other ISV")
    w.admin = (await store.issue_key(w.acme.id, scope=ADMIN))[0]
    w.read = (await store.issue_key(w.acme.id, scope=READ))[0]
    w.shop_admin = (await store.issue_key(w.shop.id, scope=ADMIN))[0]
    w.other_admin = (await store.issue_key(w.other.id, scope=ADMIN))[0]
    from okwan_api.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        w.client = c
        yield w
    if request.param == "postgres":
        for t in (w.acme, w.other):
            await store.pool.execute("DELETE FROM tenants WHERE id = $1", t.id)
        await store.close()


def _auth(key):
    return {"Authorization": f"Bearer {key}"}


async def _sandbox(w, who="admin"):
    r = await w.client.post("/v1/sandbox", headers=_auth(getattr(w, who)))
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _test_key(w, scope=ADMIN, who="admin"):
    r = await w.client.post("/v1/sandbox/keys", headers=_auth(getattr(w, who)), json={"scope": scope})
    assert r.status_code == 201, r.text
    return r.json()


async def _check(w, key, tenant_id):
    r = await w.client.post(f"/v1/tenants/{tenant_id}/reconciliations/batches_deposits", headers=_auth(key))
    assert r.status_code == 200, r.text
    return r.json()


async def _open(w, key):
    r = await w.client.get("/v1/exceptions", headers=_auth(key))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _found(items):
    return sorted((i["merchant_id"], i["left_ref"] or i["right_ref"]) for i in items)


# ── making it ──────────────────────────────────────────────────────────

async def test_a_live_account_makes_its_sandbox_once(world):
    w = world
    r = await w.client.post("/v1/sandbox", headers=_auth(w.admin))
    assert r.status_code == 201
    made = r.json()
    assert made["sandbox_of"] == w.acme.id and made["api_version"] == w.acme.api_version
    again = await w.client.post("/v1/sandbox", headers=_auth(w.admin))
    assert again.status_code == 200 and again.json()["id"] == made["id"]
    assert (await w.client.get("/v1/sandbox", headers=_auth(w.admin))).json()["id"] == made["id"]
    # A dashboard session of the account reaches the same one.
    full, token_hash = new_token(SESSION_PREFIX)
    await w.store.create_session(w.acme.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    assert (await w.client.get("/v1/sandbox", headers=_auth(full))).json()["id"] == made["id"]
    # Another account has none until it makes its own, and it is another.
    assert (await w.client.get("/v1/sandbox", headers=_auth(w.other_admin))).status_code == 404
    assert (await _sandbox(w, "other_admin"))["id"] != made["id"]


async def test_only_a_live_account_has_a_sandbox(world):
    w = world
    made = await _sandbox(w)
    test_admin = (await _test_key(w))["secret"]
    for key in (w.read, w.shop_admin, test_admin):
        r = await w.client.post("/v1/sandbox", headers=_auth(key))
        assert r.status_code == 403, (key[:12], r.text)
    with pytest.raises(SandboxRefused):
        await w.store.make_sandbox(w.shop.id)
    with pytest.raises(SandboxRefused):
        await w.store.make_sandbox(made["id"])


# ── keys ───────────────────────────────────────────────────────────────

async def test_a_sandboxs_keys_are_test_keys_and_a_live_tenants_never_are(world):
    w = world
    made = await _sandbox(w)
    issued = await _test_key(w)
    assert issued["secret"].startswith("okw_test_") and issued["prefix"] == issued["secret"][:17]
    assert issued["tenant_id"] == made["id"] and issued["scope"] == ADMIN
    assert (await _test_key(w, READ))["secret"].startswith("okw_test_")
    # A merchant made in the sandbox is in it, and its keys are test keys.
    r = await w.client.post("/v1/tenants", headers=_auth(issued["secret"]), json={"name": "Sandbox Café"})
    assert r.status_code == 201 and r.json()["sandbox"] is True
    r = await w.client.post(f"/v1/tenants/{r.json()['id']}/keys", headers=_auth(issued["secret"]))
    assert r.json()["secret"].startswith("okw_test_")
    # Live keys, however issued, never are.
    for t in (w.acme, w.shop):
        full, record = await w.store.issue_key(t.id, scope=ADMIN)
        assert full.startswith("okw_") and not apikey.is_test(full) and len(record.prefix) == 12
    r = await w.client.post(f"/v1/tenants/{w.shop.id}/keys", headers=_auth(w.admin))
    assert not apikey.is_test(r.json()["secret"])


async def test_the_live_account_revokes_its_test_keys_and_no_one_elses(world):
    w = world
    await _sandbox(w)
    issued = await _test_key(w)
    assert (await w.client.get("/v1/tenants", headers=_auth(issued["secret"]))).status_code == 200
    # A key of a merchant in the sandbox too.
    m = (await w.client.post("/v1/tenants", headers=_auth(issued["secret"]), json={"name": "Shop"})).json()
    deep = (await w.client.post(f"/v1/tenants/{m['id']}/keys", headers=_auth(issued["secret"]),
                                json={"scope": ADMIN})).json()
    await _sandbox(w, "other_admin")
    theirs = await _test_key(w, who="other_admin")
    live_key_id = (await w.store.issue_key(w.shop.id, scope=READ))[1].id
    for key_id in (theirs["key_id"], live_key_id, "key_0000000000000000"):
        r = await w.client.delete(f"/v1/sandbox/keys/{key_id}", headers=_auth(w.admin))
        assert r.status_code == 404, key_id
    for key in (issued, deep):
        r = await w.client.delete(f"/v1/sandbox/keys/{key['key_id']}", headers=_auth(w.admin))
        assert r.status_code == 204
        assert (await w.client.get("/v1/tenants", headers=_auth(key["secret"]))).status_code == 401
    assert (await w.client.get("/v1/tenants", headers=_auth(theirs["secret"]))).status_code == 200


# ── the sample week ────────────────────────────────────────────────────

def test_the_seeded_week_is_the_published_sample():
    assert sandbox.BATCHES_CSV == (SAMPLES / "processor-batches-2026-10.csv").read_text()
    assert sandbox.STATEMENT_CSV == (SAMPLES / "bank-statement-2026-10.csv").read_text()


def _week(today: date):
    rows = {}
    for filename, text, mapping in sandbox.SEED:
        prepared = prepare(sandbox.moved(text, today).encode(), filename, mapping)
        rows[mapping.kind] = [{**f, "file_id": filename, "row": n, "layout": mapping.name}
                              for n, _day, f in prepared.normalized.rows]
    return rows


@pytest.mark.parametrize("today", [date(2026, 11, 24), date(2027, 2, 14), date(2027, 7, 4), date(2031, 1, 1)])
def test_the_week_moves_by_whole_weeks_and_finds_the_same_on_any_day(today):
    rows = _week(today)
    batches, deposits = rows["settlement_batches"], rows["bank_deposits"]
    days = sorted({date.fromisoformat(str(r["closed_on"])) for r in batches}
                  | {date.fromisoformat(str(r["posted_on"])) for r in deposits})
    assert 0 <= (today - days[-1]).days < 7
    originals = sorted({date(2026, 10, d) for d in (5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16)})
    assert [d.weekday() for d in days] == [d.weekday() for d in originals]
    result = match(declarations.batches_deposits, batches, [r for r in deposits if r["amount_minor"] > 0])
    paid = sorted(sorted(m["batch_id"] for m in (p.members or [p.left])) for p in result.matched)
    assert paid == [["B-1005"], ["B-1006"], ["B-1007"], ["B-1008"], ["B-1009", "B-1010", "B-1011", "B-1012"]]
    def row(r, side):
        return r if isinstance(r, dict) else getattr(r, side)

    assert [row(r, "left")["batch_id"] for r in result.unmatched_left] == ["B-1013"]
    assert [row(r, "right")["fitid"] for r in result.unmatched_right] == ["20261016001"]  # the mobile deposit


async def test_the_sandbox_and_its_merchants_start_with_the_week_and_the_check_finds_it(world):
    w = world
    made = await _sandbox(w)
    key = (await _test_key(w))["secret"]
    m = (await w.client.post("/v1/tenants", headers=_auth(key), json={"name": "Sandbox Café"})).json()
    for tenant_id in (made["id"], m["id"]):
        files = (await w.client.get(f"/v1/tenants/{tenant_id}/imports", headers=_auth(key))).json()["imports"]
        assert sorted(f["filename"] for f in files) == ["sample-bank-statement.csv", "sample-processor-batches.csv"]
        await _check(w, key, tenant_id)
    # What the samples' README says the week shows: one batch not deposited,
    # one deposit no batch explains; in the sandbox and in its merchant.
    assert _found(await _open(w, key)) == sorted(
        (t, ref) for t in (made["id"], m["id"]) for ref in ("B-1013", "20261016001"))
    # A live merchant made the same way starts with nothing.
    live = (await w.client.post("/v1/tenants", headers=_auth(w.admin), json={"name": "Live Café"})).json()
    assert live["sandbox"] is False
    assert (await w.client.get(f"/v1/tenants/{live['id']}/imports", headers=_auth(w.admin))).json()["imports"] == []
    with pytest.raises(SandboxRefused):
        await sandbox.seed(live["id"])


async def test_a_sandbox_whose_week_did_not_go_in_is_not_kept(world, monkeypatch):
    w = world

    async def broken(tenant_id, now=None):
        raise RuntimeError("the store went away")

    monkeypatch.setattr(sandbox, "seed", broken)
    with pytest.raises(RuntimeError):
        await w.client.post("/v1/sandbox", headers=_auth(w.admin))
    assert await w.store.sandbox_for(w.acme.id) is None
    monkeypatch.undo()
    made = await _sandbox(w)
    key = (await _test_key(w))["secret"]
    files = (await w.client.get(f"/v1/tenants/{made['id']}/imports", headers=_auth(key))).json()["imports"]
    assert len(files) == 2


# ── apart from everything live ─────────────────────────────────────────

async def test_a_test_key_reaches_the_sandbox_only_and_a_live_key_never_does(world):
    w = world
    made = await _sandbox(w)
    key = (await _test_key(w))["secret"]
    read_key = (await _test_key(w, READ))["secret"]
    await _check(w, key, made["id"])
    for path in (f"/v1/tenants/{w.acme.id}/imports", f"/v1/tenants/{w.shop.id}/runs",
                 f"/v1/tenants/{w.acme.id}/exceptions"):
        assert (await w.client.get(path, headers=_auth(key))).status_code == 404, path
    for path in (f"/v1/tenants/{made['id']}/imports", f"/v1/tenants/{made['id']}/runs",
                 f"/v1/tenants/{made['id']}/exceptions"):
        assert (await w.client.get(path, headers=_auth(w.admin))).status_code == 404, path
    r = await w.client.post(f"/v1/tenants/{w.shop.id}/keys", headers=_auth(key))
    assert r.status_code == 404
    r = await w.client.post(f"/v1/tenants/{made['id']}/keys", headers=_auth(w.admin))
    assert r.status_code == 404
    listed = (await w.client.get("/v1/tenants", headers=_auth(w.admin))).json()
    assert made["id"] not in {c["id"] for c in listed["children"]}
    assert all(i["merchant_id"] == made["id"] for i in await _open(w, read_key))
    assert all(i["merchant_id"] != made["id"] for i in await _open(w, w.read))


async def test_the_sandbox_holds_no_rail_credential(world):
    w = world
    made = await _sandbox(w)
    key = (await _test_key(w))["secret"]
    r = await w.client.put(f"/v1/tenants/{made['id']}/credentials", headers=_auth(key),
                           json={"connector": "stripe", "field": "secret_key", "value": "sk_test_x"})
    assert r.status_code == 403 and "no rail credentials" in r.json()["detail"]
    r = await w.client.post(f"/v1/tenants/{made['id']}/connectors/gusto/authorize", headers=_auth(key))
    assert r.status_code == 403
    with pytest.raises(SandboxRefused):
        await w.store.put_credential(made["id"], "stripe", "secret_key", "sk_test_x")
    # One stored anyway, past every route, is read as nothing.
    await _plant(w, made["id"])
    assert await w.store.credentials_for(made["id"], "stripe", ("secret_key",)) == {"secret_key": ""}
    # A live tenant's are read as ever.
    await w.store.put_credential(w.shop.id, "stripe", "secret_key", "sk_test_live_shop")
    assert await w.store.credentials_for(w.shop.id, "stripe", ("secret_key",)) == {"secret_key": "sk_test_live_shop"}


async def _plant(w, tenant_id):
    if isinstance(w.store, MemoryStore):
        sandboxed = w.store._tenants[tenant_id]
        from dataclasses import replace

        w.store._tenants[tenant_id] = replace(sandboxed, sandbox=False)
        await w.store.put_credential(tenant_id, "stripe", "secret_key", "sk_test_planted")
        w.store._tenants[tenant_id] = sandboxed
    else:
        await w.store.pool.execute("UPDATE tenants SET sandbox = false, sandbox_of = NULL WHERE id = $1", tenant_id)
        await w.store.put_credential(tenant_id, "stripe", "secret_key", "sk_test_planted")
        await w.store.pool.execute("UPDATE tenants SET sandbox = true, sandbox_of = $2 WHERE id = $1",
                                   tenant_id, w.acme.id)


async def test_the_sandbox_counts_against_its_own_allowance(world):
    w = world
    made = await _sandbox(w)
    key = (await _test_key(w))["secret"]
    before = (await quota(w.store, w.acme.id)).used
    await _check(w, key, made["id"])
    assert (await quota(w.store, w.acme.id)).used == before
    assert (await quota(w.store, made["id"])).used >= 1


async def test_the_sandbox_goes_with_its_account(world):
    w = world
    made = await _sandbox(w)
    key = (await _test_key(w))["secret"]
    m = (await w.client.post("/v1/tenants", headers=_auth(key), json={"name": "Shop"})).json()
    gone = await w.store.delete_tenant(w.acme.id)
    assert {w.acme.id, w.shop.id, made["id"], m["id"]} <= set(gone)
    assert await w.store.sandbox_for(w.acme.id) is None
    assert await w.store.get_tenant(made["id"]) is None and await w.store.get_tenant(m["id"]) is None
    assert (await w.client.get("/v1/tenants", headers=_auth(key))).status_code == 401
    assert await w.store.get_tenant(w.other.id) is not None


async def test_the_sandbox_sends_webhooks_as_live_does(world, receiver):  # noqa: F811
    w = world
    made = await _sandbox(w)
    key = (await _test_key(w))["secret"]
    hook = (await w.client.post("/v1/webhooks", headers=_auth(key), json={"url": f"https://{HOST}/sb"})).json()
    await _check(w, key, made["id"])
    assert await wh.deliver_due() >= 1
    bodies = [_verify(hook["secret"], r) for r in receiver.requests]
    assert {b["account"] for b in bodies} == {made["id"]}
    assert "B-1013" in {b["data"]["left_ref"] for b in bodies}


# ── rows from before the columns ───────────────────────────────────────

def _block() -> str:
    import re

    schema = (Path(__file__).resolve().parents[1] / "packages/vault/okwan_vault/schema.sql").read_text()
    return next(b for b in re.findall(r"DO \$\$.*?END \$\$;", schema, re.DOTALL) if "column_name = 'sandbox'" in b)


@needs_db
async def test_rows_from_before_are_live_and_only_a_sandbox_root_names_an_account():
    con = await asyncpg.connect(DSN)
    schema = f"sandbox_mig_{uuid.uuid4().hex[:8]}"
    try:
        await con.execute(f"CREATE SCHEMA {schema}")
        await con.execute(f"SET search_path TO {schema}")
        await con.execute("CREATE TABLE tenants (id text PRIMARY KEY, name text NOT NULL, "
                          "parent_id text REFERENCES tenants(id) ON DELETE CASCADE)")
        await con.execute("INSERT INTO tenants (id, name) VALUES ('ten_old', 'Old'), ('ten_other', 'Other')")
        for _ in range(2):  # a restart runs it again
            await con.execute(_block())
        assert await con.fetchval("SELECT bool_or(sandbox) FROM tenants") is False
        await con.execute("INSERT INTO tenants (id, name, sandbox, sandbox_of) VALUES ('ten_sb', 'S', true, 'ten_old')")
        for bad in ("INSERT INTO tenants (id, name, sandbox_of) VALUES ('ten_x', 'X', 'ten_other')",
                    ("INSERT INTO tenants (id, name, parent_id, sandbox, sandbox_of) "
                     "VALUES ('ten_y', 'Y', 'ten_old', true, 'ten_other')")):
            with pytest.raises(asyncpg.CheckViolationError):
                await con.execute(bad)
        with pytest.raises(asyncpg.UniqueViolationError):
            await con.execute("INSERT INTO tenants (id, name, sandbox, sandbox_of) VALUES ('ten_z', 'Z', true, 'ten_old')")
        await con.execute("DELETE FROM tenants WHERE id = 'ten_old'")
        assert await con.fetchval("SELECT count(*) FROM tenants WHERE id = 'ten_sb'") == 0
    finally:
        await con.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await con.close()
