"""Paying for Pro: Checkout, the signed webhook that sets the plan, and the
portal. Stripe is a fake transport; webhook events are signed the way
Stripe signs them. Both stores.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from okwan_api import billing
from okwan_api.main import app
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, accounts, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
SECRET = "whsec_test_billing_secret"
PRICE = "price_test_pro_99"


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
        await s.close()


class FakeStripe:
    """Answers the two calls billing makes, and remembers what was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((request.url.path, {**form, "_auth": request.headers.get("authorization", "")}))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"type": "invalid_request_error",
                                                               "message": "echo sk_live_secret"}})
        if request.url.path.endswith("/checkout/sessions"):
            return httpx.Response(200, json={"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"})
        return httpx.Response(200, json={"id": "bps_1", "url": "https://billing.stripe.com/p/session/bps_1"})


@pytest.fixture
def stripe(monkeypatch):
    monkeypatch.setenv("OKWAN_BILLING_STRIPE_SECRET_KEY", "rk_test_restricted")
    monkeypatch.setenv("OKWAN_BILLING_STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("OKWAN_BILLING_PRO_PRICE_ID", PRICE)
    monkeypatch.setenv("OKWAN_DASHBOARD_URL", "https://app.okwan.test")
    fake = FakeStripe()
    billing.set_transport(httpx.MockTransport(fake.handler))
    yield fake
    billing.set_transport(None)


@pytest.fixture
async def client(store):
    from okwan_api.auth import set_store

    set_store(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://okwan.test") as c:
        yield c


async def _account(store, name="Paying ISV"):
    tenant = await store.create_tenant(name)
    email = f"owner-{os.urandom(3).hex()}@okwan.test"
    await store.add_account(tenant.id, email, accounts.hash_password("correct horse battery"))
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(tenant.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    return tenant, email, {"Authorization": f"Bearer {full}"}


def _signed(event: dict, secret: str = SECRET, at: int | None = None) -> tuple[bytes, dict]:
    body = json.dumps(event).encode()
    stamp = at if at is not None else int(time.time())
    sig = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"Stripe-Signature": f"t={stamp},v1={sig}", "Content-Type": "application/json"}


async def _send(client, event: dict, **kw) -> httpx.Response:
    body, headers = _signed(event, **kw)
    return await client.post("/v1/billing/webhook", content=body, headers=headers)


def _subscription(tenant_id: str, status: str, **extra) -> dict:
    return {"id": "sub_1", "object": "subscription", "customer": "cus_1", "status": status,
            "metadata": {"tenant_id": tenant_id}, "cancel_at_period_end": False,
            "current_period_end": int(time.time()) + 30 * 86400, **extra}


# ── the signature ──────────────────────────────────────────────────────

def test_a_signature_is_checked_like_stripe_does():
    body, headers = _signed({"type": "ping"})
    assert billing.verify_signature(body, headers["Stripe-Signature"], SECRET)
    assert not billing.verify_signature(body + b" ", headers["Stripe-Signature"], SECRET)
    assert not billing.verify_signature(body, headers["Stripe-Signature"], "whsec_other")
    old_body, old = _signed({"type": "ping"}, at=int(time.time()) - 600)
    assert not billing.verify_signature(old_body, old["Stripe-Signature"], SECRET)
    assert not billing.verify_signature(body, "garbage", SECRET)


async def test_an_unsigned_or_forged_event_changes_nothing(client, store, stripe):
    tenant, _, _ = await _account(store)
    event = {"type": "customer.subscription.updated", "data": {"object": _subscription(tenant.id, "active")}}
    r = await client.post("/v1/billing/webhook", content=json.dumps(event),
                          headers={"Stripe-Signature": "t=1,v1=00"})
    assert r.status_code == 400
    r = await _send(client, event, secret="whsec_forged")
    assert r.status_code == 400
    assert (await store.get_plan(tenant.id))[0] == "free"


# ── checkout ───────────────────────────────────────────────────────────

async def test_checkout_asks_stripe_for_pro_for_this_account_only(client, store, stripe):
    tenant, email, auth = await _account(store)
    r = await client.post("/v1/billing/checkout", headers=auth)
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.com/")
    path, form = stripe.calls[0]
    assert path == "/v1/checkout/sessions"
    assert form["mode"] == "subscription" and form["line_items[0][price]"] == PRICE
    assert form["client_reference_id"] == tenant.id == form["subscription_data[metadata][tenant_id]"]
    assert form["customer_email"] == email
    assert form["success_url"] == "https://app.okwan.test/settings?tab=plan&upgraded=1"
    assert form["_auth"] == "Bearer rk_test_restricted"
    # Coming back from Checkout changes nothing: only the signed webhook does.
    assert (await store.get_plan(tenant.id))[0] == "free"


async def test_billing_needs_a_dashboard_session_on_the_account(client, store, stripe):
    tenant, _, _ = await _account(store)
    key, _ = await store.issue_key(tenant.id, scope="admin")
    r = await client.post("/v1/billing/checkout", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403
    assert (await client.post("/v1/billing/checkout")).status_code == 403
    merchant = await store.create_tenant("A merchant", parent_id=tenant.id)
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(merchant.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    r = await client.post("/v1/billing/checkout", headers={"Authorization": f"Bearer {full}"})
    assert r.status_code == 403 and stripe.calls == []


async def test_without_stripe_settings_billing_is_off(client, store, monkeypatch):
    monkeypatch.delenv("OKWAN_BILLING_STRIPE_SECRET_KEY", raising=False)
    _, _, auth = await _account(store)
    assert (await client.post("/v1/billing/checkout", headers=auth)).status_code == 503
    assert (await client.get("/v1/billing", headers=auth)).json()["enabled"] is False


async def test_a_stripe_error_is_a_502_without_echoing_it(client, store, stripe):
    _, _, auth = await _account(store)
    stripe.status = 400
    r = await client.post("/v1/billing/checkout", headers=auth)
    assert r.status_code == 502 and "sk_live" not in r.text


# ── the webhook sets the plan ──────────────────────────────────────────

async def test_a_paid_checkout_moves_the_account_to_pro(client, store, stripe):
    tenant, _, auth = await _account(store)
    r = await _send(client, {"type": "checkout.session.completed", "data": {"object": {
        "mode": "subscription", "payment_status": "paid", "client_reference_id": tenant.id,
        "metadata": {"tenant_id": tenant.id}, "customer": "cus_1", "subscription": "sub_1"}}})
    assert r.json()["plan"] == "pro"
    assert (await store.get_plan(tenant.id)) == ("pro", 100_000)
    state = (await client.get("/v1/billing", headers=auth)).json()
    assert state["plan"] == "pro" and state["can_manage"] is True


async def test_an_unpaid_checkout_does_not(client, store, stripe):
    tenant, _, _ = await _account(store)
    await _send(client, {"type": "checkout.session.completed", "data": {"object": {
        "mode": "subscription", "payment_status": "unpaid", "client_reference_id": tenant.id,
        "customer": "cus_1", "subscription": "sub_1"}}})
    assert (await store.get_plan(tenant.id))[0] == "free"


@pytest.mark.parametrize(("status", "plan"), [
    ("active", "pro"), ("trialing", "pro"), ("past_due", "pro"),
    ("unpaid", "free"), ("canceled", "free"), ("incomplete_expired", "free")])
async def test_the_subscription_status_sets_the_plan(client, store, stripe, status, plan):
    tenant, _, _ = await _account(store)
    r = await _send(client, {"type": "customer.subscription.updated",
                             "data": {"object": _subscription(tenant.id, status)}})
    assert r.json()["plan"] == plan
    assert (await store.get_plan(tenant.id))[0] == plan


async def test_a_cancelled_subscription_returns_the_account_to_free(client, store, stripe):
    tenant, _, _ = await _account(store)
    await _send(client, {"type": "customer.subscription.updated", "data": {"object": _subscription(tenant.id, "active")}})
    await _send(client, {"type": "customer.subscription.deleted", "data": {"object": _subscription(tenant.id, "active")}})
    assert (await store.get_plan(tenant.id))[0] == "free"
    # A late retry of the original checkout cannot bring Pro back.
    await _send(client, {"type": "checkout.session.completed", "data": {"object": {
        "mode": "subscription", "payment_status": "paid", "client_reference_id": tenant.id,
        "customer": "cus_1", "subscription": "sub_1"}}})
    assert (await store.get_plan(tenant.id))[0] == "free"


async def test_an_event_without_metadata_finds_the_account_by_its_customer(client, store, stripe):
    tenant, _, _ = await _account(store)
    await _send(client, {"type": "customer.subscription.updated", "data": {"object": _subscription(tenant.id, "active")}})
    bare = _subscription(tenant.id, "unpaid")
    bare["metadata"] = {}
    await _send(client, {"type": "customer.subscription.updated", "data": {"object": bare}})
    assert (await store.get_plan(tenant.id))[0] == "free"


async def test_an_event_for_an_unknown_account_or_a_merchant_changes_nothing(client, store, stripe):
    tenant, _, _ = await _account(store)
    merchant = await store.create_tenant("A merchant", parent_id=tenant.id)
    for tenant_id in ("ten_0000000000000000", merchant.id):
        r = await _send(client, {"type": "customer.subscription.updated",
                                 "data": {"object": _subscription(tenant_id, "active", customer=f"cus_{tenant_id}")}})
        assert r.status_code == 200 and "plan" not in r.json()
    assert (await store.get_plan(tenant.id))[0] == "free"


# ── the portal ─────────────────────────────────────────────────────────

async def test_the_portal_opens_for_this_accounts_customer_only(client, store, stripe):
    tenant, _, auth = await _account(store)
    assert (await client.post("/v1/billing/portal", headers=auth)).status_code == 409
    await _send(client, {"type": "customer.subscription.updated", "data": {"object": _subscription(tenant.id, "active")}})
    r = await client.post("/v1/billing/portal", headers=auth)
    assert r.status_code == 200 and r.json()["url"].startswith("https://billing.stripe.com/")
    path, form = stripe.calls[-1]
    assert path == "/v1/billing_portal/sessions" and form["customer"] == "cus_1"
    _, _, other = await _account(store, "Another ISV")
    assert (await client.post("/v1/billing/portal", headers=other)).status_code == 409


async def test_an_account_already_on_pro_is_not_sent_to_checkout_again(client, store, stripe):
    tenant, _, auth = await _account(store)
    await store.set_plan(tenant.id, "pro")
    assert (await client.post("/v1/billing/checkout", headers=auth)).status_code == 409


async def test_deleting_an_account_deletes_its_billing_link(store):
    tenant, _, _ = await _account(store)
    await store.save_billing(tenant.id, customer_id="cus_del", subscription_id="sub_del", status="active",
                             cancel_at_period_end=False, current_period_end=None)
    await store.delete_tenant(tenant.id)
    assert await store.tenant_for_billing_customer("cus_del") is None
