"""Verification mail through Resend, and what signup does when it fails.

The link carries a token that creates an account, so these tests hold the
mailer to four rules: the message carries the link, its expiry and an
ignore line, and nothing else that could switch tracking on; a send
failure never changes what signup answers; the token and the link never
reach a log; and outside dev, missing configuration means 503, never a
fallback to logging links.

A fake transport stands in for Resend (httpx.MockTransport, so no new
mocking dependency), the pattern tests/test_paypal.py set.
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from okwan_api import mail
from okwan_api.mail import (
    RESEND_ENDPOINT,
    DevLogMailer,
    MailNotSent,
    ResendMailer,
    deliver_verification,
    get_mailer,
    verification_message,
)
from okwan_vault import EnvMasterKey, MemoryStore, accounts, new_key

KEY = "re_test_key_never_logged_123"
SENDER = "Okwan <verify@okwan.test>"
LINK = "https://dash.okwan.test/verify?token=okv_secret_token_abc"
PASSWORD = "correct horse battery"


class _Resend:
    """A fake Resend: records each request and answers as configured."""

    def __init__(self, status: int = 200, body: dict | None = None, raises=None):
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = body if body is not None else {"id": "4ef9a417-02e9-4d39-ad75"}
        self.raises = raises

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        return httpx.Response(self.status, json=self.body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def payload(self, i: int = -1) -> dict:
        return json.loads(self.requests[i].content)


def _mailer(fake: _Resend) -> ResendMailer:
    return ResendMailer(KEY, SENDER, transport=fake.transport)


@pytest.fixture(autouse=True)
def _real_mailer_choice():
    """Each test chooses its mailer; none leaks into the next."""
    mail._overridden = False
    yield
    mail._overridden = False


# ── the message ─────────────────────────────────────────────────────

def test_message_carries_the_link_its_expiry_and_an_ignore_line():
    subject, text, body = verification_message(LINK)
    assert "Okwan" in subject
    for part in (text, body):
        assert LINK in part
        assert "expires in 24 hours" in part
        assert "If you didn't sign up for Okwan, you can ignore this email." in part


def test_the_expiry_is_read_from_the_signup_ttl(monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr(accounts, "SIGNUP_TTL", timedelta(hours=48))
    assert "expires in 48 hours" in verification_message(LINK)[1]


def test_the_html_link_is_escaped():
    link = 'https://dash.okwan.test/verify?token=a&x="><script>'
    _, _, body = verification_message(link)
    assert "<script>" not in body
    assert 'href="https://dash.okwan.test/verify?token=a&amp;x=&quot;&gt;&lt;script&gt;"' in body


# ── sending ─────────────────────────────────────────────────────────

async def test_a_send_is_one_post_to_resend_with_the_key():
    fake = _Resend()
    await _mailer(fake).send_verification("founder@acme.test", LINK)
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.method == "POST"
    assert str(request.url) == RESEND_ENDPOINT
    assert request.headers["authorization"] == f"Bearer {KEY}"
    sent = fake.payload()
    assert sent["from"] == SENDER
    assert sent["to"] == ["founder@acme.test"]
    assert LINK in sent["text"] and LINK in sent["html"]


async def test_the_message_carries_nothing_that_could_turn_tracking_on():
    """Resend has no per-message tracking switch; tracking is a domain
    setting, turned off at domain setup (§10 item 15). The send itself
    carries exactly these fields and no headers, tags or options."""
    fake = _Resend()
    await _mailer(fake).send_verification("founder@acme.test", LINK)
    assert set(fake.payload()) == {"from", "to", "subject", "text", "html"}


async def test_a_provider_error_raises_without_the_link_token_or_address():
    fake = _Resend(422, {
        "statusCode": 422,
        "name": "validation_error",
        "message": f"Invalid `to` field: founder@acme.test, link {LINK}",
    })
    with pytest.raises(MailNotSent) as e:
        await _mailer(fake).send_verification("founder@acme.test", LINK)
    assert str(e.value) == "resend answered 422 validation_error"


async def test_an_unexpected_error_name_is_not_repeated():
    fake = _Resend(500, {"name": f"oops {LINK}", "message": "boom"})
    with pytest.raises(MailNotSent, match="^resend answered 500 unknown$"):
        await _mailer(fake).send_verification("founder@acme.test", LINK)


async def test_an_unreachable_provider_raises_without_the_link():
    fake = _Resend(raises=httpx.ConnectError(f"cannot reach, sending {LINK}"))
    with pytest.raises(MailNotSent) as e:
        await _mailer(fake).send_verification("founder@acme.test", LINK)
    assert str(e.value) == "resend unreachable: ConnectError"


def test_the_key_is_not_in_the_mailers_repr():
    assert KEY not in repr(ResendMailer(KEY, SENDER))


async def test_a_failed_delivery_is_logged_without_the_link(caplog):
    caplog.set_level(logging.DEBUG)
    fake = _Resend(422, {"name": "validation_error", "message": LINK})
    await deliver_verification(_mailer(fake), "founder@acme.test", LINK)
    assert "verification mail not sent: resend answered 422 validation_error" in caplog.text
    assert "okv_secret_token_abc" not in caplog.text
    assert "founder@acme.test" not in caplog.text


async def test_any_other_mailer_failure_is_named_by_type_only(caplog):
    class Broken:
        async def send_verification(self, email, link):
            raise RuntimeError(f"failed on {link}")

    caplog.set_level(logging.DEBUG)
    await deliver_verification(Broken(), "founder@acme.test", LINK)
    assert "verification mail not sent: RuntimeError" in caplog.text
    assert "okv_secret_token_abc" not in caplog.text


# ── configuration ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("env", "key", "sender", "expected"),
    [
        ("production", "", "", None),
        ("production", KEY, "", None),
        ("production", "", SENDER, None),
        ("production", KEY, SENDER, ResendMailer),
        ("dev", "", "", DevLogMailer),
        ("dev", KEY, "", DevLogMailer),
        ("dev", KEY, SENDER, ResendMailer),
    ],
)
def test_the_mailer_follows_the_configuration(monkeypatch, env, key, sender, expected):
    monkeypatch.setenv("OKWAN_ENV", env)
    monkeypatch.setenv("RESEND_API_KEY", key)
    monkeypatch.setenv("OKWAN_MAIL_FROM", sender)
    chosen = get_mailer()
    if expected is None:
        assert chosen is None
    else:
        assert isinstance(chosen, expected)


def test_whitespace_is_not_configuration(monkeypatch):
    monkeypatch.setenv("OKWAN_ENV", "production")
    monkeypatch.setenv("RESEND_API_KEY", "   ")
    monkeypatch.setenv("OKWAN_MAIL_FROM", SENDER)
    assert get_mailer() is None


# ── through signup ──────────────────────────────────────────────────

@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
def client(store):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    set_store(store)
    return TestClient(app)


async def _registered(store, email: str) -> None:
    tenant = await store.create_tenant(email)
    await store.add_account(tenant.id, email, accounts.hash_password(PASSWORD))


def test_signup_is_503_in_production_without_a_provider(client, monkeypatch):
    monkeypatch.setenv("OKWAN_ENV", "production")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("OKWAN_MAIL_FROM", SENDER)
    r = client.post("/v1/signup", json={"email": "a@acme.test", "password": PASSWORD})
    assert r.status_code == 503


def test_signup_sends_through_resend_when_configured(client, store, monkeypatch):
    fake = _Resend()
    mail.set_mailer(_mailer(fake))
    monkeypatch.setenv("OKWAN_DASHBOARD_URL", "https://dash.okwan.test")
    r = client.post("/v1/signup", json={"email": "new@acme.test", "password": PASSWORD})
    assert r.status_code == 202
    sent = fake.payload()
    assert sent["to"] == ["new@acme.test"]
    assert "https://dash.okwan.test/verify?token=okv_" in sent["text"]


async def test_a_failed_send_answers_exactly_like_a_registered_address(
    client, store, caplog
):
    caplog.set_level(logging.DEBUG)
    await _registered(store, "taken@acme.test")
    fake = _Resend(500, {"name": "internal_server_error", "message": "down"})
    mail.set_mailer(_mailer(fake))

    taken = client.post("/v1/signup", json={"email": "taken@acme.test", "password": PASSWORD})
    failed = client.post("/v1/signup", json={"email": "new@acme.test", "password": PASSWORD})

    assert len(fake.requests) == 1  # only the new address was sent anything
    assert (failed.status_code, failed.json()) == (taken.status_code, taken.json())
    assert failed.status_code == 202
    token = fake.payload()["text"].split("token=", 1)[1].split()[0]
    assert "verification mail not sent: resend answered 500 internal_server_error" in caplog.text
    assert token not in caplog.text
    assert "/verify?token=" not in caplog.text


def test_a_successful_signup_logs_neither_token_nor_link(client, caplog):
    caplog.set_level(logging.DEBUG)
    fake = _Resend()
    mail.set_mailer(_mailer(fake))
    client.post("/v1/signup", json={"email": "new@acme.test", "password": PASSWORD})
    token = fake.payload()["text"].split("token=", 1)[1].split()[0]
    assert token not in caplog.text
    assert "/verify?token=" not in caplog.text
