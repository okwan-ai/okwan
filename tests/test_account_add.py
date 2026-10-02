"""`python -m okwan_vault account add`: a login for an existing root tenant.

Signup is the only other path to an account, and it waits on the mailer.
These hold the operator path to the rules signup keeps: a root tenant, one
account per tenant, one per address, the same password bounds, and an
account that sign-in accepts.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from okwan_vault import EnvMasterKey, MemoryStore, accounts, cli, new_key
from okwan_vault.accounts import AccountRefused

PASSWORD = "correct horse battery"


@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
async def root(store):
    return await store.create_tenant("Okwan")


# ── the account ─────────────────────────────────────────────────────

async def test_an_added_account_can_sign_in(store, root):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    email = await cli.add_account(store, root.id, "  Felix@Okwan.TEST ", PASSWORD)
    assert email == "felix@okwan.test"

    set_store(store)
    client = TestClient(app)
    r = client.post("/v1/sessions", json={"email": "felix@okwan.test", "password": PASSWORD})
    assert r.status_code == 201
    assert r.json()["tenant_id"] == root.id
    assert (await store.tenant_for_session(accounts.hash_token(r.json()["session"]))).id == root.id


async def test_the_password_is_stored_hashed(store, root):
    await cli.add_account(store, root.id, "a@okwan.test", PASSWORD)
    tenant_id, stored = await store.account_login("a@okwan.test")
    assert tenant_id == root.id
    assert PASSWORD not in stored
    assert accounts.check_password(PASSWORD, stored)


# ── refusals ────────────────────────────────────────────────────────

async def test_an_unknown_tenant_is_refused(store):
    with pytest.raises(AccountRefused, match="no such tenant: ten_nope"):
        await cli.add_account(store, "ten_nope", "a@okwan.test", PASSWORD)


async def test_a_child_tenant_is_refused(store, root):
    child = await store.create_tenant("Merchant", parent_id=root.id)
    with pytest.raises(AccountRefused, match="is not a root tenant"):
        await cli.add_account(store, child.id, "a@okwan.test", PASSWORD)


async def test_a_tenant_with_an_account_is_refused(store, root):
    await cli.add_account(store, root.id, "a@okwan.test", PASSWORD)
    with pytest.raises(AccountRefused, match="already has an account"):
        await cli.add_account(store, root.id, "b@okwan.test", PASSWORD)


async def test_a_taken_email_is_refused_in_any_spelling(store, root):
    other = await store.create_tenant("Other")
    await cli.add_account(store, root.id, "a@okwan.test", PASSWORD)
    with pytest.raises(AccountRefused, match="a@okwan.test is already taken"):
        await cli.add_account(store, other.id, "A@OKWAN.test", PASSWORD)


@pytest.mark.parametrize("password", ["x" * (accounts.MIN_PASSWORD - 1),
                                      "x" * (accounts.MAX_PASSWORD + 1)])
async def test_password_bounds_match_signup(store, root, password):
    with pytest.raises(AccountRefused, match="password must be 12 to 256"):
        await cli.add_account(store, root.id, "a@okwan.test", password)
    assert not await store.account_exists("a@okwan.test")


async def test_not_an_email_is_refused(store, root):
    with pytest.raises(AccountRefused, match="not an email address"):
        await cli.add_account(store, root.id, "felix", PASSWORD)


# ── the password source ─────────────────────────────────────────────

def test_password_from_the_environment(monkeypatch):
    monkeypatch.setenv("OKWAN_ACCOUNT_PASSWORD", PASSWORD)
    monkeypatch.setattr("sys.stdin", io.StringIO("not this one\n"))
    assert cli._read_password() == PASSWORD


def test_password_from_piped_stdin_keeps_its_spaces(monkeypatch):
    monkeypatch.delenv("OKWAN_ACCOUNT_PASSWORD", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(f" {PASSWORD} \n"))
    assert cli._read_password() == f" {PASSWORD} "


def test_a_terminal_on_stdin_is_refused_not_echoed(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("OKWAN_ACCOUNT_PASSWORD", raising=False)
    monkeypatch.setattr("sys.stdin", Tty(PASSWORD))
    with pytest.raises(SystemExit, match="OKWAN_ACCOUNT_PASSWORD"):
        cli._read_password()


def test_account_add_takes_exactly_a_tenant_and_an_email():
    with pytest.raises(SystemExit):
        cli.main(["account", "add", "ten_abc"])
