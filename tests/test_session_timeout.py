"""A dashboard session ends after 30 minutes without use, and 12 hours
after sign-in whatever happens (OWASP ASVS 4.0 V3.3.2, level 2).

Both stores, and the route a session reaches: an idle session is the
401 any signed-out caller gets.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from okwan_api.main import app
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, accounts, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")


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
        for tenant_id in created:
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await s.close()


async def _session(store, ttl=accounts.SESSION_TTL):
    tenant = await store.create_tenant("Session ISV")
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(tenant.id, token_hash, accounts.expires(ttl))
    return tenant, full, token_hash


async def _last_used(store, token_hash: str, ago: timedelta) -> None:
    """Pretend the session was last used `ago` before now."""
    when = datetime.now(UTC) - ago
    if isinstance(store, PostgresStore):
        await store.pool.execute("UPDATE sessions SET last_seen_at = $2 WHERE token_hash = $1",
                                 token_hash, when)
    else:
        store._session_seen[token_hash] = when


async def _seen(store, token_hash: str) -> datetime:
    if isinstance(store, PostgresStore):
        return await store.pool.fetchval("SELECT last_seen_at FROM sessions WHERE token_hash = $1",
                                         token_hash)
    return store._session_seen[token_hash]


def test_the_limits_are_the_owasp_level_2_ones():
    assert accounts.SESSION_IDLE == timedelta(minutes=30)
    assert accounts.SESSION_TTL == timedelta(hours=12)


async def test_a_fresh_session_signs_in(store):
    tenant, _, token_hash = await _session(store)
    assert (await store.tenant_for_session(token_hash)).id == tenant.id


async def test_thirty_minutes_without_use_signs_out(store):
    _, _, token_hash = await _session(store)
    await _last_used(store, token_hash, accounts.SESSION_IDLE + timedelta(seconds=5))
    assert await store.tenant_for_session(token_hash) is None


async def test_using_it_keeps_it_alive(store):
    tenant, _, token_hash = await _session(store)
    await _last_used(store, token_hash, accounts.SESSION_IDLE - timedelta(minutes=1))
    assert (await store.tenant_for_session(token_hash)).id == tenant.id
    # The use moved the idle clock: it was last used just now.
    assert datetime.now(UTC) - await _seen(store, token_hash) < timedelta(seconds=30)


async def test_the_idle_clock_is_written_at_most_once_a_minute(store):
    _, _, token_hash = await _session(store)
    await _last_used(store, token_hash, timedelta(seconds=10))
    before = await _seen(store, token_hash)
    await store.tenant_for_session(token_hash)
    assert await _seen(store, token_hash) == before


async def test_twelve_hours_after_sign_in_it_ends_even_in_use(store):
    _, _, token_hash = await _session(store, ttl=timedelta(seconds=-1))
    assert await store.tenant_for_session(token_hash) is None


async def test_an_idle_session_cannot_reach_the_admin_routes(store):
    from okwan_api.auth import set_store

    set_store(store)
    _, full, token_hash = await _session(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://okwan.test") as client:
        headers = {"Authorization": f"Bearer {full}"}
        assert (await client.get("/v1/tenants", headers=headers)).status_code == 200
        await _last_used(store, token_hash, accounts.SESSION_IDLE + timedelta(seconds=5))
        assert (await client.get("/v1/tenants", headers=headers)).status_code == 401
