"""Shared fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Limits and reused runs are process-wide; one test's attempts must
    not spend another's, nor its runs answer another's queries."""
    from okwan_api.ratelimit import limiter
    from okwan_api.recon_reuse import cache

    limiter.reset()
    cache.clear()
    yield
    limiter.reset()
    cache.clear()


@pytest.fixture
def as_of(monkeypatch):
    """Fix the day a check counts its lookback from: `as_of(2026, 9, 20)`.

    A check reads a fixed number of days back from now. A test whose
    records are dated to a fixed week passes only while that week is
    inside the window, and then starts failing on a date, with nothing
    changed. Such a test says the day its records are read on.
    """
    from datetime import UTC, datetime

    from okwan_recon import runner

    real = runner.resolve_window

    def pin(year: int, month: int, day: int) -> datetime:
        at = datetime(year, month, day, 12, tzinfo=UTC)
        monkeypatch.setattr(runner, "resolve_window", lambda spec, now=None: real(spec, now or at))
        return at

    return pin


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
        full, _ = await store.issue_key(tenant.id, scope="admin")
        return full

    return {"Authorization": f"Bearer {asyncio.run(issue())}"}
