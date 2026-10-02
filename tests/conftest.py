"""Shared fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Limits are process-wide; one test's attempts must not spend another's."""
    from okwan_api.ratelimit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def api_key():
    """Headers carrying a real key for a fresh tenant in an in-memory vault.

    Routes that read data take a key and nothing else, so tests reach them
    the way a customer does rather than around the check.
    """
    import asyncio

    from okwan_api.auth import set_store
    from okwan_vault import EnvMasterKey, MemoryStore, new_key

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)

    async def issue():
        tenant = await store.create_tenant("Test tenant")
        full, _ = await store.issue_key(tenant.id)
        return full

    return {"Authorization": f"Bearer {asyncio.run(issue())}"}
