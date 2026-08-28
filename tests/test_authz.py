"""Tenant boundary.

The single most security-sensitive function in the codebase: it decides
whether one customer can read another customer's credentials. These tests
exist so a later simplification of may_administer fails loudly rather
than quietly widening access.
"""
from __future__ import annotations

import pytest

from okwan_vault import EnvMasterKey, MemoryStore, new_key
from okwan_vault.authz import (
    MAX_DEPTH,
    Forbidden,
    ancestors,
    may_administer,
    require_administer,
)


@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
async def tree(store):
    """Two ISVs, each with two merchants. The shape the product has."""
    acme = await store.create_tenant("Acme ISV")
    rival = await store.create_tenant("Rival ISV")
    return {
        "store": store,
        "acme": acme,
        "rival": rival,
        "a1": await store.create_tenant("Acme Merchant 1", parent_id=acme.id),
        "a2": await store.create_tenant("Acme Merchant 2", parent_id=acme.id),
        "r1": await store.create_tenant("Rival Merchant 1", parent_id=rival.id),
    }


# ── what is allowed ─────────────────────────────────────────────────

async def test_a_tenant_may_administer_itself(tree):
    assert await may_administer(tree["store"], tree["acme"].id, tree["acme"].id)
    assert await may_administer(tree["store"], tree["a1"].id, tree["a1"].id)


async def test_a_parent_may_administer_its_child(tree):
    assert await may_administer(tree["store"], tree["acme"].id, tree["a1"].id)
    assert await may_administer(tree["store"], tree["acme"].id, tree["a2"].id)


async def test_a_grandparent_may_administer_a_grandchild(store):
    """Depth is not limited to one level."""
    root = await store.create_tenant("Platform")
    mid = await store.create_tenant("Reseller", parent_id=root.id)
    leaf = await store.create_tenant("Merchant", parent_id=mid.id)

    assert await may_administer(store, root.id, leaf.id)
    assert await may_administer(store, mid.id, leaf.id)


# ── what is refused ─────────────────────────────────────────────────

async def test_a_child_may_not_administer_its_parent(tree):
    """Provisioned tenants cannot reach the account that created them."""
    assert not await may_administer(tree["store"], tree["a1"].id, tree["acme"].id)


async def test_siblings_are_isolated(tree):
    """Two merchants of the same ISV cannot see each other."""
    assert not await may_administer(tree["store"], tree["a1"].id, tree["a2"].id)
    assert not await may_administer(tree["store"], tree["a2"].id, tree["a1"].id)


async def test_isvs_are_isolated(tree):
    assert not await may_administer(tree["store"], tree["acme"].id, tree["rival"].id)
    assert not await may_administer(tree["store"], tree["rival"].id, tree["acme"].id)


async def test_an_isv_may_not_reach_a_rivals_merchant(tree):
    """The case that matters commercially."""
    assert not await may_administer(tree["store"], tree["acme"].id, tree["r1"].id)
    assert not await may_administer(tree["store"], tree["rival"].id, tree["a1"].id)


async def test_merchants_of_different_isvs_are_isolated(tree):
    assert not await may_administer(tree["store"], tree["a1"].id, tree["r1"].id)


async def test_unknown_actor_is_refused(tree):
    assert not await may_administer(tree["store"], "ten_nobody", tree["a1"].id)


async def test_unknown_target_is_refused(tree):
    assert not await may_administer(tree["store"], tree["acme"].id, "ten_nobody")


# ── ancestor chain ──────────────────────────────────────────────────

async def test_ancestors_excludes_self(tree):
    chain = await ancestors(tree["store"], tree["a1"].id)
    assert tree["a1"].id not in chain
    assert chain == [tree["acme"].id]


async def test_root_has_no_ancestors(tree):
    assert await ancestors(tree["store"], tree["acme"].id) == []


async def test_ancestors_are_ordered_upward(store):
    root = await store.create_tenant("Platform")
    mid = await store.create_tenant("Reseller", parent_id=root.id)
    leaf = await store.create_tenant("Merchant", parent_id=mid.id)

    assert await ancestors(store, leaf.id) == [mid.id, root.id]


async def test_walk_is_bounded(store):
    """An unbounded walk over adversarial data is a hang, not an error."""
    current = await store.create_tenant("root")
    for i in range(MAX_DEPTH + 5):
        current = await store.create_tenant(f"level-{i}", parent_id=current.id)

    chain = await ancestors(store, current.id)
    assert len(chain) <= MAX_DEPTH


# ── the raising wrapper ─────────────────────────────────────────────

async def test_require_passes_silently_when_allowed(tree):
    await require_administer(tree["store"], tree["acme"].id, tree["a1"].id)


async def test_require_raises_when_refused(tree):
    with pytest.raises(Forbidden):
        await require_administer(tree["store"], tree["a1"].id, tree["a2"].id)


async def test_forbidden_message_does_not_leak_the_target_name(tree):
    """Ids only — the message reaches a caller who may not know the tenant."""
    try:
        await require_administer(tree["store"], tree["rival"].id, tree["a1"].id)
    except Forbidden as exc:
        assert "Acme Merchant 1" not in str(exc)


# ── the model ───────────────────────────────────────────────────────

async def test_parent_must_exist(store):
    with pytest.raises(KeyError):
        await store.create_tenant("Orphan", parent_id="ten_nobody")


async def test_root_tenants_report_as_root(tree):
    assert tree["acme"].is_root
    assert not tree["a1"].is_root
