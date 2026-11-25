"""An attacker's pass on the dashboard's Test mode (okwan_api.auth.in_mode,
asked_mode; billing.session_owner; the hosted MCP; the operator console).

What Test mode promises (docs/audit/controls.md, C67): a session sent with
`Okwan-Mode: test` acts as its account's sandbox and reaches nothing live;
without it, it is the live account and reaches nothing in the sandbox;
billing refuses Test mode; it is the same session, so it ends with it; and a
key's own kind decides where it reaches, so a key sent with an
`Okwan-Mode` that is not its own is refused.

Each test asserts the safe behaviour. The ones under "Findings" failed on
the attacked commit (8f4e712) and pass since its fixes; the ones under
"Held" passed then and stay as regression tests.
Both stores, through tests/test_sandbox.py's `world`.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from okwan_api import ops
from okwan_vault import ADMIN, READ, PostgresStore, accounts, totp
from okwan_vault.accounts import SESSION_PREFIX, new_token

from tests.test_sandbox import _auth, _sandbox, _test_key, world  # noqa: F401  (a fixture)

TEST = {"Okwan-Mode": "test"}
LIVE = {"Okwan-Mode": "live"}


async def _session(w, tenant_id=None, ttl=timedelta(hours=1)):
    full, token_hash = new_token(SESSION_PREFIX)
    await w.store.create_session(tenant_id or w.acme.id, token_hash, datetime.now(UTC) + ttl)
    return full


def _as(token, mode=None):
    return {**_auth(token), **(mode or {})}


async def _alive(w, token) -> bool:
    """Whether the session still opens the live account."""
    return (await w.client.get("/v1/tenants", headers=_as(token))).status_code == 200


def _ctx(key: str, **headers: str) -> SimpleNamespace:
    """A hosted MCP tool call's context, as the HTTP transport hands it:
    lowercased headers."""
    return SimpleNamespace(headers={"authorization": f"Bearer {key}", **headers})


def _tool(name: str):
    from okwan_query.mcp_http import build_server

    return build_server()._tool_manager.get_tool(name).fn


# ═══ Findings: these failed on the attacked commit ═══════════════════════════


async def test_the_hosted_mcp_refuses_a_live_key_sent_with_test_mode(world):  # noqa: F811
    """FINDING. REST refuses a live key that says `Okwan-Mode: test` (400:
    "no one believes a live key is testing"); the hosted MCP never reads the
    header, so an MCP client configured with it is served live data."""
    w = world
    for tool in ("okwan_open_items", "okwan_describe_tables"):
        out = await _tool(tool)(ctx=_ctx(w.read, **{"okwan-mode": "test"}))
        assert "error" in out, (tool, "a live key said Okwan-Mode: test and was served live data")
        assert not out.get("data") and not out.get("tables")


async def test_the_hosted_mcp_refuses_a_test_key_sent_with_live_mode(world):  # noqa: F811
    """FINDING. The other way round: a test key that says `live` is refused
    on REST, and on the hosted MCP is served as if the header were not there."""
    w = world
    await _sandbox(w)
    test_read = (await _test_key(w, READ))["secret"]
    out = await _tool("okwan_open_items")(ctx=_ctx(test_read, **{"okwan-mode": "live"}))
    assert "error" in out, "a test key said Okwan-Mode: live and was answered"


@pytest.mark.parametrize("case", ["no sandbox made", "sandbox gone", "not a mode", "sent twice"])
async def test_sign_out_ends_the_session_whatever_okwan_mode_says(world, case):  # noqa: F811
    """FINDING. DELETE /v1/sessions/current authenticates through
    admin_actor, so in_mode/asked_mode run first: a client that sends
    `Okwan-Mode: test` on every call (as the dashboard does on all but this
    one) cannot sign out once its sandbox is not there (409), and a bad
    value (400) leaves the session open. Signing out must end the session
    it presents, whatever the mode."""
    w = world
    token = await _session(w)
    headers = [("Authorization", f"Bearer {token}")]
    if case == "no sandbox made":
        headers.append(("Okwan-Mode", "test"))
    elif case == "sandbox gone":
        made = await _sandbox(w)
        assert (await w.client.get("/v1/tenants", headers=_as(token, TEST))).status_code == 200
        await w.store.delete_tenant(made["id"])  # the operator's `tenant delete`, or a failed seed
        headers.append(("Okwan-Mode", "test"))
    elif case == "not a mode":
        headers.append(("Okwan-Mode", "sandbox"))
    else:
        await _sandbox(w)
        headers += [("Okwan-Mode", "test"), ("Okwan-Mode", "test")]
    r = await w.client.delete("/v1/sessions/current", headers=headers)
    assert r.status_code == 204, (case, r.status_code, r.text)
    assert not await _alive(w, token), f"{case}: the session outlived its sign-out"


# ── the operator console ───────────────────────────────────────────────

OPS_EMAIL = f"ops-{os.urandom(3).hex()}@okwan.test"
SECRET = totp.new_secret()


class Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_verification(self, email, link):  # pragma: no cover
        raise AssertionError("not here")

    async def send_notice(self, to, subject, text, html=None, reply_to=None):
        self.sent.append({"to": to, "subject": subject, "text": text})


@pytest.fixture
async def console(world, monkeypatch):  # noqa: F811
    """An unlocked operator whose own account has a sandbox, and a live
    customer's ticket waiting."""
    from okwan_api import mail

    w = world
    monkeypatch.setenv("OKWAN_OPERATOR_EMAILS", OPS_EMAIL)
    monkeypatch.setenv("OKWAN_SUPPORT_ESCALATION_TO", "owner-inbox@okwan.test")
    monkeypatch.setattr(ops, "_replay", totp.ReplayGuard())
    box = Outbox()
    mail.set_mailer(box)
    pw = accounts.hash_password("correct horse battery")
    hq = await w.store.create_tenant("Okwan HQ")
    await w.store.add_account(hq.id, OPS_EMAIL, pw)
    await w.store.put_operator_factor(OPS_EMAIL, SECRET)
    contact = f"founder-{os.urandom(3).hex()}@acme.test"
    await w.store.add_account(w.acme.id, contact, pw)
    ticket = await w.store.add_ticket(w.acme.id, conversation_id=None, severity="sev3", category="bug",
                                      summary="A live customer's question", notified=False)
    token = await _session(w, hq.id)
    assert (await w.client.post("/v1/sandbox", headers=_as(token))).status_code == 201
    code = totp.code_at(SECRET, totp.current_step())
    assert (await w.client.post("/v1/ops/unlock", json={"code": code}, headers=_as(token))).status_code == 200
    try:
        yield SimpleNamespace(w=w, token=token, ticket=ticket, contact=contact, box=box, hq=hq)
    finally:
        mail._overridden = False
        mail._mailer = None
        if isinstance(w.store, PostgresStore):
            await w.store.pool.execute("DELETE FROM tenants WHERE id = $1", hq.id)
            await w.store.pool.execute("DELETE FROM operator_factors WHERE email = $1", OPS_EMAIL)


async def test_test_mode_never_changes_a_live_customers_ticket_from_the_console(console):
    """FINDING. Billing refuses Test mode because Test mode "reaches nothing
    live"; the operator console never reads the header, so from under the
    banner that says nothing reaches a live merchant, a reply is mailed to a
    real customer and their ticket's status changes."""
    c = console
    w = c.w
    r = await w.client.post(f"/v1/ops/tickets/{c.ticket.id}/reply", headers=_as(c.token, TEST),
                            json={"body": "Testing the reply box, please ignore", "resolve": True})
    assert r.status_code in (400, 403, 409), (r.status_code, r.text)
    assert not [m for m in c.box.sent if m["to"] == c.contact], "a live customer was mailed from Test mode"
    r = await w.client.post(f"/v1/ops/tickets/{c.ticket.id}/status", headers=_as(c.token, TEST),
                            json={"status": "resolved"})
    assert r.status_code in (400, 403, 409), (r.status_code, r.text)
    assert (await w.store.get_ticket(c.ticket.id)).status == "open"


async def test_a_test_key_never_learns_the_live_accounts_sign_in_address(world):  # noqa: F811
    """FINDING (the sandbox, which Test mode is). okwan_api/sandbox.py: "nothing
    of the account's name, for a self-serve account its sign-in address,
    reaches a test key". GET /v1/tenants/{sandbox}/notify answers with
    `email`, store.account_email of the sandbox, which walks `sandbox_of` up
    to the live account's login: any test admin key (one handed to a
    contractor or a CI job) reads the account owner's address."""
    w = world
    owner = f"owner-{os.urandom(3).hex()}@acme.test"
    await w.store.add_account(w.acme.id, owner, accounts.hash_password("a long first password"))
    made = await _sandbox(w)
    test_admin = (await _test_key(w, ADMIN))["secret"]
    r = await w.client.get(f"/v1/tenants/{made['id']}/notify", headers=_auth(test_admin))
    assert owner not in r.text, "a test key read the live account's sign-in address"


# ═══ Held: these pass, and stay as regression tests ═════════════════════


@pytest.mark.parametrize("mode", ["test", " TEST ", "Test", "\ttest", "test\x0b", "\xa0test", "\x1ctest\x85"])
async def test_held_billing_refuses_test_mode_however_it_is_written(world, mode):  # noqa: F811
    """HELD. Billing reads the mode with the same parser as admin_actor, so no
    spelling of `test` the admin routes honour gets past billing. (asked_mode
    strips with str.strip(), wider than HTTP's spaces and tabs: a vertical
    tab, a no-break space or a separator character around `test` is read as
    `test`. Every reader of the header agrees, so it moves nothing, but it is
    looser than versions.asked, which strips only " \t".)"""
    w = world
    token = await _session(w)
    sb = await _sandbox(w)
    headers = [("Authorization", f"Bearer {token}"), ("Okwan-Mode", mode.encode("latin-1"))]
    # The same spelling does move the session on an admin route...
    tree = (await w.client.get("/v1/tenants", headers=headers)).json()
    assert tree["self"]["id"] == sb["id"]
    # ...and is refused at billing, on every route of it.
    for method, path in (("get", "/v1/billing"), ("post", "/v1/billing/checkout"), ("post", "/v1/billing/portal")):
        r = await w.client.request(method.upper(), path, headers=headers)
        assert r.status_code == 403, (mode, path, r.status_code)


async def test_held_billing_refuses_test_mode_before_any_sandbox_exists_and_twice_sent(world):  # noqa: F811
    """HELD. Billing does not need the sandbox to refuse; the header twice is
    a 400, and a session in X-Okwan-Key is not the dashboard."""
    w = world
    token = await _session(w)
    assert (await w.client.get("/v1/billing", headers=_as(token, TEST))).status_code == 403
    twice = [("Authorization", f"Bearer {token}"), ("Okwan-Mode", "live"), ("Okwan-Mode", "test")]
    assert (await w.client.get("/v1/billing", headers=twice)).status_code == 400
    assert (await w.client.get("/v1/billing", headers={"X-Okwan-Key": token})).status_code == 403


async def test_held_a_session_in_test_mode_reaches_no_data_route_and_no_mcp_tool(world):  # noqa: F811
    """HELD. Test mode moves a session on admin routes only: data routes and
    the hosted MCP still take a key and nothing else."""
    w = world
    token = await _session(w)
    await _sandbox(w)
    for method, path, body in (("GET", "/v1/exceptions", None), ("POST", "/v1/query", {"sql": "SELECT 1"}),
                               ("GET", "/v1/reconciliations", None), ("GET", "/v1/events", None)):
        r = await w.client.request(method, path, headers=_as(token, TEST), json=body)
        assert r.status_code == 401, (path, r.status_code)
    out = await _tool("okwan_open_items")(ctx=_ctx(token, **{"okwan-mode": "test"}))
    assert "error" in out and out["data"] == []


async def test_held_in_test_mode_nothing_live_is_changed_by_id(world):  # noqa: F811
    """HELD. A session in Test mode naming live things, on the admin routes
    that change something: every one is the 404 of a tenant outside the
    subtree, and nothing live moves."""
    w = world
    token = await _session(w)
    await _sandbox(w)
    live_key_full, live_key = await w.store.issue_key(w.shop.id, scope=READ)
    endpoint = await w.store.add_webhook_endpoint(
        w.acme.id, url="https://hooks.acme.test/okwan", secret="whsec_x", created_by="session",
        at=datetime.now(UTC), api_version=w.acme.api_version)
    t = _as(token, TEST)
    tries = [
        ("DELETE", f"/v1/tenants/keys/{live_key.id}", None),
        ("POST", f"/v1/tenants/{w.shop.id}/keys", {"scope": "admin"}),
        ("PUT", f"/v1/tenants/{w.shop.id}/credentials",
         {"connector": "stripe", "field": "secret_key", "value": "sk_test_planted"}),
        ("PUT", f"/v1/tenants/{w.acme.id}/notify", {"daily_checks": False}),
        ("POST", f"/v1/tenants/{w.shop.id}/connectors/gusto/authorize", None),
        ("POST", f"/v1/tenants/{w.shop.id}/reconciliations/batches_deposits", None),
        ("DELETE", f"/v1/webhooks/{endpoint.id}", None),
        ("POST", f"/v1/webhooks/{endpoint.id}/enable", None),
        ("POST", f"/v1/webhooks/{endpoint.id}/test", None),
    ]
    for method, path, body in tries:
        r = await w.client.request(method, path, headers=t, json=body)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
    # Nothing live moved.
    assert (await w.client.get("/v1/exceptions", headers=_auth(live_key_full))).status_code == 200
    assert [e.id for e in await w.store.webhook_endpoints([w.acme.id])] == [endpoint.id]
    assert (await w.client.get("/v1/webhooks", headers=t)).json()["data"] == []
    assert (await w.store.get_notify(w.acme.id))["daily_checks"] is True
    assert await w.store.connectors_configured(w.shop.id) == {}


async def test_held_out_of_test_mode_nothing_in_the_sandbox_is_changed_by_id(world):  # noqa: F811
    """HELD. The live session naming sandbox things on the general admin
    routes: 404. Its keys are managed only through /v1/sandbox/keys."""
    w = world
    token = await _session(w)
    sb = await _sandbox(w)
    test_key = await _test_key(w, READ)
    for method, path, body in (("DELETE", f"/v1/tenants/keys/{test_key['key_id']}", None),
                               ("POST", f"/v1/tenants/{sb['id']}/keys", None),
                               ("PUT", f"/v1/tenants/{sb['id']}/notify", {"finding_emails": False})):
        for mode in (None, LIVE):
            r = await w.client.request(method, path, headers=_as(token, mode), json=body)
            assert r.status_code == 404, (method, path, mode, r.status_code)
    assert (await w.client.get("/v1/exceptions", headers=_auth(test_key["secret"]))).status_code == 200


async def test_held_the_sandbox_is_not_managed_or_given_a_sandbox_from_inside(world):  # noqa: F811
    """HELD. In Test mode the session is the sandbox: it cannot make a
    sandbox (of the sandbox), nor issue, list or revoke through
    /v1/sandbox/keys; a merchant's session is given no sandbox."""
    w = world
    token = await _session(w)
    await _sandbox(w)
    issued = await _test_key(w, READ)
    for method, path in (("POST", "/v1/sandbox"), ("GET", "/v1/sandbox"), ("POST", "/v1/sandbox/keys"),
                         ("GET", "/v1/sandbox/keys"), ("DELETE", f"/v1/sandbox/keys/{issued['key_id']}")):
        r = await w.client.request(method, path, headers=_as(token, TEST))
        assert r.status_code == 403, (method, path, r.status_code)
    assert (await w.client.get("/v1/exceptions", headers=_auth(issued["secret"]))).status_code == 200
    merchant = await _session(w, w.shop.id)
    assert (await w.client.post("/v1/sandbox", headers=_as(merchant))).status_code == 403
    assert (await w.client.get("/v1/tenants", headers=_as(merchant, TEST))).status_code == 400


async def test_held_test_mode_ends_with_the_idle_timeout(world):  # noqa: F811
    """HELD. Thirty minutes without use end the session, and Test mode with it."""
    w = world
    await _sandbox(w)
    full, token_hash = new_token(SESSION_PREFIX)
    await w.store.create_session(w.acme.id, token_hash, accounts.expires(accounts.SESSION_TTL))
    assert (await w.client.get("/v1/tenants", headers=_as(full, TEST))).status_code == 200
    when = datetime.now(UTC) - accounts.SESSION_IDLE - timedelta(minutes=1)
    if isinstance(w.store, PostgresStore):
        await w.store.pool.execute("UPDATE sessions SET last_seen_at = $2 WHERE token_hash = $1", token_hash, when)
    else:
        w.store._session_seen[token_hash] = when
    for mode in (TEST, LIVE, None):
        assert (await w.client.get("/v1/tenants", headers=_as(full, mode))).status_code == 401


async def test_held_a_merchant_test_key_cannot_be_moved_live_by_the_header(world):  # noqa: F811
    """HELD. A test key of a merchant inside the sandbox, sent with `live`, is
    refused like the sandbox's own."""
    w = world
    await _sandbox(w)
    key = (await _test_key(w, ADMIN))["secret"]
    m = (await w.client.post("/v1/tenants", headers=_auth(key), json={"name": "Sandbox Café"})).json()
    deep = (await w.client.post(f"/v1/tenants/{m['id']}/keys", headers=_auth(key), json={"scope": "admin"})).json()
    for path in ("/v1/tenants", "/v1/exceptions"):
        r = await w.client.get(path, headers=_as(deep["secret"], LIVE))
        assert r.status_code == 400, (path, r.status_code)


@pytest.mark.parametrize("value", [b"test, test", "t\u0435st".encode(), b"test;", b"TEST LIVE", b"tes t"])
async def test_held_odd_spellings_of_the_mode_are_refused_on_both_kinds_of_credential(world, value):  # noqa: F811
    """HELD. A comma-joined repeat (what a proxy makes of two), a Cyrillic
    `е` in UTF-8, trailing punctuation and two words are refused; nothing
    falls back to live."""
    w = world
    token = await _session(w)
    await _sandbox(w)
    for credential in (token, w.admin):
        r = await w.client.get("/v1/tenants", headers=[("Authorization", f"Bearer {credential}"),
                                                       ("Okwan-Mode", value)])
        assert r.status_code == 400, (value, r.status_code)


async def test_held_okwan_mode_on_routes_that_take_no_key_does_nothing(world):  # noqa: F811
    """HELD. Sign-in and the catalog ignore the header: a session made with
    `Okwan-Mode: test` is an ordinary session of the live account, and the
    catalog is the same either way."""
    w = world
    email = f"owner-{os.urandom(3).hex()}@acme.test"
    await w.store.add_account(w.acme.id, email, accounts.hash_password("a long first password"))
    await _sandbox(w)
    r = await w.client.post("/v1/sessions", headers=TEST, json={"email": email, "password": "a long first password"})
    assert r.status_code == 201
    made = r.json()["session"]
    assert (await w.client.get("/v1/tenants", headers=_as(made))).json()["self"]["id"] == w.acme.id
    plain = (await w.client.get("/v1/connectors")).json()
    for headers in (TEST, _as(w.read, TEST), _as(made, TEST)):
        assert (await w.client.get("/v1/connectors", headers=headers)).json() == plain


async def test_held_test_mode_reads_are_logged_on_the_sandbox_not_the_live_account(world):  # noqa: F811
    """HELD. The access log: a check run in Test mode is the sandbox's entry,
    by "session"; the live account's log does not show it."""
    w = world
    token = await _session(w)
    sb = await _sandbox(w)
    path = f"/v1/tenants/{sb['id']}/reconciliations/batches_deposits"
    assert (await w.client.post(path, headers=_as(token, TEST))).status_code == 200
    mine = (await w.client.get(f"/v1/tenants/{sb['id']}/access", headers=_as(token, TEST))).json()["entries"]
    assert [e for e in mine if e["target"] == path and e["credential"] == "session"]
    live = (await w.client.get(f"/v1/tenants/{w.acme.id}/access", headers=_as(token))).json()["entries"]
    assert not [e for e in live if sb["id"] in (e.get("target") or "") or e.get("tenant_id") == sb["id"]]


async def test_held_an_idempotency_key_used_in_test_mode_is_not_answered_live(world):  # noqa: F811
    """HELD. A decision made in Test mode under an Idempotency-Key is kept on
    the sandbox's item: the same key and body sent live, at the sandbox's
    item or the live account, is not replayed."""
    w = world
    token = await _session(w)
    sb = await _sandbox(w)
    assert (await w.client.post(f"/v1/tenants/{sb['id']}/reconciliations/batches_deposits",
                                headers=_as(token, TEST))).status_code == 200
    listed = (await w.client.get(f"/v1/tenants/{sb['id']}/exceptions?merchant={sb['id']}",
                                 headers=_as(token, TEST))).json()["data"]
    item = listed[0]
    decide = f"/v1/tenants/{{t}}/exceptions/pair/batches_deposits/{item['key']}/decisions"
    body = {"status": "explained", "note": "a sample week, nothing to fix"}
    k = {"Idempotency-Key": "attack-k1"}
    made = await w.client.post(decide.format(t=sb["id"]), headers={**_as(token, TEST), **k}, json=body)
    assert made.status_code == 201, made.text
    for target in (sb["id"], w.acme.id):
        r = await w.client.post(decide.format(t=target), headers={**_as(token), **k}, json=body)
        assert r.status_code == 404, (target, r.status_code, r.text)


async def test_held_the_console_in_test_mode_still_needs_its_second_factor(console):
    """HELD. Test mode does not stand in for the console's unlock: locked,
    the console wants the authenticator code, and in Test mode it is refused
    whether locked or not (the fix to the finding above)."""
    c = console
    w = c.w
    assert (await w.client.post("/v1/ops/lock", headers=_as(c.token))).status_code == 200
    r = await w.client.get("/v1/ops/tickets", headers=_as(c.token))
    assert r.status_code == 403 and "authenticator" in r.json()["detail"]
    r = await w.client.get("/v1/ops/tickets", headers=_as(c.token, TEST))
    assert r.status_code == 403 and "Test mode" in r.json()["detail"]
    # A customer's session in Test mode meets the console's plain 404.
    customer = await _session(w)
    await _sandbox(w)
    assert (await w.client.get("/v1/ops/me", headers=_as(customer, TEST))).status_code == 404
