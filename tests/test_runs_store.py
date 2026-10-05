"""Stored reconciliation runs in the in-memory vault.

What would fail silently: a listing that carries rows, history that
grows without bound, a run readable under another tenant's id, or the
newest run per tenant picked by insertion order rather than time.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from okwan_vault import RUNS_KEPT, EnvMasterKey, MemoryStore, new_key


@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


async def add(store, tenant_id: str, *, name="rails", kind="across", surface="dashboard",
              status="ok", at: datetime | None = None, rows=None, summary=None, error=None):
    finished = at or datetime.now(UTC)
    failed = status == "failed"
    return await store.add_run(
        tenant_id, kind=kind, name=name, surface=surface, status=status,
        started_at=finished - timedelta(seconds=3), finished_at=finished,
        summary=None if failed else (summary or {"orders": 2, "collected_twice": 1}),
        rows=None if failed else (rows or [{"outcome": "collected_twice", "order": {"ref": "#1", "currency": "USD"}}]),
        error=error,
    )


async def test_add_then_get_carries_rows(store):
    t = await store.create_tenant("Kofi")
    rec = await add(store, t.id)
    assert rec.id.startswith("run_")
    got = await store.get_run(t.id, rec.id)
    assert got == rec
    assert got.rows == [{"outcome": "collected_twice", "order": {"ref": "#1", "currency": "USD"}}]
    assert got.started_at.tzinfo is not None and got.finished_at >= got.started_at


async def test_listing_is_newest_first_without_rows_and_filtered_by_name(store):
    t = await store.create_tenant("Kofi")
    now = datetime.now(UTC)
    old = await add(store, t.id, at=now - timedelta(days=2))
    pair = await add(store, t.id, name="shopify_stripe", kind="pair", at=now - timedelta(days=1))
    new = await add(store, t.id, at=now)
    listed = await store.list_runs(t.id)
    assert [r.id for r in listed] == [new.id, pair.id, old.id]
    assert all(r.rows is None for r in listed)
    assert all(r.summary is not None for r in listed)
    assert [r.id for r in await store.list_runs(t.id, name="rails")] == [new.id, old.id]
    assert [r.id for r in await store.list_runs(t.id, limit=1)] == [new.id]


async def test_a_run_is_only_readable_under_its_own_tenant(store):
    a = await store.create_tenant("A")
    b = await store.create_tenant("B")
    rec = await add(store, a.id)
    assert await store.get_run(b.id, rec.id) is None
    assert await store.list_runs(b.id) == []


async def test_unknown_tenant_is_refused(store):
    with pytest.raises(KeyError):
        await add(store, "ten_nope")


async def test_latest_is_the_newest_per_tenant_for_that_kind_and_name(store):
    isv = await store.create_tenant("ISV")
    m1 = await store.create_tenant("M1", parent_id=isv.id)
    m2 = await store.create_tenant("M2", parent_id=isv.id)
    m3 = await store.create_tenant("M3", parent_id=isv.id)
    now = datetime.now(UTC)
    await add(store, m1.id, at=now - timedelta(hours=2))
    newest = await add(store, m1.id, at=now - timedelta(hours=1))
    # Inserted after, but older: time wins, not insertion order.
    await add(store, m1.id, at=now - timedelta(hours=3))
    await add(store, m2.id, name="shopify_stripe", kind="pair", at=now)
    failed = await add(store, m3.id, status="failed", rows=None, summary=None, error="401 · rejected")
    latest = await store.latest_runs([m1.id, m2.id, m3.id, isv.id], "across", "rails")
    assert set(latest) == {m1.id, m3.id}
    assert latest[m1.id].id == newest.id
    assert latest[m3.id].id == failed.id and latest[m3.id].rows is None
    assert latest[m1.id].rows is not None


async def test_history_is_pruned_to_the_newest_fifty_per_tenant_kind_and_name(store):
    t = await store.create_tenant("Kofi")
    now = datetime.now(UTC)
    ids = [(await add(store, t.id, at=now + timedelta(seconds=i))).id for i in range(RUNS_KEPT + 3)]
    other = await add(store, t.id, name="shopify_stripe", kind="pair", at=now - timedelta(days=9))
    kept = await store.list_runs(t.id, name="rails", limit=100)
    assert len(kept) == RUNS_KEPT
    assert [r.id for r in kept] == list(reversed(ids[3:]))
    assert await store.get_run(t.id, ids[0]) is None
    # Another name's history is its own.
    assert await store.get_run(t.id, other.id) is not None
