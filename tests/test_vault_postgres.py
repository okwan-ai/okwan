"""Durable vault against Postgres.

Skipped when OKWAN_VAULT_DATABASE_URL is unset, so the suite stays
runnable without database access.
"""
from __future__ import annotations

import os
import uuid

import pytest
from okwan_vault import EnvMasterKey, PostgresStore, new_key

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(not DSN, reason="no vault database configured")


@pytest.fixture
async def store():
    s = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    created: list[str] = []

    class Tracked:
        def __init__(self, inner):
            self._inner = inner

        async def create_tenant(self, name):
            tenant = await self._inner.create_tenant(name)
            created.append(tenant.id)
            return tenant

        def __getattr__(self, item):
            return getattr(self._inner, item)

    try:
        yield Tracked(s)
    finally:
        for tenant_id in created:
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await s.close()


async def test_tenant_and_key_round_trip(store):
    tenant = await store.create_tenant(f"Acme {uuid.uuid4().hex[:6]}")
    full, record = await store.issue_key(tenant.id)

    resolved = await store.tenant_for_key(full)
    assert resolved.id == tenant.id
    assert await store.tenant_for_key("okw_wrong") is None
    assert record.prefix in full


async def test_revocation_is_immediate(store):
    tenant = await store.create_tenant("Acme")
    full, record = await store.issue_key(tenant.id)
    await store.revoke_key(record.id)

    assert await store.tenant_for_key(full) is None


async def test_revoking_twice_raises(store):
    tenant = await store.create_tenant("Acme")
    _, record = await store.issue_key(tenant.id)
    await store.revoke_key(record.id)

    with pytest.raises(KeyError):
        await store.revoke_key(record.id)


async def test_credentials_round_trip(store):
    tenant = await store.create_tenant("Acme")
    await store.put_credential(tenant.id, "shopify", "access_token", "shpat_x")
    await store.put_credential(tenant.id, "shopify", "shop_domain", "acme.myshopify.com")

    creds = await store.credentials_for(tenant.id, "shopify", ("access_token", "shop_domain"))
    assert creds == {"access_token": "shpat_x", "shop_domain": "acme.myshopify.com"}


async def test_rotation_replaces_in_place(store):
    """No stale ciphertext left behind for an old secret."""
    tenant = await store.create_tenant("Acme")
    await store.put_credential(tenant.id, "stripe", "secret_key", "sk_old")
    await store.put_credential(tenant.id, "stripe", "secret_key", "sk_new")

    creds = await store.credentials_for(tenant.id, "stripe", ("secret_key",))
    assert creds == {"secret_key": "sk_new"}

    count = await store.pool.fetchval(
        "SELECT count(*) FROM credentials WHERE tenant_id = $1 AND connector = 'stripe'",
        tenant.id,
    )
    assert count == 1


async def test_plaintext_is_never_written(store):
    tenant = await store.create_tenant("Acme")
    await store.put_credential(tenant.id, "stripe", "secret_key", "sk_live_supersecret")

    row = await store.pool.fetchrow(
        "SELECT ciphertext FROM credentials WHERE tenant_id = $1", tenant.id
    )
    assert b"sk_live_supersecret" not in bytes(row["ciphertext"])


async def test_tenants_are_isolated(store):
    a = await store.create_tenant("Acme")
    b = await store.create_tenant("Rival")
    await store.put_credential(a.id, "stripe", "secret_key", "sk_acme")
    await store.put_credential(b.id, "stripe", "secret_key", "sk_rival")

    assert (await store.credentials_for(a.id, "stripe", ("secret_key",)))["secret_key"] == "sk_acme"
    assert (await store.credentials_for(b.id, "stripe", ("secret_key",)))["secret_key"] == "sk_rival"


async def test_missing_credential_is_empty_not_error(store):
    tenant = await store.create_tenant("Acme")
    creds = await store.credentials_for(tenant.id, "stripe", ("secret_key",))
    assert creds == {"secret_key": ""}


async def test_deleting_a_tenant_removes_its_secrets(store):
    tenant = await store.create_tenant("Acme")
    await store.put_credential(tenant.id, "stripe", "secret_key", "sk_acme")
    await store.pool.execute("DELETE FROM tenants WHERE id = $1", tenant.id)

    count = await store.pool.fetchval(
        "SELECT count(*) FROM credentials WHERE tenant_id = $1", tenant.id
    )
    assert count == 0


# ── self-serve accounts ─────────────────────────────────────────────

@pytest.fixture
async def accounts_store():
    """Raw store plus cleanup by address, since verification creates the
    tenant inside the store rather than through a tracked call."""
    s = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    emails: list[str] = []
    try:
        yield s, emails
    finally:
        for email in emails:
            await s.pool.execute(
                "DELETE FROM tenants WHERE id IN "
                "(SELECT tenant_id FROM accounts WHERE email = $1)", email)
            await s.pool.execute("DELETE FROM signups WHERE email = $1", email)
        await s.close()


async def _pending(s, emails):
    from okwan_vault import accounts

    email = f"pg-{uuid.uuid4().hex[:8]}@okwan.test"
    emails.append(email)
    _, token_hash = accounts.new_token(accounts.VERIFY_PREFIX)
    await s.add_signup(email, accounts.hash_password("correct horse battery"),
                       token_hash, accounts.expires(accounts.SIGNUP_TTL))
    return email, token_hash


async def test_signup_completes_once_with_one_tenant(accounts_store):
    s, emails = accounts_store
    email, token_hash = await _pending(s, emails)

    assert (await s.signup_for(token_hash))[0] == email
    tenant = await s.complete_signup(token_hash)
    assert tenant is not None and tenant.is_root
    assert await s.complete_signup(token_hash) is None
    assert (await s.account_login(email))[0] == tenant.id


async def test_concurrent_verification_yields_one_tenant(accounts_store):
    """Two tokens for one address verified at once: the accounts key holds."""
    import asyncio

    from okwan_vault import accounts

    s, emails = accounts_store
    email, first = await _pending(s, emails)
    _, second = accounts.new_token(accounts.VERIFY_PREFIX)
    await s.add_signup(email, accounts.hash_password("x" * 12), second,
                       accounts.expires(accounts.SIGNUP_TTL))

    results = await asyncio.gather(s.complete_signup(first), s.complete_signup(second))
    assert sum(r is not None for r in results) == 1
    n = await s.pool.fetchval(
        "SELECT count(*) FROM tenants WHERE name = $1", email)
    assert n == 1


async def test_session_round_trip_and_expiry(accounts_store):
    from datetime import UTC, datetime, timedelta

    from okwan_vault import accounts

    s, emails = accounts_store
    _, token_hash = await _pending(s, emails)
    tenant = await s.complete_signup(token_hash)

    _, live = accounts.new_token(accounts.SESSION_PREFIX)
    _, stale = accounts.new_token(accounts.SESSION_PREFIX)
    await s.create_session(tenant.id, live, accounts.expires(accounts.SESSION_TTL))
    await s.create_session(tenant.id, stale, datetime.now(UTC) - timedelta(seconds=1))

    assert (await s.tenant_for_session(live)).id == tenant.id
    assert await s.tenant_for_session(stale) is None
    await s.delete_session(live)
    assert await s.tenant_for_session(live) is None


async def test_key_owner(store):
    tenant = await store.create_tenant("Acme")
    _, record = await store.issue_key(tenant.id)
    assert await store.key_owner(record.id) == tenant.id
    assert await store.key_owner("key_nope") is None
