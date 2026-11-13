"""The refund policy is published, says what the Terms say, and is in
front of a customer before any card is charged.

A card issuer deciding a disputed payment asks three things of a seller
with a no-refund rule: what the rule is, whether the customer was shown it
before paying, and how. So what must hold:

- the page at okwan.ai/refunds says every sentence of the policy the API
  holds (okwan_api.policy), with the same date, and the Terms still say
  what the policy is drawn from: one policy, not three that drift;
- Stripe Checkout shows it beside the pay button, and the dashboard's plan
  page shows it next to the Upgrade button, ahead of it in reading order;
- what an account was shown when it went to pay is recorded: the policy's
  version, when, and from which address; and, where a tick is required,
  when Stripe reported it ticked.

Stripe is a fake transport, as in tests/test_billing.py. Both stores.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from okwan_api import billing, policy
from okwan_api.main import app
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, accounts, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
SECRET = "whsec_test_billing_secret"
DASHBOARD_SECRET = "dashboard-shared-secret-for-tests"


def _words(path: str) -> str:
    """A page's source as the words a reader sees: tags and JSX braces
    out, entities turned into their characters, white space collapsed."""
    source = (ROOT / path).read_text()
    source = re.sub(r"\{/\*.*?\*/\}|/\*.*?\*/", " ", source, flags=re.DOTALL)
    source = re.sub(r"<[^>]+>|\{\" \"\}", " ", source)
    return " ".join(html.unescape(source).replace("’", "'").split())


SENTENCES = [s for _, sentences in policy.POLICY for s in sentences]


# ── one policy ──────────────────────────────────────────────────────

@pytest.mark.parametrize("sentence", SENTENCES)
def test_the_published_page_says_every_sentence_of_the_policy(sentence):
    assert sentence in _words("apps/web/app/refunds/page.tsx")


def test_the_page_and_the_policy_carry_the_same_date():
    page = (ROOT / "apps/web/app/refunds/page.tsx").read_text()
    assert f'updated="{policy.UPDATED}"' in page
    said = datetime.strptime(policy.UPDATED, "%B %d, %Y").replace(tzinfo=UTC)
    assert said.date().isoformat() == policy.VERSION


def test_the_policy_is_drawn_from_the_terms():
    """What the policy says about price, renewal, cancelling and refunds is
    what the Terms already bind both sides to. If the Terms change, this
    fails until the policy, its page and its version change with them."""
    terms = _words("apps/web/app/terms/page.tsx")
    for clause in (
        "99 US dollars a month",
        "billed monthly in advance by card through Stripe",
        "renews each month until you cancel",
        "You can cancel at any time from Manage billing in the dashboard",
        "Pro stays on until the end of the month you have paid for, and the account then returns to the free plan",
        "Fees are non-refundable except where the law or our agreement says otherwise",
    ):
        assert clause in terms, clause
    assert 'href="/refunds"' in (ROOT / "apps/web/app/terms/page.tsx").read_text(), "and the Terms point at it"


def test_the_policy_as_sent_to_a_card_issuer_is_the_whole_of_it():
    text = policy.text()
    assert all(sentence in text for sentence in SENTENCES)
    assert policy.VERSION in text and "https://okwan.ai/refunds" in text
    said = policy.disclosure()
    assert "next to the Upgrade button" in said and "beside the pay button" in said and policy.refunds_url() in said


def test_the_site_links_the_policy_from_every_legal_page_and_the_home_page():
    frame = (ROOT / "apps/web/app/_components/legal.tsx").read_text()
    assert frame.count('href="/refunds"') == 2, "the header and the footer of every legal page"
    assert '["Refunds", "/refunds"]' in (ROOT / "apps/web/app/page.tsx").read_text()


def test_the_plan_page_shows_the_policy_before_the_upgrade_button():
    card = (ROOT / "apps/dashboard/app/_components/settings/billing-card.tsx").read_text()
    shown, button = card.index("href={REFUNDS}"), card.index("Upgrade for $")
    assert shown < button, "ahead of the button in the page: beside it on a wide screen, above it on a phone"
    assert 'const REFUNDS = "https://okwan.ai/refunds"' in card
    assert "isn't refunded once its month has started" in html.unescape(card).replace("&apos;", "'")
    assert "renews each month until you cancel" in card


# ── at checkout ─────────────────────────────────────────────────────

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
    def __init__(self) -> None:
        self.forms: list[dict] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.forms.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"type": "invalid_request_error"}})
        return httpx.Response(200, json={"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"})


@pytest.fixture
def stripe(monkeypatch):
    monkeypatch.setenv("OKWAN_BILLING_STRIPE_SECRET_KEY", "rk_test_restricted")
    monkeypatch.setenv("OKWAN_BILLING_STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("OKWAN_BILLING_PRO_PRICE_ID", "price_test_pro_99")
    monkeypatch.setenv("OKWAN_DASHBOARD_URL", "https://app.okwan.test")
    monkeypatch.setenv("OKWAN_DASHBOARD_SECRET", DASHBOARD_SECRET)
    monkeypatch.delenv("OKWAN_BILLING_REQUIRE_TERMS", raising=False)
    monkeypatch.delenv("OKWAN_SITE_URL", raising=False)
    fake = FakeStripe()
    billing.set_transport(httpx.MockTransport(fake.handler))
    yield fake
    billing.set_transport(None)


@pytest.fixture
async def client(store):
    from okwan_api.auth import set_store

    set_store(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        yield c


async def _account(store):
    tenant = await store.create_tenant("Paying ISV")
    await store.add_account(tenant.id, f"owner-{os.urandom(3).hex()}@okwan.test",
                            accounts.hash_password("correct horse battery"))
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(tenant.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    # As the dashboard sends it: the browser's address, vouched for by the shared secret.
    return tenant, {"Authorization": f"Bearer {full}", "x-okwan-client-ip": "203.0.113.24",
                    "x-okwan-dashboard-secret": DASHBOARD_SECRET}


async def _paid(client, tenant_id: str, *, consent: dict | None, created: int) -> httpx.Response:
    event = {"type": "checkout.session.completed", "created": created, "data": {"object": {
        "mode": "subscription", "payment_status": "paid", "customer": "cus_1", "subscription": "sub_1",
        "client_reference_id": tenant_id, "metadata": {"tenant_id": tenant_id}, "consent": consent}}}
    body = json.dumps(event).encode()
    stamp = int(time.time())
    sig = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return await client.post("/v1/billing/webhook", content=body,
                             headers={"Stripe-Signature": f"t={stamp},v1={sig}", "Content-Type": "application/json"})


async def test_checkout_shows_the_policy_beside_the_pay_button(client, store, stripe):
    _, headers = await _account(store)
    assert (await client.post("/v1/billing/checkout", headers=headers)).status_code == 200
    [form] = stripe.forms
    shown = form["custom_text[submit][message]"]
    assert shown == policy.at_checkout() and len(shown) <= 1200, "Stripe's limit for this text"
    for said in ("99 US dollars a month", "renews each month until you cancel", "Manage billing",
                 "not refunded once its month has started",
                 "[Refunds and cancellation](https://okwan.ai/refunds)", "[Terms of Service](https://okwan.ai/terms)"):
        assert said in shown, said
    # Which policy that was stays with the payment in Stripe's own records.
    assert form["metadata[refund_policy]"] == form["subscription_data[metadata][refund_policy]"] == policy.VERSION
    assert "consent_collection[terms_of_service]" not in form, "a required tick is the operator's switch"


async def test_agreement_is_a_box_to_tick_once_the_operator_switches_it_on(client, store, stripe, monkeypatch):
    monkeypatch.setenv("OKWAN_BILLING_REQUIRE_TERMS", "on")
    _, headers = await _account(store)
    assert (await client.post("/v1/billing/checkout", headers=headers)).status_code == 200
    [form] = stripe.forms
    assert form["consent_collection[terms_of_service]"] == "required"
    beside_the_box = form["custom_text[terms_of_service_acceptance][message]"]
    assert "(https://okwan.ai/terms)" in beside_the_box and "(https://okwan.ai/refunds)" in beside_the_box
    assert form["custom_text[submit][message]"] == policy.at_checkout(), "and the policy is still beside the button"


async def test_what_the_account_was_shown_is_recorded(client, store, stripe):
    tenant, headers = await _account(store)
    before = datetime.now(UTC)
    assert (await client.post("/v1/billing/checkout", headers=headers)).status_code == 200
    record = await store.billing_for(tenant.id)
    assert record["policy_version"] == policy.VERSION
    assert record["checkout_ip"] == "203.0.113.24", "the browser's address, as the dashboard reports it"
    assert before - timedelta(seconds=2) <= record["checkout_at"] <= datetime.now(UTC) + timedelta(seconds=2)
    assert record["terms_accepted_at"] is None
    # An opened checkout is not a subscription: nothing to manage, still free.
    shown = (await client.get("/v1/billing", headers=headers)).json()
    assert (shown["plan"], shown["subscription"], shown["can_manage"]) == ("free", None, False)


async def test_the_record_outlives_the_payment_and_every_later_event(client, store, stripe):
    tenant, headers = await _account(store)
    await client.post("/v1/billing/checkout", headers=headers)
    opened = await store.billing_for(tenant.id)
    assert (await _paid(client, tenant.id, consent=None, created=int(time.time()))).json()["plan"] == "pro"
    await store.save_billing(tenant.id, customer_id="", subscription_id="", status="past_due",
                             cancel_at_period_end=True, current_period_end=datetime.now(UTC) + timedelta(days=9))
    record = await store.billing_for(tenant.id)
    assert (record["customer_id"], record["subscription_id"], record["status"]) == ("cus_1", "sub_1", "past_due")
    for kept in ("policy_version", "checkout_at", "checkout_ip"):
        assert record[kept] == opened[kept], kept


async def test_stripes_report_that_the_box_was_ticked_is_recorded(client, store, stripe):
    tenant, headers = await _account(store)
    await client.post("/v1/billing/checkout", headers=headers)
    at = int(time.time()) - 5
    assert (await _paid(client, tenant.id, consent={"terms_of_service": "accepted"}, created=at)).status_code == 200
    assert (await store.billing_for(tenant.id))["terms_accepted_at"] == datetime.fromtimestamp(at, UTC)


@pytest.mark.parametrize("consent", [None, {}, {"terms_of_service": None}, {"terms_of_service": "none"}])
async def test_no_tick_is_recorded_when_stripe_reports_none(client, store, stripe, consent):
    tenant, headers = await _account(store)
    await client.post("/v1/billing/checkout", headers=headers)
    assert (await _paid(client, tenant.id, consent=consent, created=int(time.time()))).json()["plan"] == "pro"
    assert (await store.billing_for(tenant.id))["terms_accepted_at"] is None


async def test_a_checkout_stripe_refused_records_nothing(client, store, stripe):
    tenant, headers = await _account(store)
    stripe.status = 400
    assert (await client.post("/v1/billing/checkout", headers=headers)).status_code == 502
    assert await store.billing_for(tenant.id) is None


async def test_the_site_address_in_the_links_follows_the_setting(client, store, stripe, monkeypatch):
    monkeypatch.setenv("OKWAN_SITE_URL", "https://staging.okwan.test/")
    _, headers = await _account(store)
    await client.post("/v1/billing/checkout", headers=headers)
    assert "(https://staging.okwan.test/refunds)" in stripe.forms[0]["custom_text[submit][message]"]
