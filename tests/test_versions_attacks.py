"""The attacker's pass on dated API versions (docs/audit/red-team.md,
2026-11-24), each finding kept as the test that failed on the attacked
commit (52ce3fa) and passes on its fix.

- R1 (medium): an address added in the dashboard took the dashboard's
  version, which the dashboard names on every call, not the account's.
- R2 (medium): a dashboard naming a version the API serving it did not have
  yet (a deploy under way, a rollback) could not sign anyone in.
- R3 (low): a version stamped by a newer release (a rolling deploy, a
  rollback) gave a 500 on adding an address, was refused when sent back,
  and labelled a newer body as an older version.
- R4 (low): the command line stamped its checkout's newest version, not
  what the deployed API serves.
- R5 (low): billing, the operator console and the catalog answered an
  account on an older version in the newest.
- R6 (low): the test ping was never given in an older version.
- R7 (low): the guard that a change's routes are mounted could not see the
  routes of included routers.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from okwan_api import webhooks as wh
from okwan_api.auth import set_store
from okwan_core import versions
from okwan_vault import ADMIN, EnvMasterKey, PostgresStore, accounts, cli, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

from tests.test_versions import (  # noqa: F401  (fixtures)
    DSN,
    NEXT,
    ROOT,
    _account,
    _auth,
    _set_version,
    mounted,
    needs_db,
    next_version,
    world,
)
from tests.test_webhooks import HOST, T0, _run, _verify, receiver  # noqa: F401  (a fixture)

PASSWORD = "correct horse battery staple"


def _dashboard_version() -> str:
    found = re.search(r'export const API_VERSION = "([0-9-]+)";', (ROOT / "apps/dashboard/lib/api.ts").read_text())
    assert found
    return found.group(1)


async def _session(w, tenant_id: str) -> str:
    full, token_hash = new_token(SESSION_PREFIX)
    await w.store.create_session(tenant_id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    return full


def _without_currency(body):
    body["data"].pop("currency", None)
    body["data"]["old_shape"] = True
    return body


def _old(body):
    """A made-up change's way back, for an answer of any shape."""
    if isinstance(body, list):
        return {"data": body, "shape": "old"}
    body["shape"] = "old"
    return body


# ── R1 ─────────────────────────────────────────────────────────────────

async def test_r1_an_address_added_in_the_dashboard_is_on_the_accounts_version(world, receiver, next_version):  # noqa: F811
    w = world
    next_version(versions.Change("an open item's currency", _without_currency,
                                 events=frozenset({"open_item.opened"})))
    acme, _, read = await _account(w)
    assert acme.api_version == NEXT
    shop = await w.store.create_tenant("Kofi's Store", parent_id=acme.id)
    # Exactly what the dashboard sends: its session and its own version.
    r = await w.client.post("/v1/webhooks", json={"url": f"https://{HOST}/dash"}, headers={
        "Authorization": f"Bearer {await _session(w, acme.id)}", "Okwan-Version": _dashboard_version()})
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["api_version"] == NEXT
    await _run(shop, [("B1", 3884, "2026-11-09")], T0)
    assert await wh.deliver_due() == 1
    sent = _verify(made["secret"], receiver.requests[-1])
    listed = (await w.client.get("/v1/events", headers=_auth(read))).json()["data"][0]["body"]
    assert sent == listed and sent["api_version"] == NEXT and "old_shape" not in sent["data"]


async def test_r1_a_dashboard_on_a_newer_version_moves_no_accounts_address(world, receiver, next_version):  # noqa: F811
    w = world
    next_version(versions.Change("an open item's currency", _without_currency,
                                 events=frozenset({"open_item.opened"})))
    acme, _, _ = await _account(w)
    await _set_version(w, acme.id, versions.FIRST)
    shop = await w.store.create_tenant("Kofi's Store", parent_id=acme.id)
    r = await w.client.post("/v1/webhooks", json={"url": f"https://{HOST}/dash"}, headers={
        "Authorization": f"Bearer {await _session(w, acme.id)}", "Okwan-Version": NEXT})
    made = r.json()
    assert made["api_version"] == versions.FIRST
    await _run(shop, [("B1", 3884, "2026-11-09")], T0)
    assert await wh.deliver_due() == 1
    sent = _verify(made["secret"], receiver.requests[-1])
    assert sent["api_version"] == versions.FIRST and sent["data"]["old_shape"] is True


# ── R2 ─────────────────────────────────────────────────────────────────

async def test_r2_a_dashboard_ahead_of_the_api_still_signs_people_in(world):  # noqa: F811
    w = world
    ahead = "2026-12-01"
    assert not versions.known(ahead)
    acme, admin, _ = await _account(w)
    await w.store.add_account(acme.id, "owner@acme.test", accounts.hash_password(PASSWORD))
    r = await w.client.post("/v1/sessions", json={"email": "owner@acme.test", "password": PASSWORD},
                            headers={"Okwan-Version": ahead})
    assert r.status_code == 201, r.text
    assert r.headers["okwan-version"] == versions.current()
    r = await w.client.get("/v1/webhooks", headers=_auth(admin, ahead))
    assert r.status_code == 200 and r.headers["okwan-version"] == versions.current()


# ── R3 ─────────────────────────────────────────────────────────────────

NEWER = "2026-12-01"  # stamped by a release after this one; not in this list


async def test_r3_an_account_stamped_by_a_newer_release_is_served_in_this_ones_newest(world, receiver):  # noqa: F811
    w = world
    assert not versions.known(NEWER)
    acme, admin, _ = await _account(w)
    await _set_version(w, acme.id, NEWER)
    r = await w.client.post("/v1/webhooks", headers=_auth(admin), json={"url": f"https://{HOST}/a"})
    assert r.status_code == 201, r.text
    # The address keeps the account's stamp, and is listed as the version it is served in.
    assert r.json()["api_version"] == versions.current()
    said = r.headers["okwan-version"]
    assert said == versions.current()
    again = await w.client.get("/v1/webhooks", headers=_auth(admin, said))
    assert again.status_code == 200 and again.headers["okwan-version"] == said


def test_r3_a_body_from_a_newer_release_keeps_its_own_label():
    stored = json.dumps({"id": "evt_1", "type": "open_item.opened", "timestamp": "2026-12-02T09:00:00+00:00",
                         "account": "ten_x", "api_version": NEWER, "data": {"amount_at_stake_minor": 3884}},
                        separators=(",", ":"))
    assert wh.in_version(stored, versions.FIRST) == stored


# ── R4 ─────────────────────────────────────────────────────────────────

async def _cli_create(monkeypatch, capsys, checkout: tuple, today: str) -> str:
    monkeypatch.setenv("OKWAN_VAULT_DATABASE_URL", DSN)
    monkeypatch.setenv("OKWAN_VAULT_MASTER_KEY", base64.urlsafe_b64encode(new_key()).decode())
    monkeypatch.delenv("OKWAN_VAULT_KMS_KEY", raising=False)
    with monkeypatch.context() as m:
        m.setattr(versions, "VERSIONS", checkout)
        m.setattr(versions, "today", lambda: today, raising=False)
        await cli.tenant_create("Made by the operator")
    return re.search(r"tenant\s+(ten_[0-9a-f]+)", capsys.readouterr().out).group(1)


async def _answered(tenant_id: str) -> tuple[str, int]:
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    try:
        set_store(store)
        admin, _ = await store.issue_key(tenant_id, scope=ADMIN)
        from okwan_api.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
            r = await c.get("/v1/webhooks", headers=_auth(admin))
            return r.headers["okwan-version"], r.status_code
    finally:
        await store.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await store.close()


@needs_db
async def test_r4_a_checkout_ahead_of_production_stamps_the_day_not_its_newest(monkeypatch, capsys):
    production = versions.VERSIONS
    ahead = (*production, versions.Version(NEXT, "on a branch, not deployed"))
    tenant_id = await _cli_create(monkeypatch, capsys, ahead, "2026-11-24")
    assert await _answered(tenant_id) == (versions.FIRST, 200)


@needs_db
async def test_r4_a_checkout_behind_production_stamps_the_day_not_its_newest(monkeypatch, capsys):
    production = (*versions.VERSIONS, versions.Version(NEXT, "deployed"))
    tenant_id = await _cli_create(monkeypatch, capsys, versions.VERSIONS, "2026-12-03")
    monkeypatch.setattr(versions, "VERSIONS", production)
    assert await _answered(tenant_id) == (NEXT, 200)


# ── R5 ─────────────────────────────────────────────────────────────────

async def test_r5_billing_answers_an_account_in_its_version(world, next_version):  # noqa: F811
    w = world
    next_version(versions.Change("the billing page", _old, routes=frozenset({"GET /v1/billing"})))
    acme, _, _ = await _account(w)
    await _set_version(w, acme.id, versions.FIRST)
    r = await w.client.get("/v1/billing", headers={"Authorization": f"Bearer {await _session(w, acme.id)}"})
    assert r.status_code == 200, r.text
    assert r.headers["okwan-version"] == versions.FIRST and r.json()["shape"] == "old"


async def test_r5_the_operator_console_answers_the_operators_account_in_its_version(world, next_version, monkeypatch):  # noqa: F811
    w = world
    next_version(versions.Change("the console", _old, routes=frozenset({"GET /v1/ops/me"})))
    op, _, _ = await _account(w, "Okwan operations")
    await _set_version(w, op.id, versions.FIRST)
    await w.store.add_account(op.id, "ops@okwan.test", accounts.hash_password(PASSWORD))
    monkeypatch.setenv("OKWAN_OPERATOR_EMAILS", "ops@okwan.test")
    r = await w.client.get("/v1/ops/me", headers={"Authorization": f"Bearer {await _session(w, op.id)}"})
    assert r.status_code == 200, r.text
    assert r.headers["okwan-version"] == versions.FIRST and r.json()["shape"] == "old"


async def test_r5_a_key_sent_to_the_catalog_sets_its_version_and_nothing_else(world, next_version):  # noqa: F811
    w = world
    next_version(versions.Change("the catalog", _old, routes=frozenset({"GET /v1/connectors"})))
    acme, _, read = await _account(w)
    await _set_version(w, acme.id, versions.FIRST)
    r = await w.client.get("/v1/connectors", headers=_auth(read))
    assert r.status_code == 200 and r.headers["okwan-version"] == versions.FIRST and r.json()["shape"] == "old"
    # No key, or one that is not a key: as public as before, in the newest.
    for headers in ({}, _auth("okw_not_a_key")):
        r = await w.client.get("/v1/connectors", headers=headers)
        assert r.status_code == 200 and r.headers["okwan-version"] == NEXT and isinstance(r.json(), list)


# ── R6 ─────────────────────────────────────────────────────────────────

def _old_envelope(body):
    body["old_envelope"] = True
    return body


async def test_r6_the_ping_is_in_the_addresss_version_as_events_are(world, receiver, next_version):  # noqa: F811
    w = world
    next_version(versions.Change("the envelope", _old_envelope, events=frozenset({*wh.EVENT_TYPES, "ping"})))
    acme, admin, _ = await _account(w)
    shop = await w.store.create_tenant("Shop", parent_id=acme.id)
    old = (await w.client.post("/v1/webhooks", headers=_auth(admin),
                               json={"url": f"https://{HOST}/old", "api_version": versions.FIRST})).json()
    await _run(shop, [("B1", 3884, "2026-11-09")], T0)
    assert await wh.deliver_due() == 1
    event = _verify(old["secret"], receiver.requests[-1])
    assert {*wh.EVENT_TYPES, "ping"} == set(wh.SENT_TYPES)
    await w.client.post(f"/v1/webhooks/{old['id']}/test", headers=_auth(admin))
    ping = _verify(old["secret"], receiver.requests[-1])
    assert event["old_envelope"] is True and ping["old_envelope"] is True
    assert ping["api_version"] == event["api_version"] == versions.FIRST


# ── R7 ─────────────────────────────────────────────────────────────────

def test_r7_the_guard_sees_the_routes_of_included_routers(monkeypatch):
    from tests import test_versions as authored

    named = "GET /v1/webhooks"
    assert named in mounted()
    monkeypatch.setattr(versions, "VERSIONS", (versions.VERSIONS[0], versions.Version(
        NEXT, "x", (versions.Change("x", lambda b: b, routes=frozenset({named})),))))
    versions.check()
    authored.test_every_route_a_change_names_is_mounted_and_every_event_is_one_okwan_sends()
    monkeypatch.setattr(versions, "VERSIONS", (versions.VERSIONS[0], versions.Version(
        NEXT, "x", (versions.Change("x", lambda b: b, routes=frozenset({"GET /v1/webhook"})),))))
    with pytest.raises(AssertionError):
        authored.test_every_route_a_change_names_is_mounted_and_every_event_is_one_okwan_sends()
