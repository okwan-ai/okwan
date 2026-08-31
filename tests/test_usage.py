"""Metering and plan enforcement."""
from __future__ import annotations

from datetime import UTC

import pytest
from okwan_vault import EnvMasterKey, MemoryStore, new_key
from okwan_vault.usage import (
    DEFAULT_PLAN,
    PLANS,
    Quota,
    billing_root,
    hour_bucket,
    month_start,
)


@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
async def tree(store):
    isv = await store.create_tenant("Acme ISV")
    return {
        "store": store,
        "isv": isv,
        "m1": await store.create_tenant("Merchant 1", parent_id=isv.id),
        "m2": await store.create_tenant("Merchant 2", parent_id=isv.id),
        "solo": await store.create_tenant("Solo Dev"),
    }


# ── attribution ─────────────────────────────────────────────────────

async def test_usage_rolls_up_to_the_paying_account(tree):
    """An ISV's merchants are not customers; the ISV is."""
    store = tree["store"]
    for _ in range(3):
        await store.record_request(tree["m1"].id, "rest:shopify")
    await store.record_request(tree["m2"].id, "rest:stripe")

    assert await store.usage_since(tree["m1"].id, month_start()) == 3
    assert await store.usage_since(tree["isv"].id, month_start()) == 4


async def test_a_merchant_does_not_see_its_siblings_usage(tree):
    store = tree["store"]
    await store.record_request(tree["m2"].id, "rest:stripe")
    assert await store.usage_since(tree["m1"].id, month_start()) == 0


async def test_billing_root_of_a_merchant_is_the_isv(tree):
    assert await billing_root(tree["store"], tree["m1"].id) == tree["isv"].id


async def test_billing_root_of_a_root_is_itself(tree):
    assert await billing_root(tree["store"], tree["solo"].id) == tree["solo"].id


async def test_one_isv_usage_does_not_reach_another(tree, store):
    rival = await store.create_tenant("Rival ISV")
    await store.record_request(tree["m1"].id, "rest:shopify")
    assert await store.usage_since(rival.id, month_start()) == 0


# ── plans ───────────────────────────────────────────────────────────

async def test_unset_plan_defaults_to_free(tree):
    name, limit = await tree["store"].get_plan(tree["isv"].id)
    assert name == DEFAULT_PLAN
    assert limit == PLANS["free"]


async def test_plan_can_be_changed(tree):
    await tree["store"].set_plan(tree["isv"].id, "pro")
    assert await tree["store"].get_plan(tree["isv"].id) == ("pro", PLANS["pro"])


async def test_unknown_plan_is_rejected(tree):
    with pytest.raises(ValueError, match="unknown plan"):
        await tree["store"].set_plan(tree["isv"].id, "platinum")


# ── quota arithmetic ────────────────────────────────────────────────

def test_quota_reports_remaining():
    q = Quota(plan="free", limit=5_000, used=1_200)
    assert q.remaining == 3_800
    assert not q.exceeded


def test_quota_is_exceeded_at_the_limit_not_past_it():
    assert Quota(plan="free", limit=100, used=100).exceeded
    assert not Quota(plan="free", limit=100, used=99).exceeded


def test_remaining_never_goes_negative():
    assert Quota(plan="free", limit=100, used=150).remaining == 0


def test_zero_limit_means_unmetered():
    """Enterprise is not a limit of zero requests."""
    q = Quota(plan="enterprise", limit=0, used=10_000_000)
    assert q.unmetered
    assert not q.exceeded
    assert q.remaining is None


# ── bucketing ───────────────────────────────────────────────────────

def test_hour_bucket_truncates():
    from datetime import datetime

    b = hour_bucket(datetime(2026, 8, 28, 14, 37, 22, tzinfo=UTC))
    assert (b.minute, b.second, b.microsecond) == (0, 0, 0)
    assert b.hour == 14


async def test_requests_in_one_hour_share_a_row(tree):
    """Per-request rows are write amplification; nothing bills by the second."""
    store = tree["store"]
    for _ in range(50):
        await store.record_request(tree["m1"].id, "rest:shopify")

    assert len(store._usage) == 1
    assert await store.usage_since(tree["m1"].id, month_start()) == 50


async def test_surfaces_are_counted_separately(tree):
    store = tree["store"]
    await store.record_request(tree["m1"].id, "rest:shopify")
    await store.record_request(tree["m1"].id, "rest:query")

    assert len(store._usage) == 2
    assert await store.usage_since(tree["m1"].id, month_start()) == 2
