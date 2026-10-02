"""Self-serve signup, dashboard sessions and the credential test.

Signup is the first route that mints a root tenant from the open
internet, so most of these tests are about what it must refuse: an
unverified address, a second tenant for one address, a password that
did not create the signup, and any answer that tells a stranger whether
an address is registered. The credential test is held to the rule the
vault already keeps: a stored value never comes back out.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from okwan_core import UpstreamError, get
from okwan_core.connector import ConnectorContext
from okwan_vault import EnvMasterKey, MemoryStore, accounts, new_key

PASSWORD = "correct horse battery"
SECRET = "sk_test_never_comes_back_1234"


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_verification(self, email: str, link: str) -> None:
        self.sent.append((email, link))

    def token(self) -> str:
        return self.sent[-1][1].split("token=", 1)[1]


@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
def outbox():
    from okwan_api.mail import set_mailer

    box = _Outbox()
    set_mailer(box)
    yield box
    from okwan_api import mail

    mail._overridden = False


@pytest.fixture
def client(store, outbox):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    set_store(store)
    return TestClient(app)


def _signup(client, outbox, email="founder@acme.test", password=PASSWORD) -> str:
    r = client.post("/v1/signup", json={"email": email, "password": password})
    assert r.status_code == 202
    return outbox.token()


def _verified(client, outbox, email="founder@acme.test") -> dict[str, Any]:
    token = _signup(client, outbox, email)
    r = client.post("/v1/signup/verify", json={"token": token, "password": PASSWORD})
    assert r.status_code == 201
    return r.json()


def _auth(session: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {session}"}


# ── accounts primitives ─────────────────────────────────────────────

def test_password_round_trips_and_rejects_others():
    stored = accounts.hash_password(PASSWORD)
    assert accounts.check_password(PASSWORD, stored)
    assert not accounts.check_password("wrong horse battery", stored)


def test_unknown_account_check_is_false_not_an_error():
    assert accounts.check_password(PASSWORD, None) is False


def test_email_is_one_spelling_per_address():
    assert accounts.normalize_email("  Founder@ACME.test ") == "founder@acme.test"


# ── signup ──────────────────────────────────────────────────────────

async def test_no_tenant_exists_until_the_email_is_verified(client, outbox, store):
    _signup(client, outbox)
    assert store._tenants == {}


async def test_verification_creates_one_root_tenant_and_a_session(client, outbox, store):
    body = _verified(client, outbox)
    tenant = await store.get_tenant(body["tenant"]["id"])
    assert tenant is not None and tenant.is_root
    assert body["session"].startswith("oks_")


def test_a_link_works_once(client, outbox):
    token = _signup(client, outbox)
    first = client.post("/v1/signup/verify", json={"token": token, "password": PASSWORD})
    again = client.post("/v1/signup/verify", json={"token": token, "password": PASSWORD})
    assert first.status_code == 201
    assert again.status_code == 400


async def test_one_tenant_per_address(client, outbox, store):
    """Two pending signups for one address yield one tenant, not two."""
    t1 = _signup(client, outbox)
    t2 = _signup(client, outbox)
    assert client.post("/v1/signup/verify",
                       json={"token": t1, "password": PASSWORD}).status_code == 201
    assert client.post("/v1/signup/verify",
                       json={"token": t2, "password": PASSWORD}).status_code == 400
    assert len(store._tenants) == 1


def test_signup_does_not_reveal_a_registered_address(client, outbox):
    _verified(client, outbox)
    sent = len(outbox.sent)
    fresh = client.post("/v1/signup",
                        json={"email": "new@acme.test", "password": PASSWORD})
    taken = client.post("/v1/signup",
                        json={"email": "FOUNDER@acme.test", "password": PASSWORD})
    assert fresh.status_code == taken.status_code == 202
    assert fresh.json() == taken.json()
    assert len(outbox.sent) == sent + 1  # only the new address got mail


def test_a_link_needs_the_password_that_created_it(client, outbox, store):
    """Pre-registration takeover: someone signs up the victim's address
    with their own password. The victim clicking that link with their
    password must not complete it, and the token must survive the miss."""
    token = _signup(client, outbox, password="attacker chose this")
    r = client.post("/v1/signup/verify", json={"token": token, "password": PASSWORD})
    assert r.status_code == 400
    assert store._tenants == {}
    ok = client.post("/v1/signup/verify",
                     json={"token": token, "password": "attacker chose this"})
    assert ok.status_code == 201


async def test_an_expired_link_creates_nothing(client, outbox, store):
    token = _signup(client, outbox)
    h = accounts.hash_token(token)
    email, pw, _ = store._signups[h]
    store._signups[h] = (email, pw, datetime.now(UTC) - timedelta(seconds=1))
    r = client.post("/v1/signup/verify", json={"token": token, "password": PASSWORD})
    assert r.status_code == 400
    assert store._tenants == {}


def test_short_passwords_are_refused(client):
    r = client.post("/v1/signup", json={"email": "a@b.test", "password": "short"})
    assert r.status_code == 422


def test_signup_fails_closed_without_a_mailer(store):
    """No mail provider is configured in production. Signup must refuse
    rather than mint accounts on addresses nobody has proven."""
    from okwan_api import mail
    from okwan_api.auth import set_store
    from okwan_api.mail import set_mailer
    from okwan_api.main import app

    set_store(store)
    set_mailer(None)
    try:
        r = TestClient(app).post("/v1/signup",
                                 json={"email": "a@b.test", "password": PASSWORD})
    finally:
        mail._overridden = False
    assert r.status_code == 503
    assert store._signups == {}


# ── sessions ────────────────────────────────────────────────────────

def test_sign_in_fails_the_same_way_for_unknown_and_wrong(client, outbox):
    _verified(client, outbox)
    wrong = client.post("/v1/sessions",
                        json={"email": "founder@acme.test", "password": "nope nope nope"})
    unknown = client.post("/v1/sessions",
                          json={"email": "ghost@acme.test", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_unverified_address_cannot_sign_in(client, outbox):
    _signup(client, outbox)
    r = client.post("/v1/sessions",
                    json={"email": "founder@acme.test", "password": PASSWORD})
    assert r.status_code == 401


def test_session_administers_but_cannot_read_a_rail(client, outbox):
    """A session provisions its tenant. Reading data still takes a key."""
    body = _verified(client, outbox)
    assert client.get("/v1/tenants", headers=_auth(body["session"])).status_code == 200
    r = client.post("/v1/stripe/charges/list", json={"limit": 1},
                    headers=_auth(body["session"]))
    assert r.status_code == 401


def test_sign_out_ends_the_session(client, outbox):
    session = _verified(client, outbox)["session"]
    assert client.delete("/v1/sessions/current",
                         headers=_auth(session)).status_code == 204
    assert client.get("/v1/tenants", headers=_auth(session)).status_code == 401


# ── the 404 boundary ────────────────────────────────────────────────

async def test_another_accounts_tenant_is_404_on_every_route(client, outbox):
    mine = _verified(client, outbox)
    theirs = _verified(client, outbox, email="rival@other.test")
    tid, headers = theirs["tenant"]["id"], _auth(mine["session"])

    assert client.post(f"/v1/tenants/{tid}/keys", headers=headers).status_code == 404
    assert client.get(f"/v1/tenants/{tid}/credentials",
                      headers=headers).status_code == 404
    assert client.put(f"/v1/tenants/{tid}/credentials", headers=headers,
                      json={"connector": "stripe", "field": "secret_key",
                            "value": SECRET}).status_code == 404
    assert client.post(f"/v1/tenants/{tid}/connectors/stripe/test",
                       headers=headers).status_code == 404


async def test_revoking_someone_elses_key_is_404_and_leaves_it_working(
    client, outbox, store
):
    mine = _verified(client, outbox)
    theirs = _verified(client, outbox, email="rival@other.test")
    full, record = await store.issue_key(theirs["tenant"]["id"])

    r = client.delete(f"/v1/tenants/keys/{record.id}", headers=_auth(mine["session"]))
    assert r.status_code == 404
    ghost = client.delete("/v1/tenants/keys/key_doesnotexist",
                          headers=_auth(mine["session"]))
    assert r.json()["detail"].replace(record.id, "ID") == \
        ghost.json()["detail"].replace("key_doesnotexist", "ID")
    assert await store.tenant_for_key(full) is not None


async def test_revoking_your_own_key_still_works(client, outbox, store):
    mine = _verified(client, outbox)
    full, record = await store.issue_key(mine["tenant"]["id"])
    r = client.delete(f"/v1/tenants/keys/{record.id}", headers=_auth(mine["session"]))
    assert r.status_code == 204
    assert await store.tenant_for_key(full) is None


# ── credentials never come back out ─────────────────────────────────

def test_a_malformed_credential_body_does_not_echo_the_value(client, outbox):
    body = _verified(client, outbox)
    r = client.put(f"/v1/tenants/{body['tenant']['id']}/credentials",
                   headers=_auth(body["session"]),
                   json={"connector": "stripe", "value": SECRET})  # no field
    assert r.status_code == 422
    assert SECRET not in r.text


def test_a_rejected_signup_does_not_echo_the_password(client):
    r = client.post("/v1/signup", json={"email": "not-an-email", "password": PASSWORD})
    assert r.status_code == 422
    assert PASSWORD not in r.text


# ── probe derivation ────────────────────────────────────────────────

@pytest.mark.parametrize(("connector", "expected"), [
    ("stripe", "customers.list"),
    ("paystack", "transactions.list"),
    ("paypal", "transactions.list"),
    ("shopify", "orders.list"),
    ("postgres", "tables.list"),
])
def test_probe_is_the_first_list_runnable_blind(connector, expected):
    resource, op, params = get(connector).probe()
    assert f"{resource.name}.{op.name}" == expected
    if "limit" in type(params).model_fields:
        assert params.limit == 1


def test_a_connector_whose_lists_all_need_arguments_has_no_probe():
    """WhatsApp's only list needs a waba_id; there is no blind read."""
    assert get("whatsapp").probe() is None


def test_connector_listing_states_fields_and_probe(client):
    listed = {c["name"]: c for c in client.get("/v1/connectors").json()}
    assert listed["shopify"]["credential_fields"] == ["access_token", "shop_domain"]
    assert listed["stripe"]["probe"] == "stripe.customers.list"
    assert listed["whatsapp"]["probe"] is None


# ── the credential test ─────────────────────────────────────────────

class _FakeClient:
    def __init__(self, reply) -> None:
        self.reply, self.calls = reply, []

    async def get(self, path: str, params: dict[str, Any] | None = None):
        self.calls.append((path, params))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_stripe(monkeypatch):
    stripe = get("stripe")
    holder: dict[str, _FakeClient] = {}

    def install(reply) -> _FakeClient:
        fake = _FakeClient(reply)
        holder["seen"] = {}

        def factory(_, creds):
            holder["seen"] = dict(creds)
            return ConnectorContext(client=fake, credentials=creds)

        monkeypatch.setattr(stripe, "context_factory", factory)
        return fake

    install.seen = holder
    return install


def _stored(client, outbox) -> tuple[str, dict[str, str]]:
    body = _verified(client, outbox)
    tid, headers = body["tenant"]["id"], _auth(body["session"])
    r = client.put(f"/v1/tenants/{tid}/credentials", headers=headers,
                   json={"connector": "stripe", "field": "secret_key", "value": SECRET})
    assert r.status_code == 204
    return tid, headers


def test_rows_back_is_proof(client, outbox, fake_stripe):
    fake = fake_stripe({"data": [{"id": "cus_1", "created": 1}], "has_more": True})
    tid, headers = _stored(client, outbox)
    r = client.post(f"/v1/tenants/{tid}/connectors/stripe/test", headers=headers)
    assert r.json()["status"] == "rows"
    assert r.json()["rows"] == 1
    assert fake.calls == [("/customers", {"limit": 1})]
    assert SECRET not in r.text


def test_the_test_reads_from_the_vault(client, outbox, fake_stripe):
    """The credential under test is the stored one, not anything sent."""
    fake_stripe({"data": [], "has_more": False})
    tid, headers = _stored(client, outbox)
    client.post(f"/v1/tenants/{tid}/connectors/stripe/test", headers=headers)
    assert fake_stripe.seen["seen"] == {"secret_key": SECRET}


def test_working_credentials_over_an_empty_list_say_so(client, outbox, fake_stripe):
    fake_stripe({"data": [], "has_more": False})
    tid, headers = _stored(client, outbox)
    r = client.post(f"/v1/tenants/{tid}/connectors/stripe/test", headers=headers)
    assert r.json()["status"] == "empty"


def test_an_upstream_error_that_quotes_the_key_is_scrubbed(client, outbox, fake_stripe):
    fake_stripe(UpstreamError(401, f"Invalid API Key provided: {SECRET}"))
    tid, headers = _stored(client, outbox)
    r = client.post(f"/v1/tenants/{tid}/connectors/stripe/test", headers=headers)
    assert r.json()["status"] == "failed"
    assert r.json()["upstream_status"] == 401
    assert SECRET not in r.text
    assert "[redacted]" in r.json()["detail"]


def test_a_transport_error_is_a_result_not_a_500(client, outbox, fake_stripe):
    fake_stripe(ConnectionError(f"could not reach host with {SECRET}"))
    tid, headers = _stored(client, outbox)
    r = client.post(f"/v1/tenants/{tid}/connectors/stripe/test", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert SECRET not in r.text


def test_nothing_stored_names_the_missing_fields(client, outbox):
    body = _verified(client, outbox)
    r = client.post(f"/v1/tenants/{body['tenant']['id']}/connectors/shopify/test",
                    headers=_auth(body["session"]))
    assert r.json()["status"] == "missing"
    assert r.json()["missing"] == ["access_token", "shop_domain"]


def test_untestable_connector_says_why(client, outbox):
    body = _verified(client, outbox)
    r = client.post(f"/v1/tenants/{body['tenant']['id']}/connectors/whatsapp/test",
                    headers=_auth(body["session"]))
    assert r.json()["status"] == "untestable"


def test_unknown_connector_is_404(client, outbox):
    body = _verified(client, outbox)
    r = client.post(f"/v1/tenants/{body['tenant']['id']}/connectors/nope/test",
                    headers=_auth(body["session"]))
    assert r.status_code == 404


def test_an_empty_windowed_read_says_which_window(client, outbox, monkeypatch):
    """PayPal reads 30 days by default; empty there is not empty overall."""
    from datetime import datetime

    from okwan_core.pagination import CursorPage

    stripe = get("stripe")
    _, op, _ = stripe.probe()

    async def windowed(ctx, p):
        return CursorPage[dict](items=[], span_start=datetime(2026, 9, 2, tzinfo=UTC),
                                span_end=datetime(2026, 10, 2, tzinfo=UTC))

    monkeypatch.setattr(op, "handler", windowed)
    tid, headers = _stored(client, outbox)
    r = client.post(f"/v1/tenants/{tid}/connectors/stripe/test", headers=headers).json()
    assert r["status"] == "empty"
    assert r["span"]["start"].startswith("2026-09-02")
    assert "2026-09-02" in r["detail"] and "2026-10-02" in r["detail"]
