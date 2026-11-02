"""`python -m okwan_vault plan set|show`: the operator path to a plan.

Nothing else sets a plan, and on purpose: there is no API route, so no
key or session can raise its own tenant's limit. These hold the rules
the command keeps, against both store backends: a plan lands only on a
root tenant (billing rolls up to it, and a child's plan is never read),
the name comes from PLANS, and `show` reads the same arithmetic as the
402 gate.

The Postgres runs skip without OKWAN_VAULT_DATABASE_URL, like
tests/test_vault_postgres.py.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from okwan_vault import (
    PLANS,
    EnvMasterKey,
    MemoryStore,
    PlanRefused,
    PostgresStore,
    cli,
    new_key,
    quota,
)

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
        for tenant_id in reversed(created):
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await s.close()


@pytest.fixture
async def isv(store):
    return await store.create_tenant("Acme ISV")


@pytest.fixture
async def merchant(store, isv):
    return await store.create_tenant("Merchant", parent_id=isv.id)


# ── set ─────────────────────────────────────────────────────────────

async def test_set_lands_on_the_root_and_reads_back(store, isv):
    lines = await cli.set_plan(store, isv.id, "pro")
    assert await store.get_plan(isv.id) == ("pro", PLANS["pro"])
    assert "plan       pro" in lines
    assert "limit      100,000 requests a month" in lines


async def test_a_root_plan_lifts_the_gate_for_its_merchants(store, isv, merchant):
    """The point of the command: the merchant's gate reads the root's plan."""
    assert (await quota(store, merchant.id)).limit == PLANS["free"]
    await cli.set_plan(store, isv.id, "team")
    q = await quota(store, merchant.id)
    assert (q.plan, q.limit) == ("team", PLANS["team"])


async def test_set_can_move_a_plan_down_as_well_as_up(store, isv):
    await cli.set_plan(store, isv.id, "enterprise")
    await cli.set_plan(store, isv.id, "free")
    assert await store.get_plan(isv.id) == ("free", PLANS["free"])


async def test_a_child_tenant_is_refused_and_named_to_its_root(store, isv, merchant):
    with pytest.raises(PlanRefused, match=f"{merchant.id} is not a root tenant") as e:
        await cli.set_plan(store, merchant.id, "pro")
    assert f"billed to {isv.id}" in str(e.value)
    assert await store.get_plan(merchant.id) == ("free", PLANS["free"])


async def test_the_store_refuses_a_child_without_the_cli(store, merchant):
    """Structural, not a CLI courtesy: any future caller gets the same no."""
    with pytest.raises(PlanRefused, match="is not a root tenant"):
        await store.set_plan(merchant.id, "pro")


async def test_an_unknown_tenant_is_refused(store):
    with pytest.raises(PlanRefused, match="no such tenant: ten_nope"):
        await cli.set_plan(store, "ten_nope", "pro")
    with pytest.raises(PlanRefused, match="no such tenant: ten_nope"):
        await store.set_plan("ten_nope", "pro")


async def test_an_unknown_plan_is_refused_with_the_known_ones(store, isv):
    with pytest.raises(PlanRefused, match="unknown plan 'platinum'") as e:
        await cli.set_plan(store, isv.id, "platinum")
    assert all(name in str(e.value) for name in PLANS)
    assert await store.get_plan(isv.id) == ("free", PLANS["free"])


# ── show ────────────────────────────────────────────────────────────

async def test_show_defaults_to_free_with_nothing_used(store, isv):
    lines = await cli.show_plan(store, isv.id)
    assert lines[:3] == [
        f"tenant     {isv.id}",
        "plan       free",
        "limit      5,000 requests a month",
    ]
    assert "remaining  5,000" in lines


async def test_show_counts_the_whole_subtree_this_month(store, isv, merchant):
    for _ in range(3):
        await store.record_request(merchant.id, "rest")
    await store.record_request(isv.id, "mcp:query")
    lines = await cli.show_plan(store, isv.id)
    month = datetime.now(UTC).strftime("%Y-%m-01")
    assert f"used       4 since {month} UTC, merchants included" in lines
    assert "remaining  4,996" in lines


async def test_show_is_the_gate_arithmetic(store, isv, merchant):
    await cli.set_plan(store, isv.id, "pro")
    await store.record_request(merchant.id, "rest")
    q = await quota(store, isv.id)
    lines = await cli.show_plan(store, isv.id)
    assert f"plan       {q.plan}" in lines
    assert f"limit      {q.limit:,} requests a month" in lines
    assert f"remaining  {q.remaining:,}" in lines


async def test_show_says_when_the_gate_is_closed(store, isv, monkeypatch):
    monkeypatch.setitem(PLANS, "free", 2)  # a 2-request free plan, for this test
    await store.record_request(isv.id, "rest")
    await store.record_request(isv.id, "rest")
    lines = await cli.show_plan(store, isv.id)
    assert "remaining  0" in lines
    assert "gate       limit reached: metered calls answer 402" in lines


async def test_show_an_unmetered_plan(store, isv):
    lines = await cli.show_plan(store, isv.id)
    assert not any("gate" in line for line in lines)
    lines = await cli.set_plan(store, isv.id, "enterprise")
    assert "limit      unmetered" in lines
    assert not any(line.startswith("remaining") for line in lines)


async def test_show_refuses_a_child_and_names_its_root(store, isv, merchant):
    with pytest.raises(PlanRefused, match=f"billed to {isv.id}"):
        await cli.show_plan(store, merchant.id)


async def test_show_refuses_an_unknown_tenant(store):
    with pytest.raises(PlanRefused, match="no such tenant: ten_nope"):
        await cli.show_plan(store, "ten_nope")


# ── the gate itself, and the command line ───────────────────────────

async def test_setting_a_plan_reopens_a_closed_gate(monkeypatch):
    """The 402 the API returns is lifted by the operator command."""
    from fastapi import HTTPException
    from okwan_api.auth import check_quota, quota_for, set_store

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    isv = await store.create_tenant("Acme ISV")
    merchant = await store.create_tenant("Merchant", parent_id=isv.id)
    monkeypatch.setitem(PLANS, "free", 1)
    await store.record_request(merchant.id, "rest")

    assert await quota_for(merchant) == await quota(store, merchant.id)
    with pytest.raises(HTTPException) as e:
        await check_quota(merchant)
    assert e.value.status_code == 402

    await cli.set_plan(store, isv.id, "pro")
    assert await check_quota(merchant) is merchant


class _Closable(MemoryStore):
    async def close(self) -> None:
        pass


def _run(monkeypatch, store, argv: list[str]) -> None:
    async def fake_store():
        return store

    monkeypatch.setattr(cli, "_store", fake_store)
    cli.main(argv)


async def _tree():
    store = _Closable(EnvMasterKey(new_key()))
    isv = await store.create_tenant("Acme ISV")
    merchant = await store.create_tenant("Merchant", parent_id=isv.id)
    return store, isv, merchant


def test_cli_plan_set_and_show_print_the_plan(monkeypatch, capsys):
    import asyncio

    store, isv, _ = asyncio.run(_tree())
    _run(monkeypatch, store, ["plan", "set", isv.id, "pro"])
    assert "plan       pro" in capsys.readouterr().out
    _run(monkeypatch, store, ["plan", "show", isv.id])
    out = capsys.readouterr().out
    assert f"tenant     {isv.id}" in out
    assert "limit      100,000 requests a month" in out


def test_cli_refusal_exits_with_the_reason(monkeypatch):
    import asyncio

    store, isv, merchant = asyncio.run(_tree())
    with pytest.raises(SystemExit, match=f"billed to {isv.id}"):
        _run(monkeypatch, store, ["plan", "set", merchant.id, "pro"])
    with pytest.raises(SystemExit, match="unknown plan"):
        _run(monkeypatch, store, ["plan", "set", isv.id, "platinum"])


def test_cli_plan_commands_are_listed_and_checked_for_arity():
    assert "plan set <tenant_id> <plan>" in cli.USAGE
    assert "plan show <tenant_id>" in cli.USAGE
    with pytest.raises(SystemExit):
        cli.main(["plan", "set", "ten_only"])
    with pytest.raises(SystemExit):
        cli.main(["plan", "show"])
