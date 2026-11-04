"""The operator console's API: only an allowlisted, unlocked operator gets in,
everyone else meets a 404, and everything the operator does is logged.

Both stores, like tests/test_support.py.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from okwan_api import ops
from okwan_api.main import app
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, accounts, new_key, totp
from okwan_vault.accounts import SESSION_PREFIX, new_token

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
NOW = datetime.now(UTC)
OPS_EMAIL = f"ops-{os.urandom(3).hex()}@okwan.test"
SECRET = totp.new_secret()

#: Every operator route, as (method, path template, body).
ROUTES = [
    ("GET", "/v1/ops/me", None),
    ("POST", "/v1/ops/unlock", {"code": "000000"}),
    ("POST", "/v1/ops/lock", None),
    ("GET", "/v1/ops/tickets", None),
    ("GET", "/v1/ops/tickets/{t}", None),
    ("POST", "/v1/ops/tickets/{t}/reply", {"body": "hello"}),
    ("POST", "/v1/ops/tickets/{t}/status", {"status": "resolved"}),
    ("GET", "/v1/ops/log", None),
]


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
        await s.pool.execute("DELETE FROM operator_factors WHERE email = $1", OPS_EMAIL)
        await s.close()


class Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_verification(self, email, link):  # pragma: no cover
        raise AssertionError("not here")

    async def send_notice(self, to, subject, text, html=None, reply_to=None):
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html,
                          "reply_to": reply_to})


@pytest.fixture
def outbox(monkeypatch):
    from okwan_api import mail

    box = Outbox()
    mail.set_mailer(box)
    monkeypatch.setenv("OKWAN_SUPPORT_ESCALATION_TO", "owner-inbox@okwan.test")
    yield box
    mail._overridden = False
    mail._mailer = None


@pytest.fixture(autouse=True)
def fresh_replay_guard(monkeypatch):
    monkeypatch.setattr(ops, "_replay", totp.ReplayGuard())


async def _session(store, tenant_id: str) -> str:
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(tenant_id, token_hash, NOW + timedelta(hours=1))
    return full


@pytest.fixture
async def world(store, monkeypatch):
    monkeypatch.setenv("OKWAN_OPERATOR_EMAILS", f"someone-else@okwan.test, {OPS_EMAIL.upper()}")
    pw = accounts.hash_password("correct horse battery")
    hq = await store.create_tenant("Okwan HQ")
    await store.add_account(hq.id, OPS_EMAIL, pw)
    await store.put_operator_factor(OPS_EMAIL, SECRET)
    a = await store.create_tenant("Acme ISV")
    contact = f"founder-{os.urandom(3).hex()}@acme.test"
    await store.add_account(a.id, contact, pw)
    b = await store.create_tenant("Rival ISV")
    conv = await store.save_conversation(a.id, None, [
        {"role": "user", "content": [{"type": "text", "text": "My Stripe test fails"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "test_connection",
                                           "input": {"connector": "stripe"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": "{\"status\": \"failed\"}"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "I've passed this on."}]},
    ], 1)
    ticket_a = await store.add_ticket(
        a.id, conversation_id=conv, severity="sev2", category="bug",
        summary="Stripe connection test fails", notified=True,
        brief={"findings": "Stripe answered 401", "likely_cause": "key rolled",
               "resolution": "new restricted key", "draft_reply": "Hi, please replace the key."})
    ticket_b = await store.add_ticket(
        b.id, conversation_id=None, severity="sev3", category="billing",
        summary="Wants the pro plan", notified=False)
    key, _ = await store.issue_key(hq.id)
    return SimpleNamespace(
        store=store, hq=hq, a=a, b=b, contact=contact, ticket_a=ticket_a, ticket_b=ticket_b,
        ops=await _session(store, hq.id), customer=await _session(store, a.id), ops_key=key)


@pytest.fixture
async def client(store):
    from okwan_api.auth import set_store

    set_store(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://okwan.test") as c:
        yield c


def _auth(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _unlock(client, world, token: str | None = None) -> httpx.Response:
    code = totp.code_at(SECRET, totp.current_step())
    return await client.post("/v1/ops/unlock", json={"code": code},
                             headers=_auth(token or world.ops))


async def _call(client, world, method, path, body, token):
    return await client.request(method, path.format(t=world.ticket_a.id), json=body,
                                headers=_auth(token))


# ── who gets in ────────────────────────────────────────────────────────

@pytest.mark.parametrize("route", ROUTES, ids=lambda r: f"{r[0]} {r[1]}")
@pytest.mark.parametrize("who", ["nobody", "customer", "operator_key", "operator_unlisted"])
async def test_anyone_but_an_operator_meets_a_plain_404(client, world, monkeypatch, route, who):
    """The console's existence is not advertised: the 404 is the one an
    unknown route gets."""
    method, path, body = route
    if who == "operator_unlisted":
        monkeypatch.delenv("OKWAN_OPERATOR_EMAILS")
    token = {"nobody": None, "customer": world.customer, "operator_key": world.ops_key,
             "operator_unlisted": world.ops}[who]
    r = await _call(client, world, method, path, body, token)
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_the_console_is_not_in_the_public_api_reference():
    paths = app.openapi()["paths"]
    assert not [p for p in paths if p.startswith("/v1/ops")]


@pytest.mark.parametrize("route", [r for r in ROUTES if r[1] not in
                                   ("/v1/ops/me", "/v1/ops/unlock", "/v1/ops/lock")],
                         ids=lambda r: f"{r[0]} {r[1]}")
async def test_a_locked_operator_sees_nothing(client, world, route):
    method, path, body = route
    r = await _call(client, world, method, path, body, world.ops)
    assert r.status_code == 403 and "authenticator" in r.json()["detail"]


async def test_me_says_enrolled_and_locked(client, world):
    r = await client.get("/v1/ops/me", headers=_auth(world.ops))
    assert r.json() == {"email": OPS_EMAIL, "enrolled": True, "unlocked_until": None}


# ── the second factor ──────────────────────────────────────────────────

async def test_the_right_code_unlocks_for_thirty_minutes(client, world, store):
    r = await _unlock(client, world)
    assert r.status_code == 200
    until = datetime.fromisoformat(r.json()["unlocked_until"])
    assert timedelta(minutes=29) < until - datetime.now(UTC) <= timedelta(minutes=30)
    assert (await client.get("/v1/ops/tickets", headers=_auth(world.ops))).status_code == 200
    assert [e["action"] for e in await store.operator_log()][:2] == ["list_tickets", "unlock"]


async def test_a_code_works_once(client, world):
    code = totp.code_at(SECRET, totp.current_step())
    first = await client.post("/v1/ops/unlock", json={"code": code}, headers=_auth(world.ops))
    again = await client.post("/v1/ops/unlock", json={"code": code}, headers=_auth(world.ops))
    assert (first.status_code, again.status_code) == (200, 401)


async def test_a_wrong_code_is_refused_logged_and_rate_limited(client, world, store):
    # Postgres keeps the log across tests (nothing can clear it): count what this adds.
    before = sum(e["action"] == "unlock_failed" for e in await store.operator_log(500))
    codes = []
    for _ in range(6):
        r = await client.post("/v1/ops/unlock", json={"code": "123456"}, headers=_auth(world.ops))
        codes.append(r.status_code)
    assert codes[:5] == [401] * 5 and codes[5] == 429
    after = sum(e["action"] == "unlock_failed" for e in await store.operator_log(500))
    assert after - before == 5


async def test_unlocking_one_session_leaves_the_others_locked(client, world, store):
    other = await _session(store, world.hq.id)
    await _unlock(client, world)
    assert (await client.get("/v1/ops/tickets", headers=_auth(other))).status_code == 403


async def test_an_expired_unlock_locks_again(client, world, store):
    await _unlock(client, world)
    await store.set_session_operator(accounts.hash_token(world.ops), NOW - timedelta(seconds=1))
    assert (await client.get("/v1/ops/tickets", headers=_auth(world.ops))).status_code == 403


async def test_lock_ends_the_unlock(client, world):
    await _unlock(client, world)
    await client.post("/v1/ops/lock", headers=_auth(world.ops))
    assert (await client.get("/v1/ops/tickets", headers=_auth(world.ops))).status_code == 403


async def test_without_enrolment_the_operator_is_told_how(client, world, store, monkeypatch):
    hq2 = await store.create_tenant("Okwan HQ 2")
    email = f"ops2-{os.urandom(3).hex()}@okwan.test"
    await store.add_account(hq2.id, email, accounts.hash_password("correct horse battery"))
    monkeypatch.setenv("OKWAN_OPERATOR_EMAILS", email)
    r = await client.post("/v1/ops/unlock", json={"code": "123456"},
                          headers=_auth(await _session(store, hq2.id)))
    assert r.status_code == 409 and "operator enroll" in r.json()["detail"]


# ── the support inbox ──────────────────────────────────────────────────

async def test_the_inbox_lists_open_tickets_across_accounts(client, world):
    await _unlock(client, world)
    rows = (await client.get("/v1/ops/tickets", headers=_auth(world.ops))).json()["data"]
    mine = {r["id"]: r for r in rows if r["id"] in (world.ticket_a.id, world.ticket_b.id)}
    assert set(mine) == {world.ticket_a.id, world.ticket_b.id}
    a = mine[world.ticket_a.id]
    assert (a["issue"], a["account"], a["contact"], a["status"]) == (
        "Stripe connection test fails", "Acme ISV", world.contact, "open")
    assert mine[world.ticket_b.id]["contact"] is None


async def test_a_ticket_shows_its_brief_and_conversation_and_the_view_is_logged(client, world, store):
    await _unlock(client, world)
    r = await client.get(f"/v1/ops/tickets/{world.ticket_a.id}", headers=_auth(world.ops))
    t = r.json()
    assert t["brief"]["draft_reply"] == "Hi, please replace the key."
    assert t["conversation"] == [
        {"from": "customer", "text": "My Stripe test fails"},
        {"from": "tool", "text": "test_connection"},
        {"from": "assistant", "text": "I've passed this on."},
    ]
    assert t["replies"] == []
    entry = (await store.operator_log())[0]
    assert (entry["action"], entry["target"], entry["operator"]) == (
        "view_ticket", world.ticket_a.id, OPS_EMAIL)


async def test_a_reply_reaches_the_customer_and_answers_the_ticket(client, world, store, outbox):
    await _unlock(client, world)
    r = await client.post(f"/v1/ops/tickets/{world.ticket_a.id}/reply",
                          json={"body": "Hi,\n\nPlease save a new restricted key. <b>Thanks</b>"},
                          headers=_auth(world.ops))
    assert r.status_code == 200 and r.json()["status"] == "answered"
    sent = outbox.sent[0]
    assert sent["to"] == world.contact and sent["reply_to"] == "owner-inbox@okwan.test"
    assert sent["subject"] == f"Re: your Okwan support request ({world.ticket_a.id})"
    assert "&lt;b&gt;Thanks&lt;/b&gt;" in sent["html"] and "<b>" not in sent["html"]
    assert "<a " not in sent["html"]
    t = await store.get_ticket(world.ticket_a.id)
    assert t.status == "answered"
    replies = await store.replies_for(world.a.id, world.ticket_a.id)
    assert [(x["operator"], x["sent"]) for x in replies] == [(OPS_EMAIL, True)]
    assert (await store.operator_log())[0]["action"] == "reply"


async def test_reply_and_resolve(client, world, store, outbox):
    await _unlock(client, world)
    await client.post(f"/v1/ops/tickets/{world.ticket_a.id}/reply",
                      json={"body": "Fixed now.", "resolve": True}, headers=_auth(world.ops))
    t = await store.get_ticket(world.ticket_a.id)
    assert t.status == "resolved" and t.resolved_at is not None
    open_ids = [x["id"] for x in (await client.get("/v1/ops/tickets",
                                                   headers=_auth(world.ops))).json()["data"]]
    assert world.ticket_a.id not in open_ids


async def test_a_reply_with_a_secret_is_refused(client, world, outbox):
    await _unlock(client, world)
    r = await client.post(f"/v1/ops/tickets/{world.ticket_a.id}/reply",
                          json={"body": "use sk_live_51HxQ2bL0ngSecretValue"}, headers=_auth(world.ops))
    assert r.status_code == 422 and "secret" in r.json()["detail"]
    assert outbox.sent == []


async def test_a_reply_without_a_mailer_is_503(client, world):
    from okwan_api import mail

    await _unlock(client, world)
    mail.set_mailer(None)
    try:
        r = await client.post(f"/v1/ops/tickets/{world.ticket_a.id}/reply",
                              json={"body": "hi"}, headers=_auth(world.ops))
    finally:
        mail._overridden = False
    assert r.status_code == 503


async def test_a_ticket_without_a_contact_cannot_be_replied_to(client, world, outbox):
    await _unlock(client, world)
    r = await client.post(f"/v1/ops/tickets/{world.ticket_b.id}/reply",
                          json={"body": "hi"}, headers=_auth(world.ops))
    assert r.status_code == 409 and outbox.sent == []


async def test_status_changes_are_logged(client, world, store):
    await _unlock(client, world)
    r = await client.post(f"/v1/ops/tickets/{world.ticket_b.id}/status",
                          json={"status": "resolved"}, headers=_auth(world.ops))
    assert r.status_code == 200
    assert (await store.get_ticket(world.ticket_b.id)).status == "resolved"
    entry = (await store.operator_log())[0]
    assert (entry["action"], entry["detail"]["status"]) == ("set_status", "resolved")


async def test_the_log_is_readable_in_the_console(client, world):
    await _unlock(client, world)
    await client.get("/v1/ops/tickets", headers=_auth(world.ops))
    log = (await client.get("/v1/ops/log", headers=_auth(world.ops))).json()["data"]
    actions = [e["action"] for e in log if e["operator"] == OPS_EMAIL]
    assert actions[:2] == ["list_tickets", "unlock"]


async def test_the_log_cannot_be_changed_in_postgres(world, store):
    if not isinstance(store, PostgresStore):
        pytest.skip("the append-only rule is the database's")
    await store.log_operator(OPS_EMAIL, "probe", None, None)
    for sql in ("UPDATE operator_actions SET action = 'edited'",
                "DELETE FROM operator_actions", "TRUNCATE operator_actions"):
        with pytest.raises(Exception, match="append-only"):
            await store.pool.execute(sql)


def test_the_setup_file_carries_the_key_for_an_authenticator_app():
    from okwan_vault.cli import setup_text

    text = setup_text("ops@okwan.test", SECRET)
    assert totp.grouped(SECRET) in text
    assert totp.setup_uri(SECRET, "ops@okwan.test") in text
    assert "delete this file" in text
