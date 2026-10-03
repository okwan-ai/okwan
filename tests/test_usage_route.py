"""The usage meter, read from the dashboard: GET /v1/tenants/{id}/usage.

What would fail silently: a merchant seeing a sibling's buckets, a foreign
tenant answering anything but 404, the read itself being metered, a window
that leaks older buckets, or a session being refused where a key works.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from okwan_vault import EnvMasterKey, MemoryStore, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token
from okwan_vault.usage import hour_bucket


@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
def client(store):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    set_store(store)
    return TestClient(app)


async def _session(store, tenant_id: str) -> dict[str, str]:
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(tenant_id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    return {"Authorization": f"Bearer {full}"}


@pytest.fixture
async def tree(store):
    """An ISV with two merchants, each with some traffic, and a stranger."""
    isv = await store.create_tenant("ISV")
    m1 = await store.create_tenant("Merchant 1", parent_id=isv.id)
    m2 = await store.create_tenant("Merchant 2", parent_id=isv.id)
    other = await store.create_tenant("Other ISV")
    for _ in range(3):
        await store.record_request(m1.id, "dashboard:across")
    await store.record_request(m1.id, "mcp:reconcile")
    await store.record_request(m2.id, "rest:across")
    await store.record_request(isv.id, "test:stripe")
    await store.record_request(other.id, "sql")
    key, _ = await store.issue_key(isv.id)
    return {"isv": isv, "m1": m1, "m2": m2, "other": other,
            "isv_auth": await _session(store, isv.id),
            "m1_auth": await _session(store, m1.id),
            "other_auth": await _session(store, other.id),
            "isv_key": {"Authorization": f"Bearer {key}"}}


def _by_tenant(body: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for b in body["buckets"]:
        out[b["tenant_id"]] = out.get(b["tenant_id"], 0) + b["requests"]
    return out


# ── what the ISV sees ───────────────────────────────────────────────

async def test_isv_sees_its_plan_and_every_merchants_buckets(client, tree):
    r = client.get(f"/v1/tenants/{tree['isv'].id}/usage", headers=tree["isv_auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"] == {
        "name": "free", "limit": 5000, "used": 6, "remaining": 4994,
        "unmetered": False, "month_start": body["plan"]["month_start"],
    }
    assert _by_tenant(body) == {tree["m1"].id: 4, tree["m2"].id: 1, tree["isv"].id: 1}
    surfaces = {(b["tenant_id"], b["surface"]): b["requests"] for b in body["buckets"]}
    assert surfaces[(tree["m1"].id, "dashboard:across")] == 3
    assert surfaces[(tree["m1"].id, "mcp:reconcile")] == 1
    # The stranger's traffic is nowhere in it.
    assert tree["other"].id not in _by_tenant(body)


async def test_a_key_is_accepted_like_a_session(client, tree):
    r = client.get(f"/v1/tenants/{tree['isv'].id}/usage", headers=tree["isv_key"])
    assert r.status_code == 200
    assert r.json()["plan"]["used"] == 6


# ── what a merchant sees ────────────────────────────────────────────

async def test_a_merchant_sees_the_shared_allowance_but_only_its_own_buckets(client, tree):
    r = client.get(f"/v1/tenants/{tree['m1'].id}/usage", headers=tree["m1_auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    # The allowance is the ISV's, which the 402 it would get already names.
    assert body["plan"]["used"] == 6
    # The buckets are its own: no sibling, no parent.
    assert _by_tenant(body) == {tree["m1"].id: 4}


async def test_the_isv_can_read_one_merchant(client, tree):
    r = client.get(f"/v1/tenants/{tree['m2'].id}/usage", headers=tree["isv_auth"])
    assert r.status_code == 200
    assert _by_tenant(r.json()) == {tree["m2"].id: 1}


# ── the boundary ────────────────────────────────────────────────────

async def test_a_foreign_tenant_is_404(client, tree):
    r = client.get(f"/v1/tenants/{tree['m1'].id}/usage", headers=tree["other_auth"])
    assert r.status_code == 404
    assert r.json()["detail"] == f"no such tenant: {tree['m1'].id}"


async def test_a_merchant_cannot_read_its_parent(client, tree):
    r = client.get(f"/v1/tenants/{tree['isv'].id}/usage", headers=tree["m1_auth"])
    assert r.status_code == 404


async def test_signed_out_is_401(client, tree):
    assert client.get(f"/v1/tenants/{tree['isv'].id}/usage").status_code == 401


# ── the meter itself ────────────────────────────────────────────────

async def test_reading_the_meter_does_not_move_it(client, store, tree):
    before = dict(store._usage)
    client.get(f"/v1/tenants/{tree['isv'].id}/usage", headers=tree["isv_auth"])
    client.get(f"/v1/tenants/{tree['m1'].id}/usage", headers=tree["isv_auth"])
    assert dict(store._usage) == before


async def test_the_window_excludes_older_buckets_but_the_month_total_keeps_them(client, store, tree):
    old = hour_bucket() - timedelta(days=10)
    store._usage[(tree["m1"].id, old, "rest:across")] = 7
    r = client.get(f"/v1/tenants/{tree['isv'].id}/usage", params={"days": 7},
                   headers=tree["isv_auth"])
    body = r.json()
    assert body["window"]["days"] == 7
    assert all(b["requests"] != 7 for b in body["buckets"])
    # Month-to-date is the billing figure; it is not windowed. Whether the
    # old bucket falls in this calendar month depends on today's date.
    expected = 6 + (7 if old >= datetime.now(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0) else 0)
    assert body["plan"]["used"] == expected


async def test_window_bounds(client, tree):
    for days in (0, 93):
        r = client.get(f"/v1/tenants/{tree['isv'].id}/usage", params={"days": days},
                       headers=tree["isv_auth"])
        assert r.status_code == 422, days
