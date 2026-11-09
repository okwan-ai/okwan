"""The access log: every read of a tenant's data, who made it and from where.

What would fail silently: a read that leaves no row, a row naming the
wrong key or Render's proxy instead of the caller, the full key or the
SQL text written into the log, an alert that never fires when a key turns
up somewhere new, an alert on every request of a new deployment, a volume
alert repeated every hour, or rows kept past 90 days. The store checks run
against both stores; the Postgres half needs OKWAN_VAULT_DATABASE_URL,
like tests/test_tenant_rls.py.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, new_key
from okwan_vault import access as rules
from okwan_vault.accounts import SESSION_PREFIX, new_token

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
HOME = "198.51.100.20"      # where the key has always run from
ELSEWHERE = "203.0.113.7"   # somewhere it has never been


class Mailbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send_verification(self, email: str, link: str) -> None:
        raise AssertionError("not a verification")

    async def send_notice(self, to, subject, text, html=None, reply_to=None) -> None:
        self.sent.append((to, subject, text))


@pytest.fixture
def mailbox(monkeypatch):
    from okwan_api import mail

    box = Mailbox()
    monkeypatch.setattr(mail, "_mailer", box)
    monkeypatch.setattr(mail, "_overridden", True)
    monkeypatch.setenv("OKWAN_SUPPORT_ESCALATION_TO", "ops@okwan.test")
    return box


@pytest.fixture
async def api(mailbox):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    shop = await store.create_tenant("Corner Café")
    await store.add_account(shop.id, "owner@cafe.test", "not-a-real-hash")
    key, _ = await store.issue_key(shop.id, scope="admin")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://okwan.test") as c:
        yield SimpleNamespace(client=c, store=store, shop=shop, key=key, mail=mailbox)


def _from(key: str, address: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "CF-Connecting-IP": address}


async def _read(api, address: str = HOME, key: str | None = None) -> httpx.Response:
    r = await api.client.post("/v1/files/bank_deposits/list", json={"limit": 5},
                              headers=_from(key or api.key, address))
    assert r.status_code == 200, r.text
    return r


async def _history(api, address: str = HOME, age: timedelta = timedelta(days=3)) -> None:
    """The key has been reading from `address` since `age` ago."""
    await api.store.record_access(
        api.shop.id, credential=api.key[:12], ip=address, network=rules.network_of(address),
        surface="rest:files", target="/v1/files/bank_deposits/list",
        at=datetime.now(UTC) - age)


async def _log(api) -> list[dict]:
    return await api.store.access_entries(api.shop.id, datetime.now(UTC) - timedelta(days=90))


# ── the rules ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(("address", "network"), [
    ("203.0.113.7", "203.0.113.0/24"),
    ("2001:db8:abcd:12::1", "2001:db8:abcd::/48"),
    ("::ffff:203.0.113.7", "203.0.113.0/24"),
    ("unknown", "unknown"),
    ("", "unknown"),
])
def test_an_address_is_read_as_the_network_one_site_is_given(address, network):
    assert rules.network_of(address) == network


def test_a_key_is_logged_by_its_public_prefix_and_anything_else_as_a_session():
    from okwan_api.access import credential_for
    from okwan_vault import apikey

    full, prefix, _ = apikey.generate()
    assert credential_for(full) == prefix == full[:12]
    assert len(prefix) < len(full) // 3
    session, _ = new_token(SESSION_PREFIX)
    assert credential_for(session) == rules.SESSION


# ── every read is a row ───────────────────────────────────────────────

async def test_a_rest_read_is_logged_with_the_key_prefix_the_callers_address_and_the_route(api):
    await _read(api, ELSEWHERE)
    [row] = await _log(api)
    assert row["tenant_id"] == api.shop.id
    assert row["credential"] == api.key[:12]
    assert (row["ip"], row["network"]) == (ELSEWHERE, "203.0.113.0/24")
    assert (row["surface"], row["target"]) == ("rest:files", "/v1/files/bank_deposits/list")


async def test_the_full_key_and_the_query_text_are_never_written(api):
    await _read(api)
    r = await api.client.post("/v1/query", headers=_from(api.key, HOME),
                              json={"sql": "SELECT count(*) AS n FROM files.bank_deposits"})
    assert r.status_code == 200, r.text
    rows = await _log(api)
    assert [r["target"] for r in rows] == ["/v1/query", "/v1/files/bank_deposits/list"]
    text = repr(rows)
    assert api.key not in text
    assert api.key[:12] in text
    assert "SELECT" not in text and "bank_deposits\"" not in text


async def test_a_refused_key_and_an_unmetered_call_leave_no_row(api):
    r = await api.client.post("/v1/files/bank_deposits/list", json={},
                              headers=_from("okw_" + "x" * 43, ELSEWHERE))
    assert r.status_code == 401
    assert (await api.client.get("/v1/connectors")).status_code == 200
    assert (await api.client.get(f"/v1/tenants/{api.shop.id}/access",
                                 headers=_from(api.key, HOME))).status_code == 200
    assert await _log(api) == []


async def test_a_dashboard_read_is_logged_as_a_session_from_the_browsers_address(api, monkeypatch):
    monkeypatch.setenv("OKWAN_DASHBOARD_SECRET", "s3cret-for-tests")
    full, token_hash = new_token(SESSION_PREFIX)
    await api.store.create_session(api.shop.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    r = await api.client.post(
        f"/v1/tenants/{api.shop.id}/reconciliations/batches_deposits",
        headers={"Authorization": f"Bearer {full}", "X-Okwan-Client-IP": ELSEWHERE,
                 "X-Okwan-Dashboard-Secret": "s3cret-for-tests", "CF-Connecting-IP": "10.0.0.1"})
    assert r.status_code == 200, r.text
    [row] = await _log(api)
    assert (row["credential"], row["ip"], row["surface"]) == ("session", ELSEWHERE, "dashboard:reconcile")
    assert full not in repr(row)


async def test_a_hosted_mcp_read_names_the_tool_and_what_it_ran(api):
    from okwan_query.mcp_http import build_server

    ctx = SimpleNamespace(headers={"authorization": f"Bearer {api.key}", "cf-connecting-ip": HOME})
    tools = build_server()._tool_manager
    out = await tools.get_tool("okwan_reconcile").fn(name="batches_deposits", ctx=ctx)
    assert "error" not in out, out
    [row] = await _log(api)
    assert (row["credential"], row["ip"]) == (api.key[:12], HOME)
    assert (row["surface"], row["target"]) == ("mcp:reconcile", "okwan_reconcile batches_deposits")


# ── a key somewhere new ───────────────────────────────────────────────

async def test_an_established_key_on_a_new_network_alerts_the_account_once(api):
    from okwan_api import access

    await _history(api)
    await _read(api, HOME)
    await access.settle()
    assert api.mail.sent == [], "the key's own network is not news"

    await _read(api, ELSEWHERE)
    await _read(api, "203.0.113.99")  # same /24: the same new network
    await access.settle()
    [alert] = await api.store.access_alerts(api.shop.id, datetime.now(UTC) - timedelta(days=1))
    assert (alert["kind"], alert["credential"], alert["mailed"]) == ("new_network", api.key[:12], True)
    assert alert["detail"]["network"] == "203.0.113.0/24"

    [(to, subject, text), (ops, ops_subject, _)] = api.mail.sent
    assert (to, ops) == ("owner@cafe.test", "ops@okwan.test")
    assert api.key[:12] in subject and ops_subject.startswith("[Access alert]")
    assert ELSEWHERE in text and "203.0.113.0/24" in text and "Corner Café" in text
    assert "/settings?tab=keys" in text
    assert api.key not in text


async def test_a_new_key_and_a_dashboard_session_do_not_alert(api):
    from okwan_api import access

    await _read(api, ELSEWHERE)                             # its first read ever
    await _history(api, HOME, age=timedelta(hours=2))       # under a day of history
    await _read(api, "192.0.2.10")
    full, token_hash = new_token(SESSION_PREFIX)
    await api.store.create_session(api.shop.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    await api.store.record_access(api.shop.id, credential="session", ip=HOME, network="198.51.100.0/24",
                                  surface="dashboard:reconcile", at=datetime.now(UTC) - timedelta(days=5))
    r = await api.client.post(f"/v1/tenants/{api.shop.id}/reconciliations/batches_deposits",
                              headers={"Authorization": f"Bearer {full}", "CF-Connecting-IP": ELSEWHERE})
    assert r.status_code == 200, r.text
    await access.settle()
    assert await api.store.access_alerts(api.shop.id, datetime.now(UTC) - timedelta(days=1)) == []
    assert api.mail.sent == []


async def test_a_network_unused_for_thirty_days_is_new_again(api):
    from okwan_api import access

    await _history(api, ELSEWHERE, age=timedelta(days=45))
    await _history(api, HOME, age=timedelta(days=2))
    await _read(api, ELSEWHERE)
    await access.settle()
    assert [a["kind"] for a in await api.store.access_alerts(
        api.shop.id, datetime.now(UTC) - timedelta(days=1))] == ["new_network"]


async def test_an_alert_that_cannot_be_mailed_is_still_kept(api, monkeypatch):
    from okwan_api import access, mail

    monkeypatch.setattr(mail, "_mailer", None)
    await _history(api)
    await _read(api, ELSEWHERE)
    await access.settle()
    [alert] = await api.store.access_alerts(api.shop.id, datetime.now(UTC) - timedelta(days=1))
    assert alert["mailed"] is False


# ── a key reading far more than usual ─────────────────────────────────

async def _hour(store, tenant_id, credential, n, ending: datetime) -> None:
    for i in range(n):
        await store.record_access(tenant_id, credential=credential, ip=HOME, network="198.51.100.0/24",
                                  surface="rest:stripe", at=ending - timedelta(seconds=i + 1))


async def test_an_hour_far_above_the_keys_busiest_alerts_once_a_day(api):
    from okwan_api import access

    now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
    key = api.key[:12]
    await _hour(api.store, api.shop.id, key, 20, now - timedelta(days=2))
    await _hour(api.store, api.shop.id, key, 150, now)
    assert await access.check_volume(now) == 1
    [alert] = await api.store.access_alerts(api.shop.id, now - timedelta(days=1))
    assert alert["detail"] | {"hour_to": None} == {"requests": 150, "busiest": 20, "hour_to": None}
    assert "150 reads" in api.mail.sent[0][2] and "was 20" in api.mail.sent[0][2]
    assert await access.check_volume(now + timedelta(minutes=10)) == 0, "one alert a day per key"


async def test_a_busy_key_at_its_usual_pace_and_a_quiet_hour_do_not_alert(api):
    from okwan_api import access

    now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
    busy, quiet = api.key[:12], "okw_quietkey"
    await _hour(api.store, api.shop.id, busy, 200, now - timedelta(days=1))
    await _hour(api.store, api.shop.id, busy, 400, now)   # twice its busiest: within 3x
    await _hour(api.store, api.shop.id, quiet, 60, now)   # under the floor
    await _hour(api.store, api.shop.id, "session", 500, now)  # people, not keys
    assert await access.check_volume(now) == 0


# ── both stores agree ─────────────────────────────────────────────────

@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.skipif(
    not DSN, reason="no vault database configured"))])
async def store(request):
    if request.param == "memory":
        yield MemoryStore(EnvMasterKey(new_key()))
        return
    s = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    made: list[str] = []
    create = s.create_tenant

    async def tracked(name, parent_id=None):
        t = await create(name, parent_id=parent_id)
        made.append(t.id)
        return t

    s.create_tenant = tracked
    try:
        yield s
    finally:
        for tenant_id in reversed(made):
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await s.close()


async def test_what_the_store_knew_before_each_row(store):
    t = await store.create_tenant("Shop")
    now = datetime.now(UTC)
    row = {"credential": "okw_abcdefgh", "ip": HOME, "network": "198.51.100.0/24", "surface": "rest:x"}
    first = await store.record_access(t.id, **row, at=now - timedelta(days=40))
    assert (first.known_network, first.first_seen) == (False, None)
    stale = await store.record_access(t.id, **row, at=now - timedelta(days=5))
    assert stale.known_network is False, "40 days ago is outside the 30-day memory"
    assert abs(stale.first_seen - (now - timedelta(days=40))) < timedelta(seconds=1)
    known = await store.record_access(t.id, **row, at=now)
    assert known.known_network is True
    other = await store.record_access(t.id, **{**row, "network": "203.0.113.0/24"}, at=now)
    assert other.known_network is False


async def test_a_parent_reads_its_merchants_log_and_a_merchant_only_its_own(store):
    isv = await store.create_tenant("ISV")
    m1 = await store.create_tenant("M1", parent_id=isv.id)
    m2 = await store.create_tenant("M2", parent_id=isv.id)
    for t in (isv, m1, m2):
        await store.record_access(t.id, credential="okw_isvkey00", ip=HOME,
                                  network="198.51.100.0/24", surface="rest:x")
        await store.add_access_alert(t.id, kind="new_network", credential="okw_isvkey00", detail={"n": 1})
    since = datetime.now(UTC) - timedelta(hours=1)
    assert {r["tenant_id"] for r in await store.access_entries(isv.id, since)} == {isv.id, m1.id, m2.id}
    assert {r["tenant_id"] for r in await store.access_entries(m1.id, since)} == {m1.id}
    assert {a["tenant_id"] for a in await store.access_alerts(m2.id, since)} == {m2.id}
    assert [a["detail"] for a in await store.access_alerts(m2.id, since)] == [{"n": 1}]


async def test_volume_spikes_and_their_quiet_day(store):
    t = await store.create_tenant("Shop")
    now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
    await _hour(store, t.id, "okw_spikykey", 10, now - timedelta(days=3))
    await _hour(store, t.id, "okw_spikykey", 120, now)
    spikes = await store.volume_spikes(now)
    assert [(s["tenant_id"], s["credential"], s["requests"], s["busiest"]) for s in spikes] == [
        (t.id, "okw_spikykey", 120, 10)]
    await store.add_access_alert(t.id, kind="volume", credential="okw_spikykey", detail={}, at=now)
    assert await store.volume_spikes(now + timedelta(minutes=5)) == []


async def test_rows_age_out_after_90_days_and_alerts_after_400(store):
    t = await store.create_tenant("Shop")
    now = datetime.now(UTC)
    for age in (91, 89):
        await store.record_access(t.id, credential="okw_abcdefgh", ip=HOME, network="198.51.100.0/24",
                                  surface="rest:x", at=now - timedelta(days=age))
    for age in (401, 300):
        await store.add_access_alert(t.id, kind="volume", credential="okw_abcdefgh", detail={},
                                     at=now - timedelta(days=age))
    assert await store.sweep_access(now) >= 1
    ages = [round((now - r["at"]) / timedelta(days=1))
            for r in await store.access_entries(t.id, now - timedelta(days=500))]
    assert ages == [89]
    assert [round((now - a["at"]) / timedelta(days=1))
            for a in await store.access_alerts(t.id, now - timedelta(days=500))] == [300]


async def test_deleting_a_tenant_deletes_its_log(store):
    t = await store.create_tenant("Shop")
    await store.record_access(t.id, credential="okw_abcdefgh", ip=HOME, network="198.51.100.0/24",
                              surface="rest:x")
    await store.add_access_alert(t.id, kind="volume", credential="okw_abcdefgh", detail={})
    await store.delete_tenant(t.id)
    since = datetime.now(UTC) - timedelta(days=1)
    assert await store.access_entries(t.id, since) == []
    assert await store.access_alerts(t.id, since) == []
