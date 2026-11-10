"""A rail that quotes the key it rejected never hands that key back.

Found by the red team on 2026-11-10 (docs/audit/red-team.md): Stripe,
like several rails, answers a bad key with "Invalid API key provided:
sk_live_…". The connection test and the stored run history scrubbed that,
but the data routes returned the body as it came, so a read-only key, the
kind handed to an AI client, read back the merchant's stored Stripe secret
through `POST /v1/stripe/charges/list` and `POST /v1/query`.

The fix is where the text is made (okwan_core.redact): an upstream body is
cleaned of every credential the request carried and every stored value of
the connection before it becomes an `UpstreamError`, so no surface, today's
or a later one, can show it. These are the red team's exploits turned
around: each passes only when the attack fails.
"""
from __future__ import annotations

import base64

import httpx
import pytest
from okwan_core import OkwanClient, RateLimitProfile, UpstreamError, get
from okwan_core.redact import REDACTED
from okwan_vault import ADMIN, READ, EnvMasterKey, MemoryStore, new_key

STRIPE_KEY = "sk_live_LEAKME_0123456789abcdefABCDEF"
SQUARE_TOKEN = "EAAAl-square-token-0123456789-abcdef"


def _echo(request: httpx.Request) -> httpx.Response:
    """A rail that quotes everything it was sent: its credential headers,
    decoded Basic pairs and its URL, as an error body."""
    seen = [f"{k}: {v}" for k, v in request.headers.items()]
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        seen.append(base64.b64decode(auth[6:]).decode())
    token = auth[7:] if auth.lower().startswith("bearer ") else auth
    return httpx.Response(401, json={"error": {
        "type": "invalid_request_error",
        "message": f"Invalid API key provided: {token}. Seen: {' | '.join(seen)} at {request.url}"}})


@pytest.fixture
def echoing_rails(monkeypatch):
    """Every outbound client built from here on talks to the echoing rail;
    a client given its own transport (the in-process API client) does not."""
    real = httpx.AsyncClient

    def built(*args, **kwargs):
        if "transport" not in kwargs:
            kwargs["transport"] = httpx.MockTransport(_echo)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", built)
    return real


@pytest.fixture
async def api(echoing_rails):
    from okwan_api.auth import set_store
    from okwan_api.main import app

    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    shop = await store.create_tenant("Corner Café")
    await store.put_credential(shop.id, "stripe", "secret_key", STRIPE_KEY)
    await store.put_credential(shop.id, "square", "access_token", SQUARE_TOKEN)
    await store.put_credential(shop.id, "square", "environment", "sandbox")
    read_key, _ = await store.issue_key(shop.id, scope=READ)
    admin_key, _ = await store.issue_key(shop.id, scope=ADMIN)
    async with echoing_rails(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        yield c, shop, {"read": read_key, "admin": admin_key}


SURFACES = [
    ("POST", "/v1/stripe/charges/list", {}),
    ("POST", "/v1/query", {"sql": "SELECT * FROM stripe.charges"}),
    ("GET", "/v1/reconciliations/square_payments_payouts", None),
]


@pytest.mark.parametrize(("method", "path", "body"), SURFACES)
async def test_a_read_key_never_reads_a_stored_secret_back_through_a_rail_error(api, method, path, body):
    client, _, keys = api
    r = await client.request(method, path, headers={"Authorization": f"Bearer {keys['read']}"}, json=body)
    assert r.status_code >= 400
    assert STRIPE_KEY not in r.text and SQUARE_TOKEN not in r.text, f"{path} leaked a stored secret: {r.text}"
    assert REDACTED in r.text


async def test_the_connection_test_stays_redacted_too(api):
    client, shop, keys = api
    r = await client.post(f"/v1/tenants/{shop.id}/connectors/stripe/test",
                          headers={"Authorization": f"Bearer {keys['admin']}"})
    assert STRIPE_KEY not in r.text and REDACTED in r.text


async def test_every_connector_context_cleans_its_own_errors(echoing_rails):
    """The hosted MCP's tools reach rails through these same contexts, so
    an error they return to an agent is clean before they see it."""
    for name, creds in [("stripe", {"secret_key": STRIPE_KEY}),
                        ("square", {"access_token": SQUARE_TOKEN, "environment": "sandbox"})]:
        connector = get(name)
        ctx = connector.context(creds)
        try:
            with pytest.raises(UpstreamError) as caught:
                await ctx.client.get("/v1/anything")
        finally:
            await ctx.client.aclose()
        text = f"{caught.value} {caught.value.body}"
        assert all(v not in text for v in creds.values() if len(v) > 8), f"{name}: {text}"
        assert caught.value.status == 401 and "Invalid API key provided" in caught.value.body


class _Carry(httpx.Auth):
    """A credential the store never saw (a renewed OAuth token), a header
    key, a Basic pair and a key in the query string, all at once."""

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = "Basic " + base64.b64encode(b"acct-12345:license-SECRET-9876").decode()
        request.headers["X-Shopify-Access-Token"] = "shpat_header_secret_5555"
        request.headers["X-Renewed-Token"] = "rotated-bearer-token-7777"
        request.url = request.url.copy_merge_params({"api_key": "query-param-secret-3333"})
        yield request


async def test_every_way_a_credential_travels_is_cleaned(echoing_rails):
    client = OkwanClient("https://rail.example", _Carry(), RateLimitProfile(requests_per_second=100, burst=10))
    try:
        with pytest.raises(UpstreamError) as caught:
            await client.get("/v1/things")
    finally:
        await client.aclose()
    body = caught.value.body
    basic = base64.b64encode(b"acct-12345:license-SECRET-9876").decode()
    for secret in (basic, "acct-12345:license-SECRET-9876", "license-SECRET-9876", "acct-12345",
                   "shpat_header_secret_5555", "rotated-bearer-token-7777", "query-param-secret-3333"):
        assert secret not in body, f"{secret} left in: {body}"
    assert "Invalid API key provided" in body and "rail.example/v1/things" in body, "the error still says what happened"


def test_a_postgres_error_never_shows_the_dsn_or_its_password():
    from okwan_postgres.connector import PgTransport

    dsn = "postgresql://reader:s3cr%40t-pw-4242@db.example.com:5432/books"
    err = PgTransport(dsn)._upstream(502, f"postgres connect failed: could not reach {dsn} (password s3cr@t-pw-4242)")
    assert "s3cr%40t-pw-4242" not in err.body and "s3cr@t-pw-4242" not in err.body and dsn not in err.body
    assert err.body.startswith("postgres connect failed") and err.status == 502
