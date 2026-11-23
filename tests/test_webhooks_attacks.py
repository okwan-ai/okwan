"""The attacker's pass on webhooks (2026-11-23), each finding kept as a test
that fails on the commit it was found on (05f0097) and passes since.

F1  An IPv6 spelling of an internal IPv4 address (NAT64's prefix, the
    IPv4-compatible and IPv4-translated forms) passed the egress check, so
    an address at `[64:ff9b::a9fe:a9fe]` (the metadata service) was added,
    pinged and dialed. The check judges such a form as the IPv4 it carries,
    or refuses it, for webhooks and for the database connector alike.
F2  The database connector's exceptions (`OKWAN_EGRESS_ALLOW`) applied to
    webhooks too: one internal host named for a database became an address
    any account could add, and the ping a way to probe its ports.
    Webhooks take no exception.
F3  Every account's deliveries shared sixteen places: sixteen slow
    addresses, of a few accounts, held every other account's events. An
    account now sends to two addresses at once at most, an account with
    nothing being sent goes first, and an address's turn ends.
"""
from __future__ import annotations

import asyncio
import ipaddress
import time
from datetime import UTC, datetime, timedelta

import pytest
from okwan_api import webhooks as wh
from okwan_core.egress import EgressRefused, is_public, resolve_public
from okwan_vault import EnvMasterKey, MemoryStore, new_key

from tests import test_webhooks as data

HOST, T0, _run = data.HOST, data.T0, data._run
world, receiver = data.world, data.receiver

#: IPv6 forms of an internal IPv4 address, and the address each lands on.
FORMS = [
    ("64:ff9b::a9fe:a9fe", "169.254.169.254"),   # NAT64 well-known prefix: the metadata service
    ("64:ff9b::7f00:1", "127.0.0.1"),
    ("64:ff9b::a00:5", "10.0.0.5"),
    ("64:ff9b:1::808:808", "local-use NAT64"),   # a translator in someone's own network
    ("::a9fe:a9fe", "169.254.169.254"),          # IPv4-compatible
    ("::7f00:1", "127.0.0.1"),
    ("::ffff:0:a9fe:a9fe", "169.254.169.254"),   # IPv4-translated (SIIT)
    ("2002:a9fe:a9fe::1", "6to4 of 169.254.169.254"),
    ("2001:0:4136:e378:8000:63bf:3fff:fdd2", "Teredo"),
]


@pytest.mark.parametrize("form, lands_on", FORMS)
def test_f1_an_ipv6_form_of_an_internal_address_is_not_public(form, lands_on):
    assert not is_public(ipaddress.ip_address(form)), lands_on


def test_f1_public_addresses_stay_public_in_every_form():
    for form in ("8.8.8.8", "::ffff:8.8.8.8", "64:ff9b::808:808", "2606:4700:4700::1111"):
        assert is_public(ipaddress.ip_address(form)), form


@pytest.mark.parametrize("form, lands_on", FORMS[:3] + FORMS[4:7])
async def test_f1_such_an_address_is_never_added_pinged_or_dialed(world, receiver, monkeypatch, form, lands_on):
    dialed = []
    real_send = wh.httpx.AsyncClient.send

    async def spy(self, request, **kw):
        dialed.append(str(request.url))
        return await real_send(self, request, **kw)

    r = await world.client.post("/v1/webhooks", headers=data._auth(world.admin["acme"]),
                                json={"url": f"https://[{form}]/latest/meta-data/"})
    assert r.status_code == 422, (form, r.text)
    monkeypatch.setattr(wh.httpx.AsyncClient, "send", spy)   # Okwan's own sending, from here on
    status, error = await wh.send(f"https://[{form}]:{receiver.port}/x", "{}", {})
    assert status is None and error.startswith("refused") and dialed == []


async def test_f2_the_database_exceptions_open_no_webhook_address(world, receiver, monkeypatch):
    monkeypatch.setenv("OKWAN_EGRESS_ALLOW", "localhost,127.0.0.0/8,10.0.0.0/8")
    # The database connector keeps its exception ...
    assert await resolve_public("localhost", 5432) in ("127.0.0.1", "::1")
    # ... and a webhook address has none.
    for url in ("https://localhost/internal/admin?op=drop", "https://127.0.0.1/x", "https://10.0.0.5/x"):
        r = await world.client.post("/v1/webhooks", headers=data._auth(world.admin["acme"]), json={"url": url})
        assert r.status_code == 422, (url, r.text)
    with pytest.raises(EgressRefused):
        await resolve_public("localhost", 443, exceptions=False)
    status, error = await wh.send(f"https://localhost:{receiver.port}/x", "{}", {})
    assert status is None and error.startswith("refused") and receiver.requests == []


async def test_f3_slow_addresses_of_other_accounts_do_not_hold_up_an_account_with_nothing_sending(receiver):
    store = MemoryStore(EnvMasterKey(new_key()))
    data.set_store(store)
    at = datetime.now(UTC)
    receiver.slow["/slow"] = 3.0
    for i in range(wh.SENDING_AT_ONCE // 4):
        account = await store.create_tenant(f"Slow {i}")
        for j in range(5):
            await store.add_webhook_endpoint(account.id, url=f"https://{HOST}:{receiver.port}/slow{i}-{j}",
                                             secret=wh.new_secret(), created_by="s", at=at)
        await _run(account, [("B1", 1, "2026-11-09")], T0)
    victim = await store.create_tenant("Victim")
    await store.add_webhook_endpoint(victim.id, url=f"https://{HOST}:{receiver.port}/fast",
                                     secret=wh.new_secret(), created_by="s", at=at)
    await _run(victim, [("V1", 1, "2026-11-09")], T0)
    for (ep, _), d in store._deliveries.items():
        if store._endpoints[ep]["tenant_id"] == victim.id:
            d["next_at"] = at + timedelta(seconds=30)   # its delivery sorts last
    started = time.monotonic()
    await wh.deliver_due(at + timedelta(minutes=1), wait=False)
    while not any(r["path"] == "/fast" for r in receiver.requests) and time.monotonic() - started < 2.5:
        await asyncio.sleep(0.02)
    assert any(r["path"] == "/fast" for r in receiver.requests), "the account waited behind others' slow addresses"
    per_account: dict[str, int] = {}
    for account, _ in wh._sending.values():
        per_account[account] = per_account.get(account, 0) + 1
    assert max(per_account.values()) <= wh.ACCOUNT_AT_ONCE
    await asyncio.gather(*[task for _, task in wh._sending.values()])


async def test_f3_when_places_are_short_an_account_with_nothing_sending_goes_first(receiver, monkeypatch):
    """Two places. A busy account with three addresses takes both; another
    account's event arrives. When the first of the busy account's queues
    ends, the place goes to the other account, not to the busy account's
    third address, which was there first."""
    monkeypatch.setattr(wh, "SENDING_AT_ONCE", 2)
    store = MemoryStore(EnvMasterKey(new_key()))
    data.set_store(store)
    at = datetime.now(UTC)
    receiver.slow.update({"/slow0": 0.2, "/slow1": 5.0, "/slow2": 0.2})
    busy = await store.create_tenant("Busy")
    for j in range(3):
        await store.add_webhook_endpoint(busy.id, url=f"https://{HOST}:{receiver.port}/slow{j}",
                                         secret=wh.new_secret(), created_by="s", at=at)
    await _run(busy, [(f"B{i}", i, "2026-11-09") for i in range(10)], T0)
    await wh.deliver_due(at + timedelta(seconds=1), wait=False)
    victim = await store.create_tenant("Victim")
    await store.add_webhook_endpoint(victim.id, url=f"https://{HOST}:{receiver.port}/fast",
                                     secret=wh.new_secret(), created_by="s", at=at)
    await _run(victim, [("V1", 1, "2026-11-09")], T0)
    started = time.monotonic()
    try:
        while not any(r["path"] == "/fast" for r in receiver.requests) and time.monotonic() - started < 20:
            await wh.deliver_due(at + timedelta(minutes=1), wait=False)
            await asyncio.sleep(0.05)
        paths = [r["path"] for r in receiver.requests]
        assert "/fast" in paths
        assert "/slow2" not in paths[:paths.index("/fast")], "the busy account's third address went first"
    finally:
        for value in list(wh._sending.values()):
            task = value[1] if isinstance(value, tuple) else value
            task.cancel()


async def test_f3_an_account_that_was_just_served_waits_behind_one_that_was_not(receiver, monkeypatch):
    """One place. A busy account's queue keeps coming back, its deliveries
    older than anyone's; another account's event arrives. When the busy
    account's turn ends, the place goes to the account that has waited."""
    monkeypatch.setattr(wh, "SENDING_AT_ONCE", 1)
    monkeypatch.setattr(wh, "QUEUE_SECONDS", 0.4)
    store = MemoryStore(EnvMasterKey(new_key()))
    data.set_store(store)
    at = datetime.now(UTC)
    receiver.slow["/slow"] = 0.15
    busy = await store.create_tenant("Busy")
    await store.add_webhook_endpoint(busy.id, url=f"https://{HOST}:{receiver.port}/slow",
                                     secret=wh.new_secret(), created_by="s", at=at)
    await _run(busy, [(f"B{i}", i, "2026-11-09") for i in range(40)], T0)
    await wh.deliver_due(at + timedelta(seconds=1), wait=False)
    victim = await store.create_tenant("Victim")
    await store.add_webhook_endpoint(victim.id, url=f"https://{HOST}:{receiver.port}/fast",
                                     secret=wh.new_secret(), created_by="s", at=at)
    await _run(victim, [("V1", 1, "2026-11-09")], T0)
    started = time.monotonic()
    try:
        while not any(r["path"] == "/fast" for r in receiver.requests) and time.monotonic() - started < 6:
            await wh.deliver_due(at + timedelta(minutes=1), wait=False)
            await asyncio.sleep(0.02)
        paths = [r["path"] for r in receiver.requests]
        assert "/fast" in paths, "the busy account took every place that came free"
        assert paths.index("/fast") <= 6, "the waiting account was served after the busy one's next turn"
    finally:
        for value in list(wh._sending.values()):
            task = value[1] if isinstance(value, tuple) else value
            task.cancel()


async def test_f3_an_addresses_turn_ends_and_the_rest_goes_back(world, receiver, monkeypatch):
    monkeypatch.setattr(wh, "QUEUE_SECONDS", 0.5)
    receiver.slow["/okwan"] = 0.3
    await data._add(world)
    await _run(world.kofi, [(f"B{i}", i, "2026-11-09") for i in range(8)], T0)
    first = await wh.deliver_due()
    assert 1 <= first < 8, "the turn should have ended before the queue did"
    # What was left is due again at once, not held for the round's claim.
    total = first
    for _ in range(10):
        if total >= 8:
            break
        total += await wh.deliver_due()
    assert total == 8
