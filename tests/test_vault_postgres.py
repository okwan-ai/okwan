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
