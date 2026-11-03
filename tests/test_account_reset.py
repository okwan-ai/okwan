"""`python -m okwan_vault account reset-password`: the operator's way back in.

There is no reset by email yet, and no API route for this on purpose:
only someone holding the vault database can run it. These hold what it
must do, against both store backends: the new password signs in and the
old one stops; every session the login held ends, and only that login's;
an unknown address and a password outside signup's bounds are refused,
leaving the login as it was.

The Postgres runs skip without OKWAN_VAULT_DATABASE_URL, like
tests/test_vault_postgres.py.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, accounts, cli, new_key
from okwan_vault.accounts import AccountRefused

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
OLD = "correct horse battery"
NEW = "a different staple, longer"


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


def _email(store, local: str) -> str:
    """Addresses are unique across the vault, so Postgres runs need their own."""
    return f"{local}-{id(store)}@okwan.test"


@pytest.fixture
async def login(store):
    tenant = await store.create_tenant("Acme ISV")
    email = await cli.add_account(store, tenant.id, _email(store, "founder"), OLD)
    return tenant, email


async def _session(store, tenant_id: str) -> str:
    _, token_hash = accounts.new_token(accounts.SESSION_PREFIX)
    await store.create_session(tenant_id, token_hash, accounts.expires(accounts.SESSION_TTL))
    return token_hash


# ── the reset ───────────────────────────────────────────────────────

async def test_the_new_password_signs_in_and_the_old_one_stops(store, login):
    tenant, email = login
    assert await cli.reset_password(store, email, NEW) == (email, tenant.id)
    tenant_id, stored = await store.account_login(email)
    assert tenant_id == tenant.id
    assert accounts.check_password(NEW, stored)
    assert not accounts.check_password(OLD, stored)
    assert NEW not in stored


async def test_every_session_of_that_login_ends_and_no_other(store, login):
    tenant, email = login
    other = await store.create_tenant("Rival ISV")
    await cli.add_account(store, other.id, _email(store, "rival"), OLD)
    mine = [await _session(store, tenant.id), await _session(store, tenant.id)]
    theirs = await _session(store, other.id)

    await cli.reset_password(store, email, NEW)

    for token_hash in mine:
        assert await store.tenant_for_session(token_hash) is None
    assert (await store.tenant_for_session(theirs)).id == other.id


async def test_the_address_is_read_in_any_spelling(store, login):
    tenant, email = login
    spelled = "  " + email.upper() + " "
    assert await cli.reset_password(store, spelled, NEW) == (email, tenant.id)


# ── refusals ────────────────────────────────────────────────────────

async def test_an_unknown_address_is_refused(store):
    with pytest.raises(AccountRefused, match="no account for nobody@okwan.test"):
        await cli.reset_password(store, "nobody@okwan.test", NEW)
    with pytest.raises(AccountRefused, match="no account for nobody@okwan.test"):
        await store.reset_password("nobody@okwan.test", accounts.hash_password(NEW))


@pytest.mark.parametrize("password", ["too short", "x" * (accounts.MAX_PASSWORD + 1)])
async def test_a_password_outside_signups_bounds_is_refused(store, login, password):
    _, email = login
    with pytest.raises(AccountRefused, match="password must be 12 to 256 characters"):
        await cli.reset_password(store, email, password)
    _, stored = await store.account_login(email)
    assert accounts.check_password(OLD, stored)


async def test_a_refused_reset_leaves_sessions_alone(store, login):
    tenant, email = login
    token_hash = await _session(store, tenant.id)
    with pytest.raises(AccountRefused):
        await cli.reset_password(store, email, "too short")
    assert (await store.tenant_for_session(token_hash)).id == tenant.id


# ── through the API and the command line ────────────────────────────

async def test_sign_in_answers_to_the_new_password_only():
    from fastapi.testclient import TestClient
    from okwan_api.auth import set_store
    from okwan_api.main import app

    store = MemoryStore(EnvMasterKey(new_key()))
    tenant = await store.create_tenant("Acme ISV")
    await cli.add_account(store, tenant.id, "founder@okwan.test", OLD)
    await cli.reset_password(store, "founder@okwan.test", NEW)

    set_store(store)
    client = TestClient(app)
    r = client.post("/v1/sessions", json={"email": "founder@okwan.test", "password": NEW})
    assert r.status_code == 201
    assert r.json()["tenant_id"] == tenant.id
    r = client.post("/v1/sessions", json={"email": "founder@okwan.test", "password": OLD})
    assert r.status_code == 401


class _Closable(MemoryStore):
    async def close(self) -> None:
        pass


def _cli_store(monkeypatch):
    store = _Closable(EnvMasterKey(new_key()))

    async def seed():
        tenant = await store.create_tenant("Acme ISV")
        await cli.add_account(store, tenant.id, "founder@okwan.test", OLD)
        return tenant

    tenant = asyncio.run(seed())

    async def fake_store():
        return store

    monkeypatch.setattr(cli, "_store", fake_store)
    return store, tenant


def test_the_command_reads_the_password_from_the_environment(monkeypatch, capsys):
    store, tenant = _cli_store(monkeypatch)
    monkeypatch.setenv("OKWAN_ACCOUNT_PASSWORD", NEW)
    cli.main(["account", "reset-password", "founder@okwan.test"])
    out = capsys.readouterr().out
    assert f"tenant  {tenant.id}" in out
    assert "every session for this account has ended" in out
    assert NEW not in out
    _, stored = asyncio.run(store.account_login("founder@okwan.test"))
    assert accounts.check_password(NEW, stored)


def test_the_command_exits_with_the_reason(monkeypatch):
    _cli_store(monkeypatch)
    monkeypatch.setenv("OKWAN_ACCOUNT_PASSWORD", NEW)
    with pytest.raises(SystemExit, match="no account for ghost@okwan.test"):
        cli.main(["account", "reset-password", "ghost@okwan.test"])


def test_the_command_is_listed_and_checked_for_arity():
    assert "account reset-password <email>" in cli.USAGE
    with pytest.raises(SystemExit):
        cli.main(["account", "reset-password"])
