"""The attacker's pass on the sandbox (docs/audit/red-team.md, 2026-11-24),
each finding kept as the test that failed on the attacked commit (6556bdc)
and passes on its fix.

- S1 (medium): the sandbox was a second day of paid model calls.
- S2 (medium): a test key's access alerts went to Okwan's operator, never
  to the account that can revoke it.
- S3 (low): the operator console counted a sandbox as a customer.
- S4 (low): a support ticket from the sandbox could not be answered.
- S5 (low): a half-made sandbox was kept and never given its week.
- S6 (low): the sample week aged out of the check it was moved for.
- S7 (low): the limit on issuing test keys covered one route of two, and
  the account could not see keys a test key had minted.
- S8 (low): nothing in a sandbox's event said it was one.
- S9 (low): every merchant made in a sandbox was an unlimited import write.
- S10 (medium): the live account's sign-in address reached every test key.
- And, seen in passing: on Postgres a tenant's usage answered `used` as a
  string.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import okwan_api.main  # noqa: F401  (registers the connectors and declarations)
import pytest
from okwan_api import access as api_access
from okwan_api import mail, ops, sandbox, support
from okwan_api import webhooks as wh
from okwan_api.auth import set_store
from okwan_api.suggestions import DAILY_ACCOUNT, SURFACE
from okwan_recon import exceptions as exc
from okwan_vault import (
    ADMIN,
    EnvMasterKey,
    MemoryStore,
    PostgresStore,
    accounts,
    new_key,
    totp,
)
from okwan_vault import access as rules
from okwan_vault.accounts import SESSION_PREFIX, new_token

from tests.test_webhooks import HOST, _verify, receiver  # noqa: F401  (a fixture)

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not DSN, reason="no vault database configured")
HOME = "198.51.100.20"
ELSEWHERE = "203.0.113.7"
OPS_EMAIL = f"ops-{os.urandom(3).hex()}@okwan.test"
OWNER_EMAIL = f"owner-{os.urandom(3).hex()}@acme.test"
ESCALATION = "escalation-inbox@okwan.test"
SECRET = totp.new_secret()


# ── the world ──────────────────────────────────────────────────────────

class Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_verification(self, email, link):  # pragma: no cover
        raise AssertionError("not here")

    async def send_notice(self, to, subject, text, html=None, reply_to=None):
        self.sent.append({"to": to, "subject": subject, "text": text})


class FakeModel:
    """`client.beta.messages.create` that plays a script (as tests/test_suggestions.py)."""

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=self)

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return self.script.pop(0)


def suggestion_answer():
    text = json.dumps({"suggestion": "none", "note": "Find the deposit for this batch in the bank file.",
                       "confidence": "low", "evidence": [{"ref": "F1", "why": "The batch has no deposit."}]})
    return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", content=[
        SimpleNamespace(type="text", text=text)])


@pytest.fixture(params=["memory", pytest.param("postgres", marks=needs_db)])
async def world(request, monkeypatch):
    if request.param == "memory":
        store = MemoryStore(EnvMasterKey(new_key()))
    else:
        store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    set_store(store)
    box = Outbox()
    mail.set_mailer(box)
    monkeypatch.setenv("OKWAN_SUPPORT_ESCALATION_TO", ESCALATION)
    monkeypatch.setenv("OKWAN_OPERATOR_EMAILS", OPS_EMAIL)
    monkeypatch.setattr(ops, "_replay", totp.ReplayGuard())
    w = SimpleNamespace(store=store, mail=box, kind=request.param)
    pw = accounts.hash_password("correct horse battery")
    w.hq = await store.create_tenant("Okwan HQ")
    await store.add_account(w.hq.id, OPS_EMAIL, pw)
    await store.put_operator_factor(OPS_EMAIL, SECRET)
    w.acme = await store.create_tenant("Acme ISV")
    await store.add_account(w.acme.id, OWNER_EMAIL, pw)
    w.shop = await store.create_tenant("Kofi's Store", parent_id=w.acme.id)
    w.admin = (await store.issue_key(w.acme.id, scope=ADMIN))[0]
    w.shop_admin = (await store.issue_key(w.shop.id, scope=ADMIN))[0]
    from okwan_api.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        w.client = c
        yield w
    support.set_client(None)
    mail._overridden = False
    mail._mailer = None
    if request.param == "postgres":
        for t in (w.acme, w.hq):
            await store.pool.execute("DELETE FROM tenants WHERE id = $1", t.id)
        await store.pool.execute("DELETE FROM operator_factors WHERE email = $1", OPS_EMAIL)
        await store.close()


def _auth(key: str, ip: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {key}"}
    if ip:
        h["CF-Connecting-IP"] = ip
    return h


async def _sandbox_and_key(w, scope=ADMIN):
    r = await w.client.post("/v1/sandbox", headers=_auth(w.admin))
    assert r.status_code in (200, 201), r.text
    made = r.json()
    r = await w.client.post("/v1/sandbox/keys", headers=_auth(w.admin), json={"scope": scope})
    assert r.status_code == 201, r.text
    return made, r.json()


async def _operator(w) -> str:
    full, token_hash = new_token(SESSION_PREFIX)
    await w.store.create_session(w.hq.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    code = totp.code_at(SECRET, totp.current_step())
    r = await w.client.post("/v1/ops/unlock", json={"code": code}, headers=_auth(full))
    assert r.status_code == 200, r.text
    return full




async def _check(w, key, tenant_id):
    r = await w.client.post(f"/v1/tenants/{tenant_id}/reconciliations/batches_deposits", headers=_auth(key))
    assert r.status_code == 200, r.text
    return r.json()


async def _readme_items(w, key, tenant_id):
    """What the samples' README says the week shows, for one tenant."""
    items = (await w.client.get("/v1/exceptions", headers=_auth(key))).json()["data"]
    return sorted(i["left_ref"] or i["right_ref"] for i in items if i["merchant_id"] == tenant_id)


README = ["20261016001", "B-1013"]


# ── S1 ─────────────────────────────────────────────────────────────────

async def test_s1_the_sandbox_shares_its_accounts_day_of_model_calls(world):
    w = world
    support.set_client(FakeModel(suggestion_answer()))
    for _ in range(DAILY_ACCOUNT):
        await w.store.record_request(w.acme.id, SURFACE)
    made, issued = await _sandbox_and_key(w)
    test = issued["secret"]
    await _check(w, test, made["id"])
    items = (await w.client.get("/v1/exceptions", headers=_auth(test))).json()["data"]
    item = next(i for i in items if i["left_ref"] == "B-1013")
    r = await w.client.post(
        f"/v1/tenants/{made['id']}/exceptions/pair/batches_deposits/{item['item_key']}/suggestions",
        headers=_auth(test))
    assert r.status_code == 429, r.text
    assert support.get_client().calls == []


async def test_s1_the_sandboxs_calls_count_against_the_live_account_too(world):
    w = world
    made, _ = await _sandbox_and_key(w)
    for _ in range(DAILY_ACCOUNT):
        await w.store.record_request(made["id"], SURFACE)
    support.set_client(FakeModel(suggestion_answer()))
    rows = [{"status": "unmatched_left", "left_ref": "#1001", "right_ref": None,
             "evidence": {"record": {"amount_minor": 3884, "currency": "USD", "when": "2026-11-20"}}}]
    at = datetime.now(UTC)
    kept = exc.carry(w.shop.id, "shopify_stripe", "pair", rows, None, at)
    await w.store.add_run(w.shop.id, kind="pair", name="shopify_stripe", surface="rest", status="ok",
                          started_at=at, finished_at=at, summary={"unmatched_left": 1}, rows=kept, error=None)
    key = next(k for k, _ in exc.keyed(w.shop.id, "shopify_stripe", "pair", rows))
    r = await w.client.post(f"/v1/tenants/{w.shop.id}/exceptions/pair/shopify_stripe/{key}/suggestions",
                            headers=_auth(w.admin))
    assert r.status_code == 429, r.text


# ── S2 ─────────────────────────────────────────────────────────────────

async def test_s2_a_test_keys_access_alert_goes_to_the_account_that_owns_it(world):
    w = world
    made, issued = await _sandbox_and_key(w)
    test, prefix = issued["secret"], issued["prefix"]
    await w.store.record_access(made["id"], credential=prefix, ip=HOME, network=rules.network_of(HOME),
                                surface="rest:files", target="/v1/files/bank_deposits/list",
                                at=datetime.now(UTC) - timedelta(days=3))
    r = await w.client.post("/v1/files/bank_deposits/list", json={"limit": 5}, headers=_auth(test, ELSEWHERE))
    assert r.status_code == 200, r.text
    await api_access.settle()
    to = [m["to"] for m in w.mail.sent]
    assert OWNER_EMAIL in to and ESCALATION not in to, to


# ── S3 ─────────────────────────────────────────────────────────────────

async def test_s3_the_console_counts_and_lists_customers_and_sandboxes_apart(world):
    w = world
    op = await _operator(w)
    before = (await w.client.get("/v1/ops/health", headers=_auth(op))).json()
    made, issued = await _sandbox_and_key(w)
    test = issued["secret"]
    assert (await w.client.post("/v1/tenants", headers=_auth(test), json={"name": "Sandbox Café"})).status_code == 201
    r = await w.client.post(f"/v1/tenants/{made['id']}/reconciliations/across/rails", headers=_auth(test))
    assert r.status_code == 401
    after = (await w.client.get("/v1/ops/health", headers=_auth(op))).json()
    assert (after["accounts"], after["merchants"]) == (before["accounts"], before["merchants"])
    assert after["sandboxes"] == before["sandboxes"] + 1
    assert not [f for f in after["runs"]["failures"] if made["id"] in (f["account_id"], f["tenant_id"])]
    listed = (await w.client.get("/v1/ops/accounts", headers=_auth(op))).json()["data"]
    assert made["id"] not in {a["id"] for a in listed}
    assert next(a for a in listed if a["id"] == w.acme.id)["sandbox"] == made["id"]


# ── S4 ─────────────────────────────────────────────────────────────────

async def test_s4_a_ticket_from_the_sandbox_reaches_the_accounts_owner(world):
    w = world
    _, issued = await _sandbox_and_key(w)
    brief = {"severity": "sev2", "category": "bug", "issue": "The sample check shows a batch not deposited",
             "findings": "B-1013 is open.", "likely_cause": "unknown", "resolution": "Explain the sample.",
             "draft_reply": "Hi, that is the sample week. Okwan Support"}
    support.set_client(FakeModel(
        SimpleNamespace(stop_reason="tool_use", model="m", content=[
            SimpleNamespace(type="tool_use", id="t1", name="escalate", input=brief,
                            model_dump=lambda **_: {"type": "tool_use", "id": "t1", "name": "escalate",
                                                    "input": brief})]),
        SimpleNamespace(stop_reason="end_turn", model="m", content=[
            SimpleNamespace(type="text", text="A person will follow up.",
                            model_dump=lambda **_: {"type": "text", "text": "A person will follow up."})])))
    r = await w.client.post("/v1/support/messages", headers=_auth(issued["secret"]),
                            json={"message": "Why is B-1013 open in my sandbox?"})
    assert r.status_code == 200, r.text
    ticket = r.json()["ticket"]
    page = next(m for m in w.mail.sent if m["to"] == ESCALATION)
    assert "no sign-in address on this account" not in page["text"] and OWNER_EMAIL in page["text"]
    op = await _operator(w)
    r = await w.client.post(f"/v1/ops/tickets/{ticket}/reply", headers=_auth(op),
                            json={"body": "That is the sample week: B-1013 is meant to be open."})
    assert r.status_code == 200, r.text
    assert any(m["to"] == OWNER_EMAIL for m in w.mail.sent)


# ── S5 ─────────────────────────────────────────────────────────────────

async def test_s5_a_half_made_sandbox_is_given_its_whole_week_when_asked_again(world, monkeypatch):
    w = world
    real_add, real_delete = w.store.add_import, w.store.delete_tenant
    calls = {"add": 0}

    async def flaky_add(*a, **k):
        calls["add"] += 1
        if calls["add"] == 2:
            raise ConnectionError("the store went away")
        return await real_add(*a, **k)

    async def gone_delete(*a, **k):
        raise ConnectionError("the store went away")

    monkeypatch.setattr(w.store, "add_import", flaky_add)
    monkeypatch.setattr(w.store, "delete_tenant", gone_delete)
    with pytest.raises(ConnectionError):
        await w.client.post("/v1/sandbox", headers=_auth(w.admin))
    monkeypatch.setattr(w.store, "add_import", real_add)
    monkeypatch.setattr(w.store, "delete_tenant", real_delete)
    made, issued = await _sandbox_and_key(w)
    key = issued["secret"]
    files = (await w.client.get(f"/v1/tenants/{made['id']}/imports", headers=_auth(key))).json()["imports"]
    assert sorted(f["filename"] for f in files) == ["sample-bank-statement.csv", "sample-processor-batches.csv"]
    await _check(w, key, made["id"])
    assert await _readme_items(w, key, made["id"]) == README


async def test_s5_a_sandbox_made_but_never_seeded_is_given_its_week_when_asked(world):
    w = world
    await w.store.make_sandbox(w.acme.id)  # the INSERT, then the process died
    made, issued = await _sandbox_and_key(w)
    files = (await w.client.get(f"/v1/tenants/{made['id']}/imports", headers=_auth(issued["secret"]))).json()
    assert len(files["imports"]) == 2


# ── S6 ─────────────────────────────────────────────────────────────────

async def test_s6_an_old_week_is_put_back_fresh_when_the_sandbox_is_asked_for(world, as_of, monkeypatch):
    w = world
    made, issued = await _sandbox_and_key(w)
    key = issued["secret"]
    m = (await w.client.post("/v1/tenants", headers=_auth(key), json={"name": "Shop"})).json()
    # 125 days later: the week is out of the check's 120 days.
    later = datetime.now(UTC) + timedelta(days=125)
    as_of(later.year, later.month, later.day)
    monkeypatch.setattr(sandbox, "_now", lambda: later, raising=False)
    assert (await _check(w, key, made["id"]))["data"] == []
    assert (await w.client.post("/v1/sandbox", headers=_auth(w.admin))).status_code == 200
    for tenant_id in (made["id"], m["id"]):
        await _check(w, key, tenant_id)
        assert await _readme_items(w, key, tenant_id) == README
    # A fresh week is left as it is.
    before = {f.id for f in await w.store.list_imports(made["id"])}
    await w.client.post("/v1/sandbox", headers=_auth(w.admin))
    assert {f.id for f in await w.store.list_imports(made["id"])} == before


# ── S7 ─────────────────────────────────────────────────────────────────

async def test_s7_test_keys_are_limited_on_every_route_and_the_account_sees_them_all(world):
    w = world
    made, issued = await _sandbox_and_key(w)
    minted = []
    for _ in range(18):  # 1 make + 1 issue above: 20 spent
        r = await w.client.post(f"/v1/tenants/{made['id']}/keys", headers=_auth(issued["secret"]),
                                json={"scope": "admin"})
        assert r.status_code == 201
        minted.append(r.json()["key_id"])
    r = await w.client.post(f"/v1/tenants/{made['id']}/keys", headers=_auth(issued["secret"]), json={"scope": "admin"})
    assert r.status_code == 429
    assert (await w.client.post("/v1/sandbox/keys", headers=_auth(w.admin))).status_code == 429
    listed = (await w.client.get("/v1/sandbox/keys", headers=_auth(w.admin))).json()["data"]
    assert {issued["key_id"], *minted} <= {k["key_id"] for k in listed}
    assert all(k["prefix"].startswith("okw_test_") and "secret" not in k for k in listed)
    # A live key's is not listed there, and a test key cannot list them.
    assert (await w.client.get("/v1/sandbox/keys", headers=_auth(issued["secret"]))).status_code == 403


# ── S8 ─────────────────────────────────────────────────────────────────

async def test_s8_an_event_says_whether_it_is_the_sandboxs(world, receiver):  # noqa: F811
    w = world
    made, issued = await _sandbox_and_key(w)
    test = issued["secret"]
    hook = (await w.client.post("/v1/webhooks", headers=_auth(test), json={"url": f"https://{HOST}/sb"})).json()
    await _check(w, test, made["id"])
    assert await wh.deliver_due() >= 1
    bodies = [_verify(hook["secret"], r) for r in receiver.requests]
    assert bodies and all(b["sandbox"] is True for b in bodies)
    listed = (await w.client.get("/v1/events", headers=_auth(test))).json()["data"]
    assert listed and all(e["body"]["sandbox"] is True for e in listed)
    live = (await w.client.post("/v1/webhooks", headers=_auth(w.admin), json={"url": f"https://{HOST}/live"})).json()
    await w.client.post(f"/v1/webhooks/{live['id']}/test", headers=_auth(w.admin))
    assert _verify(live["secret"], receiver.requests[-1])["sandbox"] is False


# ── S9 ─────────────────────────────────────────────────────────────────

async def test_s9_a_sandbox_holds_a_bounded_number_of_merchants_made_at_a_bounded_rate(world, monkeypatch):
    w = world
    _, issued = await _sandbox_and_key(w)
    test = issued["secret"]
    made_now = 0
    for _ in range(60):
        r = await w.client.post("/v1/tenants", headers=_auth(test), json={"name": f"Shop {made_now}"})
        if r.status_code != 201:
            break
        made_now += 1
    assert r.status_code == 429 and made_now == 18
    # With the hour's limit out of the way, the sandbox's size still holds.
    from okwan_api.ratelimit import Rule

    monkeypatch.setattr(sandbox, "SANDBOX_CHANGE", Rule("sandbox:change:test", 10_000, 3600), raising=False)
    for _ in range(60):
        r = await w.client.post("/v1/tenants", headers=_auth(test), json={"name": f"Shop {made_now}"})
        if r.status_code != 201:
            break
        made_now += 1
    assert r.status_code == 409 and made_now == sandbox.SANDBOX_TENANTS_MAX - 1


# ── S10 ────────────────────────────────────────────────────────────────

async def test_s10_no_test_key_reads_the_live_accounts_sign_in_address(world, receiver):  # noqa: F811
    w = world
    email = f"founder-{os.urandom(3).hex()}@example.test"
    _full, token_hash = new_token("okv")
    await w.store.add_signup(email, accounts.hash_password("correct horse battery"), token_hash,
                             datetime.now(UTC) + timedelta(hours=1))
    account = await w.store.complete_signup(token_hash)
    session, session_hash = new_token(SESSION_PREFIX)
    await w.store.create_session(account.id, session_hash, datetime.now(UTC) + timedelta(hours=1))
    try:
        made = (await w.client.post("/v1/sandbox", headers=_auth(session))).json()
        read = (await w.client.post("/v1/sandbox/keys", headers=_auth(session), json={"scope": "read"})).json()
        admin = (await w.client.post("/v1/sandbox/keys", headers=_auth(session), json={"scope": "admin"})).json()
        await w.client.post("/v1/webhooks", headers=_auth(admin["secret"]), json={"url": f"https://{HOST}/in"})
        await _check(w, admin["secret"], made["id"])
        seen = [made, (await w.client.get("/v1/exceptions", headers=_auth(read["secret"]))).json(),
                (await w.client.get("/v1/events", headers=_auth(read["secret"]))).json(),
                (await w.client.get("/v1/tenants", headers=_auth(admin["secret"]))).json(),
                (await w.client.get("/v1/sandbox", headers=_auth(session))).json()]
        assert email not in json.dumps(seen)
        assert made["name"] == "Sandbox"
    finally:
        if w.kind == "postgres":
            await w.store.pool.execute("DELETE FROM tenants WHERE id = $1", account.id)


# ── seen in passing ────────────────────────────────────────────────────

async def test_usage_is_a_number_in_every_store(world):
    w = world
    await w.store.record_request(w.acme.id, "rest:stripe")
    usage = (await w.client.get(f"/v1/tenants/{w.acme.id}/usage", headers=_auth(w.admin))).json()
    assert isinstance(usage["plan"]["used"], int) and usage["plan"]["used"] >= 1
