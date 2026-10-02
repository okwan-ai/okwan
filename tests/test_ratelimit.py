"""Rate limits on signup, sign-in, verification and the credential test.

Each limit is per client IP and per subject, so these tests rotate one
while holding the other. They also hold the limits to the rules the
routes already keep: a 429 must not reveal whether an address is
registered, and a tenant outside the caller's subtree is still a 404.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from okwan_api.ratelimit import (
    SIGNIN_ADDRESS,
    SIGNIN_IP,
    SIGNUP_ADDRESS,
    TEST_TENANT,
    Limiter,
    Rule,
)
from okwan_vault import EnvMasterKey, MemoryStore, new_key

PASSWORD = "correct horse battery"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ── the limiter ─────────────────────────────────────────────────────

def test_limit_then_reset_after_the_window():
    clock, rule = _Clock(), Rule("t", 2, 60)
    lim = Limiter(clock)
    assert lim.take((rule, "a")) is None
    assert lim.take((rule, "a")) is None
    assert lim.take((rule, "a")) == pytest.approx(60)
    clock.now += 60
    assert lim.take((rule, "a")) is None


def test_subjects_are_independent():
    lim, rule = Limiter(_Clock()), Rule("t", 1, 60)
    assert lim.take((rule, "a")) is None
    assert lim.take((rule, "b")) is None


def test_a_refused_attempt_spends_nothing():
    """All checks pass or none is counted: a refusal on the address must
    not also eat into the IP's allowance."""
    lim = Limiter(_Clock())
    ip, addr = Rule("ip", 5, 60), Rule("addr", 1, 60)
    assert lim.take((ip, "1.1.1.1"), (addr, "x")) is None
    for _ in range(10):
        assert lim.take((ip, "1.1.1.1"), (addr, "x")) is not None
    assert lim.take((ip, "1.1.1.1"), (addr, "y")) is None  # IP spent only 2


def test_memory_is_bounded(monkeypatch):
    import okwan_api.ratelimit as rl

    monkeypatch.setattr(rl, "MAX_KEYS", 100)
    lim = Limiter(_Clock())
    for i in range(1000):
        lim.take((SIGNIN_IP, f"10.0.{i // 256}.{i % 256}"))
    assert len(lim._hits) <= 100


# ── client IP ───────────────────────────────────────────────────────

@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


@pytest.fixture
def client(store):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    set_store(store)
    return TestClient(app)


@pytest.fixture
async def account(store):
    """A verified account, made directly so the signup limits stay unspent."""
    from okwan_vault import accounts

    _, token_hash = accounts.new_token(accounts.VERIFY_PREFIX)
    await store.add_signup("founder@acme.test", accounts.hash_password(PASSWORD),
                           token_hash, accounts.expires(accounts.SIGNUP_TTL))
    return await store.complete_signup(token_hash)


def _sign_in(client, email, ip, password="wrong wrong wrong", **headers):
    return client.post("/v1/sessions", json={"email": email, "password": password},
                       headers={"X-Forwarded-For": ip, **headers})


def test_rotating_addresses_from_one_ip_is_limited(client):
    for i in range(SIGNIN_IP.limit):
        assert _sign_in(client, f"u{i}@x.test", "9.9.9.9").status_code == 401
    r = _sign_in(client, "fresh@x.test", "9.9.9.9")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_rotating_ips_against_one_address_is_limited(client, account):
    for i in range(SIGNIN_ADDRESS.limit):
        assert _sign_in(client, "founder@acme.test", f"9.9.9.{i}").status_code == 401
    r = _sign_in(client, "FOUNDER@acme.test", "8.8.8.8", password=PASSWORD)
    assert r.status_code == 429  # even with the right password, until the window ends


def test_a_429_says_nothing_about_registration(client, account):
    for i in range(SIGNIN_ADDRESS.limit):
        _sign_in(client, "founder@acme.test", f"9.9.9.{i}")
        _sign_in(client, "ghost@acme.test", f"9.9.8.{i}")
    known = _sign_in(client, "founder@acme.test", "7.7.7.7")
    unknown = _sign_in(client, "ghost@acme.test", "7.7.7.8")
    assert known.status_code == unknown.status_code == 429
    assert known.json() == unknown.json()


def test_forwarded_ip_header_is_ignored_without_the_dashboard_secret(client, monkeypatch):
    monkeypatch.setenv("OKWAN_DASHBOARD_SECRET", "s3cret-shared-by-render")
    for i in range(SIGNIN_IP.limit):
        _sign_in(client, f"u{i}@x.test", "9.9.9.9", **{"X-Okwan-Client-IP": f"1.2.3.{i}"})
    r = _sign_in(client, "z@x.test", "9.9.9.9", **{"X-Okwan-Client-IP": "1.2.3.250"})
    assert r.status_code == 429


def test_the_dashboard_secret_makes_the_forwarded_ip_count(client, monkeypatch):
    """Behind the dashboard every request comes from one address; the
    forwarded browser IP is what gets limited."""
    monkeypatch.setenv("OKWAN_DASHBOARD_SECRET", "s3cret-shared-by-render")
    via = {"X-Okwan-Dashboard-Secret": "s3cret-shared-by-render"}
    for i in range(SIGNIN_IP.limit):
        _sign_in(client, f"u{i}@x.test", "10.0.0.1", **via, **{"X-Okwan-Client-IP": "1.2.3.4"})
    blocked = _sign_in(client, "z@x.test", "10.0.0.1", **via, **{"X-Okwan-Client-IP": "1.2.3.4"})
    other = _sign_in(client, "z@x.test", "10.0.0.1", **via, **{"X-Okwan-Client-IP": "5.6.7.8"})
    assert blocked.status_code == 429
    assert other.status_code == 401


def test_only_the_proxy_appended_hop_is_believed(client):
    """Entries left of the last one were written by the client."""
    for i in range(SIGNIN_IP.limit):
        _sign_in(client, f"u{i}@x.test", f"6.6.6.{i}, 9.9.9.9")
    assert _sign_in(client, "z@x.test", "6.6.6.250, 9.9.9.9").status_code == 429


# ── signup and verification ─────────────────────────────────────────

@pytest.fixture
def outbox():
    from okwan_api import mail

    class Box:
        async def send_verification(self, email, link):
            pass

    mail.set_mailer(Box())
    yield
    mail._overridden = False


def test_signup_is_limited_per_address(client, outbox):
    for i in range(SIGNUP_ADDRESS.limit):
        r = client.post("/v1/signup", json={"email": "a@x.test", "password": PASSWORD},
                        headers={"X-Forwarded-For": f"9.9.9.{i}"})
        assert r.status_code == 202
    r = client.post("/v1/signup", json={"email": "a@x.test", "password": PASSWORD},
                    headers={"X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 429


def test_verify_is_limited_per_ip(client):
    from okwan_api.ratelimit import VERIFY_IP

    for _ in range(VERIFY_IP.limit):
        client.post("/v1/signup/verify", json={"token": "okv_x", "password": "x"},
                    headers={"X-Forwarded-For": "9.9.9.9"})
    r = client.post("/v1/signup/verify", json={"token": "okv_x", "password": "x"},
                    headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 429


# ── the credential test ─────────────────────────────────────────────

async def test_credential_test_is_limited_per_tenant(client, store, account):
    full, _ = await store.issue_key(account.id)
    auth = {"Authorization": f"Bearer {full}"}
    url = f"/v1/tenants/{account.id}/connectors/whatsapp/test"  # untestable: no upstream
    for i in range(TEST_TENANT.limit):
        assert client.post(url, headers={**auth, "X-Forwarded-For": f"9.9.9.{i}"}
                           ).status_code == 200
    assert client.post(url, headers={**auth, "X-Forwarded-For": "8.8.8.8"}
                       ).status_code == 429


async def test_a_foreign_tenant_is_404_even_when_limited(client, store, account):
    rival = await store.create_tenant("Rival")
    full, _ = await store.issue_key(account.id)
    auth = {"Authorization": f"Bearer {full}", "X-Forwarded-For": "9.9.9.9"}
    for _ in range(TEST_TENANT.limit + 5):
        client.post(f"/v1/tenants/{account.id}/connectors/whatsapp/test", headers=auth)
    r = client.post(f"/v1/tenants/{rival.id}/connectors/whatsapp/test", headers=auth)
    assert r.status_code == 404


# ── forwarded-IP diagnostic (temporary) ─────────────────────────────

@pytest.fixture
def forwarded_log():
    """Records from the diagnostic's own logger, which does not propagate."""
    import logging

    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    log = logging.getLogger("okwan_api.forwarded")
    handler = Capture()
    log.addHandler(handler)
    yield records
    log.removeHandler(handler)


def test_the_diagnostic_is_off_by_default(client, forwarded_log, monkeypatch):
    monkeypatch.delenv("OKWAN_LOG_FORWARDED", raising=False)
    _sign_in(client, "a@x.test", "9.9.9.9")
    assert forwarded_log == []


def test_the_diagnostic_logs_the_four_facts_and_nothing_else(
    client, forwarded_log, monkeypatch
):
    secret = "s3cret-shared-by-render"
    monkeypatch.setenv("OKWAN_LOG_FORWARDED", "1")
    monkeypatch.setenv("OKWAN_DASHBOARD_SECRET", secret)
    client.post(
        "/v1/sessions",
        json={"email": "victim@x.test", "password": "hunter2-correct-horse"},
        headers={"X-Forwarded-For": "1.1.1.1, 2.2.2.2",
                 "X-Okwan-Client-IP": "5.6.7.8",
                 "X-Okwan-Dashboard-Secret": secret,
                 "Authorization": "Bearer okw_should_never_appear",
                 "Cookie": "okwan_session=oks_should_never_appear"},
    )
    [line] = forwarded_log
    assert line == ("x-forwarded-for=['1.1.1.1', '2.2.2.2'] x-okwan-client-ip=present "
                    "dashboard-secret=matched via=dashboard chose='5.6.7.8'")
    for private in (secret, "victim@x.test", "hunter2", "okw_", "oks_"):
        assert private not in line


def test_the_diagnostic_shows_a_mismatched_secret_falling_back(
    client, forwarded_log, monkeypatch
):
    monkeypatch.setenv("OKWAN_LOG_FORWARDED", "1")
    monkeypatch.setenv("OKWAN_DASHBOARD_SECRET", "s3cret-shared-by-render")
    _sign_in(client, "a@x.test", "1.1.1.1, 2.2.2.2",
             **{"X-Okwan-Client-IP": "5.6.7.8", "X-Okwan-Dashboard-Secret": "wrong"})
    assert forwarded_log == [(
        "x-forwarded-for=['1.1.1.1', '2.2.2.2'] "
        "x-okwan-client-ip=present dashboard-secret=not-matched via=chain chose='2.2.2.2'"
    )]


def test_a_non_ascii_secret_header_is_a_mismatch_not_a_500(client, monkeypatch):
    monkeypatch.setenv("OKWAN_DASHBOARD_SECRET", "s3cret-shared-by-render")
    r = _sign_in(client, "a@x.test", "9.9.9.9",
                 **{"X-Okwan-Client-IP": "5.6.7.8",
                    "X-Okwan-Dashboard-Secret": "s3cr\xe9t".encode("latin-1")})
    assert r.status_code == 401


# ── which address is the client ─────────────────────────────────────

def _chosen(client, forwarded_log, monkeypatch, **headers) -> str:
    monkeypatch.setenv("OKWAN_LOG_FORWARDED", "1")
    client.post("/v1/sessions", json={"email": "a@x.test", "password": "x"},
                headers=headers)
    return forwarded_log[-1].rsplit("via=", 1)[1]


# The dashboard's chain, observed 2026-10-02: client, Cloudflare edge, Render.
OBSERVED = "175.213.142.165, 172.68.175.85, 10.28.103.150"


def test_cloudflare_names_a_direct_caller(client, forwarded_log, monkeypatch):
    via = _chosen(client, forwarded_log, monkeypatch, **{
        "X-Forwarded-For": OBSERVED, "CF-Connecting-IP": "175.213.142.165"})
    assert via == "cloudflare chose='175.213.142.165'"


def test_a_forged_chain_does_not_move_a_cloudflare_caller(
    client, forwarded_log, monkeypatch
):
    """Forged entries land on the left; Cloudflare's header is not in reach."""
    via = _chosen(client, forwarded_log, monkeypatch, **{
        "X-Forwarded-For": f"6.6.6.6, {OBSERVED}", "CF-Connecting-IP": "175.213.142.165"})
    assert via == "cloudflare chose='175.213.142.165'"


def test_the_trusted_dashboard_header_beats_cloudflare(client, forwarded_log, monkeypatch):
    """On a dashboard call, Cloudflare names the dashboard; the browser is forwarded."""
    monkeypatch.setenv("OKWAN_DASHBOARD_SECRET", "s3cret-shared-by-render")
    via = _chosen(client, forwarded_log, monkeypatch, **{
        "CF-Connecting-IP": "74.220.48.143",
        "X-Okwan-Client-IP": "175.213.142.165",
        "X-Okwan-Dashboard-Secret": "s3cret-shared-by-render"})
    assert via == "dashboard chose='175.213.142.165'"


def test_a_malformed_cloudflare_header_is_ignored(client, forwarded_log, monkeypatch):
    via = _chosen(client, forwarded_log, monkeypatch, **{
        "X-Forwarded-For": "9.9.9.9", "CF-Connecting-IP": "not-an-address"})
    assert via == "chain chose='9.9.9.9'"


def test_without_cloudflare_one_hop_never_reads_a_client_entry(
    client, forwarded_log, monkeypatch
):
    """The fallback errs toward the proxy's own entry, never the client's."""
    via = _chosen(client, forwarded_log, monkeypatch,
                  **{"X-Forwarded-For": f"6.6.6.6, {OBSERVED}"})
    assert via == "chain chose='10.28.103.150'"
