"""Deleting an account deletes everything in it, and nothing else.

The Privacy Policy promises that a deleted account takes its merchants,
login, saved credentials, keys, check results, usage, support
conversations, tickets and replies with it. Both stores, like
tests/test_ops.py; on Postgres every tenant table is counted directly.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, accounts, cli, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token
from okwan_vault.postgres import TENANT_TABLES

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
NOW = datetime.now(UTC)


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        yield MemoryStore(EnvMasterKey(new_key()))
        return
    if not DSN:
        pytest.skip("no vault database configured")
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


async def _fill(store, name: str) -> dict:
    """An account with one merchant and something in every table."""
    root = await store.create_tenant(name)
    shop = await store.create_tenant(f"{name} shop", parent_id=root.id)
    email = f"{name.lower().replace(' ', '-')}-{os.urandom(3).hex()}@okwan.test"
    await store.add_account(root.id, email, accounts.hash_password("correct horse battery"))
    _, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(root.id, token_hash, NOW + timedelta(hours=1))
    await store.add_signup(email, "hash", f"sig-{os.urandom(4).hex()}", NOW + timedelta(hours=1))
    _, key = await store.issue_key(shop.id, scope="admin")
    await store.put_credential(shop.id, "stripe", "secret_key", "sk_test_DeleteMe")
    await store.record_request(root.id, "rest:stripe")
    await store.set_plan(root.id, "pro")
    await store.add_run(shop.id, kind="across", name="rails", surface="rest", status="ok",
                        started_at=NOW, finished_at=NOW, summary={}, rows=[], error=None)
    conv = await store.save_conversation(root.id, None, [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]}], 1)
    ticket = await store.add_ticket(root.id, conversation_id=conv, severity="sev3",
                                    category="other", summary="hello", notified=True)
    await store.add_reply(root.id, ticket.id, "ops@okwan.test", "hi", True)
    await store.add_import(
        shop.id, kind="bank_deposits", filename="statement.csv", format="csv", layout="custom",
        mapping={}, sha256=f"sha-{os.urandom(4).hex()}", rows=[(2, NOW.date(), {"fitid": "F1"})],
        skipped=[], card_columns=[], first_date=NOW.date(), last_date=NOW.date())
    await store.save_layout(shop.id, "bank-csv", {"kind": "bank_deposits", "columns": {}})
    await store.set_notify(root.id, finding_emails=False)
    await store.notify_token(root.id)
    await store.notify_mailed(root.id, NOW)
    await store.claim_daily_check(shop.id, NOW)
    return {"root": root, "shop": shop, "email": email, "token_hash": token_hash,
            "key": key, "conv": conv, "ticket": ticket}


async def _gone(store, w: dict) -> None:
    root, shop = w["root"].id, w["shop"].id
    assert await store.get_tenant(root) is None and await store.get_tenant(shop) is None
    assert await store.account_login(w["email"]) is None
    assert await store.tenant_for_session(w["token_hash"]) is None
    assert await store.keys_for(shop) == []
    assert await store.connectors_configured(shop) == {}
    assert await store.list_imports(shop) == []
    assert await store.import_rows(shop, "bank_deposits") == []
    assert await store.list_layouts(shop) == []
    assert await store.list_runs(shop) == []
    assert await store.daily_checks([root, shop]) == {}
    assert await store.get_conversation(root, w["conv"]) is None
    assert await store.get_ticket(w["ticket"].id) is None
    assert await store.replies_for(root, w["ticket"].id) == []


async def test_deleting_an_account_takes_everything_in_it(store):
    w = await _fill(store, "Leaving ISV")
    gone = await store.delete_tenant(w["root"].id)
    assert gone == sorted([w["root"].id, w["shop"].id])
    await _gone(store, w)


async def test_another_account_is_untouched(store):
    stay = await _fill(store, "Staying ISV")
    leave = await _fill(store, "Leaving ISV")
    await store.delete_tenant(leave["root"].id)
    assert await store.get_tenant(stay["root"].id) is not None
    assert await store.account_login(stay["email"]) is not None
    assert await store.connectors_configured(stay["shop"].id) == {"stripe": ["secret_key"]}
    assert len(await store.list_runs(stay["shop"].id)) == 1
    assert await store.get_ticket(stay["ticket"].id) is not None


async def test_deleting_a_merchant_leaves_its_account(store):
    w = await _fill(store, "Keeping ISV")
    assert await store.delete_tenant(w["shop"].id) == [w["shop"].id]
    assert await store.get_tenant(w["root"].id) is not None
    assert await store.get_ticket(w["ticket"].id) is not None
    assert await store.keys_for(w["shop"].id) == []


async def test_an_unknown_tenant_deletes_nothing(store):
    assert await store.delete_tenant("ten_0000000000000000") == []


async def test_postgres_keeps_no_row_for_a_deleted_account(store):
    if not isinstance(store, PostgresStore):
        pytest.skip("counts the database's own tables")
    w = await _fill(store, "Leaving ISV")
    ids = [w["root"].id, w["shop"].id]
    await store.delete_tenant(w["root"].id)
    for table in (*TENANT_TABLES, "api_keys", "sessions", "accounts"):
        n = await store.pool.fetchval(f"SELECT count(*) FROM {table} WHERE tenant_id = ANY($1::text[])", ids)
        assert n == 0, table
    assert await store.pool.fetchval("SELECT count(*) FROM signups WHERE email = $1", w["email"]) == 0
    assert await store.pool.fetchval("SELECT count(*) FROM tenants WHERE id = ANY($1::text[])", ids) == 0


# ── the command ────────────────────────────────────────────────────────

class _Closable(MemoryStore):
    async def close(self) -> None:
        pass


def _cli_store(monkeypatch):
    store = _Closable(EnvMasterKey(new_key()))
    w = asyncio.run(_fill(store, "Leaving ISV"))

    async def fake_store():
        return store

    monkeypatch.setattr(cli, "_store", fake_store)
    return store, w


def test_the_command_deletes_after_the_id_is_typed_again(monkeypatch, capsys):
    store, w = _cli_store(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _prompt: w["root"].id)
    cli.main(["tenant", "delete", w["root"].id])
    out = capsys.readouterr().out
    assert w["email"] in out and "cannot be undone" in out and "deleted 2 tenant(s)" in out
    asyncio.run(_gone(store, w))
    entry = asyncio.run(store.operator_log())[0]
    assert (entry["operator"], entry["action"], entry["target"]) == ("cli", "delete_account", w["root"].id)


def test_the_command_deletes_nothing_on_a_mismatch(monkeypatch):
    store, w = _cli_store(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    with pytest.raises(SystemExit, match="not deleted"):
        cli.main(["tenant", "delete", w["root"].id])
    assert asyncio.run(store.get_tenant(w["root"].id)) is not None


def test_the_command_is_listed():
    assert "tenant delete <tenant_id>" in cli.USAGE
