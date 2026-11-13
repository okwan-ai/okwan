"""The refund policy is published, says what the Terms say, and is in
front of a customer before any card is charged.

A card issuer deciding a disputed payment asks three things of a seller
with a no-refund rule: what the rule is, whether the customer was shown it
before paying, and how. So what must hold:

- there is one policy. The page at okwan.ai/refunds renders a file that
  okwan_api.policy writes and has no words of its own, so the page, Stripe
  Checkout and the copy sent to a bank cannot say different things; the
  words cannot change without the version changing; and the Terms still
  say what the policy is drawn from;
- Stripe Checkout shows it beside the pay button, and the dashboard's plan
  page shows it next to the Upgrade button, ahead of it in reading order;
- what each Checkout session showed is recorded under that session: the
  policy's version, when, and from which address; when Stripe reports the
  session paid, that record and no other is marked, once; and no later
  checkout, payment or billing event changes it;
- billing is a signed-in person's: an API key is refused whatever header
  comes with it, and a payment names its account only through what Okwan
  itself put on the session.

Stripe is a fake transport, as in tests/test_billing.py. Both stores.
The last four sections keep what an attacker's pass found.
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
from okwan_vault.usage import UNPAID_CHECKOUT_KEPT

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
SECRET = "whsec_test_billing_secret"
DASHBOARD_SECRET = "dashboard-shared-secret-for-tests"
PAGE = "apps/web/app/refunds/page.tsx"
PAGE_FILE = "apps/web/app/refunds/policy.json"
TERMS = "apps/web/app/terms/page.tsx"

#: The words of each version of the policy, by digest of the page's file.
#: A line here is never edited. New words are a new version: change VERSION
#: and UPDATED in okwan_api/policy.py, put what text() returned before into
#: its ARCHIVE under the old version, and add the new line.
PINNED = {
    "2026-11-13": "1a55d79b4b9fcf8c3475190cd77e7d55802c174f0a29f7993161e2be12093525",
}
#: The Terms' "Plans and fees" section, by digest of its words. If this
#: fails, the Terms changed there: read the section against the refund
#: policy, change the policy with it if they no longer agree, then update.
TERMS_PLANS = "34c9dc974209432e902ba18871e00250cde76e488425a956bbcb894c62bbc913"


def _words(source: str) -> str:
    """A page's source as the words a reader sees: comments, tags and JSX
    braces out, entities turned into their characters, white space collapsed."""
    source = re.sub(r"\{/\*.*?\*/\}|/\*.*?\*/", " ", source, flags=re.DOTALL)
    source = re.sub(r"<[^>]+>|\{\" \"\}", " ", source)
    return " ".join(html.unescape(source).replace("’", "'").split())


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def _plans_section(terms: str) -> str:
    """The words of the Terms' part with id "plans"."""
    start = terms.index('id: "plans"')
    return _words(terms[start:terms.index("\n  {\n    id: ", start)])


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


#: Everything the page's source may say in a string, outside its comments:
#: where it imports from, its address, and the look of a link. No prose.
PAGE_MAY_SAY = {"next", "../_components/legal", "./policy.json", "/refunds", "ul",
                "font-medium text-ink underline underline-offset-4"}
#: The page's two template strings: the tab's title and a list key.
PAGE_TEMPLATES = {"${policy.title} — Okwan", "${i}-${j}"}


def own_words(source: str) -> list[str]:
    """What a page's source would put in front of a reader by itself: text
    between tags, and any string that is not on the short list above. The
    refund page has none: every word it shows comes from the policy's file."""
    code = re.sub(r"\{/\*.*?\*/\}|/\*.*?\*/", " ", source, flags=re.DOTALL)
    code = re.sub(r"^\s*//.*$", " ", code, flags=re.MULTILINE)
    found = [t for t in re.findall(r"`([^`]*)`", code) if t not in PAGE_TEMPLATES]
    code = re.sub(r"`[^`]*`", " ", code)
    code = re.sub(r"/\\\[.*?/g", " ", code)                    # the link pattern
    found += [t for t in re.findall(r"\"([^\"\n]*)\"", code) + re.findall(r"'([^'\n]*)'", code)
              if t not in PAGE_MAY_SAY]
    # Text a tag encloses: after a tag's ">" and before the next "<" or "{".
    found += [t.strip() for t in re.findall(r"(?<=[\w\"'}/])>([^<>{}=;()]*[A-Za-z][^<>{}=;()]*)(?=[<{])", code)]
    return [t for t in found if t.strip()]


# ── one policy ──────────────────────────────────────────────────────

def test_the_pages_file_is_exactly_what_the_policy_writes():
    assert _read(PAGE_FILE) == policy.page_file(), (
        "write it again: python -m okwan_api.policy > apps/web/app/refunds/policy.json")


def test_the_page_has_no_words_of_its_own():
    page = _read(PAGE)
    assert own_words(page) == []
    # And it renders the file, whole: title, date, introduction and every line of every part.
    for used in ('import policy from "./policy.json"', "title={policy.title}", "updated={policy.updated}",
                 "linked(policy.intro)", "policy.parts.map((part)", "part.blocks.map((block, i)",
                 "block.lines.map((line, j)"):
        assert used in page, used
    assert page.count("block.lines.map((line, j)") == 2, "a list's items and a paragraph's lines"


def test_the_date_on_the_page_is_the_version():
    said = datetime.strptime(policy.UPDATED, "%B %d, %Y").replace(tzinfo=UTC)
    assert said.date().isoformat() == policy.VERSION == policy.published()["version"]


def test_the_words_cannot_change_without_the_version():
    assert _digest(policy.page_file()) == PINNED[policy.VERSION], (
        "the policy's words changed: give it a new VERSION (see PINNED above)")
    assert set(PINNED) == {policy.VERSION} | set(policy.ARCHIVE), "every earlier version is kept, whole"
    for version, words in policy.ARCHIVE.items():
        assert policy.text(version) == words and f"(version {version})" in words


def test_the_policy_as_sent_to_a_card_issuer_is_the_whole_page():
    text = policy.text()
    assert policy.text(policy.VERSION) == text
    page = json.loads(_read(PAGE_FILE))
    said = [page["title"], page["intro"]] + [p["title"] for p in page["parts"]] + [
        line for p in page["parts"] for b in p["blocks"] for line in b["lines"]]
    for words in said:
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", words)
        for sentence in re.split(r"(?<=[.])\s+(?=[A-Z])", plain):
            assert sentence.split(" (")[0] in re.sub(r" \(https://[^)]+\)", "", text), sentence
    assert f"(version {policy.VERSION})" in text and "https://okwan.ai/refunds" in text
    assert "Terms of Service (https://okwan.ai/terms#plans)" in text, "a link's address is written out"
    said = policy.disclosure()
    assert "next to the Upgrade button" in said and "beside the pay button" in said and policy.refunds_url() in said


def test_an_unknown_version_is_never_answered_with_other_words():
    with pytest.raises(KeyError):
        policy.text("2020-01-01")


def test_the_price_is_one_number():
    assert billing.PRO_PRICE_USD == policy.PRO_PRICE_USD == 99
    price = f"Pro is {policy.PRO_PRICE_USD} US dollars a month"
    assert policy.sentences()[2].startswith(price) and policy.at_checkout().startswith(price)
    assert f"{policy.PRO_PRICE_USD} US dollars a month" in _words(_read(TERMS))
    # The plan page takes it from the API, which takes it from the policy.
    assert "price={bill.pro_price_usd}" in _read("apps/dashboard/app/settings/page.tsx")
    card = _read("apps/dashboard/app/_components/settings/billing-card.tsx")
    assert "Upgrade for $${price}/month" in card and "$99" not in card and "99 " not in card


def test_the_policy_is_drawn_from_the_terms():
    """What the policy says about price, renewal, cancelling and refunds is
    what the Terms already bind both sides to. If the Terms change there,
    this fails until someone has read the two against each other."""
    source = _read(TERMS)
    terms = _words(source)
    for clause in (
        "99 US dollars a month",
        "billed monthly in advance by card through Stripe",
        "renews each month until you cancel",
        "You can cancel at any time from Manage billing in the dashboard",
        "Pro stays on until the end of the month you have paid for, and the account then returns to the free plan",
        "Fees are non-refundable except where the law or our agreement says otherwise",
    ):
        assert clause in terms, clause
    assert 'href="/refunds"' in source, "and the Terms point at it"
    assert _digest(_plans_section(source)) == TERMS_PLANS, "the Terms' Plans and fees section changed (see TERMS_PLANS)"


def test_the_company_and_address_are_the_sites():
    frame = _read("apps/web/app/_components/legal.tsx")
    assert f'export const COMPANY = "{policy.COMPANY}"' in frame
    assert f'export const CONTACT_EMAIL = "{policy.CONTACT_EMAIL}"' in frame


def test_the_site_links_the_policy_from_every_legal_page_and_the_home_page():
    frame = _read("apps/web/app/_components/legal.tsx")
    assert frame.count('href="/refunds"') == 2, "the header and the footer of every legal page"
    assert '["Refunds", "/refunds"]' in _read("apps/web/app/page.tsx")


def test_the_plan_page_shows_the_policy_before_the_upgrade_button():
    card = _read("apps/dashboard/app/_components/settings/billing-card.tsx")
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
    """Each Checkout session gets its own id, as Stripe gives one."""

    def __init__(self) -> None:
        self.forms: list[dict] = []
        self.status = 200
        self.tag = os.urandom(4).hex()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.forms.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"type": "invalid_request_error"}})
        if request.url.path.endswith("/checkout/sessions"):
            session = f"cs_test_{self.tag}_{len(self.forms)}"
            return httpx.Response(200, json={"id": session, "url": f"https://checkout.stripe.com/c/pay/{session}"})
        return httpx.Response(200, json={"id": "bps_1", "url": "https://billing.stripe.com/p/session/bps_1"})


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


def _from(session: str, ip: str = "203.0.113.24") -> dict:
    """As the dashboard sends it: the browser's address, vouched for by the shared secret."""
    return {"Authorization": f"Bearer {session}", "x-okwan-client-ip": ip,
            "x-okwan-dashboard-secret": DASHBOARD_SECRET}


async def _account(store, name: str = "Paying ISV"):
    tenant = await store.create_tenant(name)
    await store.add_account(tenant.id, f"owner-{os.urandom(3).hex()}@okwan.test",
                            accounts.hash_password("correct horse battery"))
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(tenant.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    return tenant, _from(full)


async def _open(client, headers) -> str:
    """Open a checkout; the id of the Stripe session it made."""
    r = await client.post("/v1/billing/checkout", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["url"].rsplit("/", 1)[1]


async def _send(client, event: dict) -> httpx.Response:
    body = json.dumps(event).encode()
    stamp = int(time.time())
    sig = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return await client.post("/v1/billing/webhook", content=body,
                             headers={"Stripe-Signature": f"t={stamp},v1={sig}", "Content-Type": "application/json"})


async def _paid(client, tenant_id: str, session: str, *, consent: dict | None = None, created: int | None = None,
                **over) -> httpx.Response:
    """Stripe's signed report that one Checkout session was paid."""
    obj = {"id": session, "mode": "subscription", "payment_status": "paid", "customer": "cus_1",
           "subscription": "sub_1", "client_reference_id": tenant_id,
           "metadata": {"tenant_id": tenant_id, "refund_policy": policy.VERSION}, "consent": consent, **over}
    return await _send(client, {"type": "checkout.session.completed", "created": created or int(time.time()),
                                "data": {"object": obj}})


async def _ended(client, tenant_id: str) -> httpx.Response:
    return await _send(client, {"type": "customer.subscription.deleted", "data": {"object": {
        "id": "sub_1", "object": "subscription", "customer": "cus_1", "status": "canceled",
        "cancel_at_period_end": False, "metadata": {"tenant_id": tenant_id}}}})


async def _row(store, tenant_id: str, session: str) -> dict:
    [row] = [c for c in await store.checkouts_for(tenant_id) if c["session_id"] == session]
    return row


async def test_checkout_shows_the_policy_beside_the_pay_button(client, store, stripe):
    _, headers = await _account(store)
    await _open(client, headers)
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
    await _open(client, headers)
    [form] = stripe.forms
    assert form["consent_collection[terms_of_service]"] == "required"
    beside_the_box = form["custom_text[terms_of_service_acceptance][message]"]
    assert "(https://okwan.ai/terms)" in beside_the_box and "(https://okwan.ai/refunds)" in beside_the_box
    assert form["custom_text[submit][message]"] == policy.at_checkout(), "and the policy is still beside the button"


async def test_what_a_checkout_showed_is_recorded_under_its_own_session(client, store, stripe):
    tenant, headers = await _account(store)
    before = datetime.now(UTC)
    session = await _open(client, headers)
    [row] = await store.checkouts_for(tenant.id)
    assert (row["session_id"], row["tenant_id"], row["policy_version"]) == (session, tenant.id, policy.VERSION)
    assert row["ip"] == "203.0.113.24", "the browser's address, as the dashboard reports it"
    assert before - timedelta(seconds=2) <= row["opened_at"] <= datetime.now(UTC) + timedelta(seconds=2)
    assert (row["paid_at"], row["subscription_id"], row["terms_accepted_at"]) == (None, None, None)
    # An opened checkout is not a subscription: no billing record, nothing to manage, still free.
    assert await store.billing_for(tenant.id) is None
    shown = (await client.get("/v1/billing", headers=headers)).json()
    assert (shown["plan"], shown["subscription"], shown["can_manage"]) == ("free", None, False)


async def test_the_paid_session_is_marked_once_and_nothing_later_changes_it(client, store, stripe):
    tenant, headers = await _account(store)
    session = await _open(client, headers)
    opened = await _row(store, tenant.id, session)
    at = int(time.time()) - 5
    assert (await _paid(client, tenant.id, session, created=at)).json()["plan"] == "pro"
    paid = await _row(store, tenant.id, session)
    assert (paid["paid_at"], paid["subscription_id"]) == (datetime.fromtimestamp(at, UTC), "sub_1")
    for kept in ("policy_version", "opened_at", "ip"):
        assert paid[kept] == opened[kept], kept
    # Stripe sends the event again, later, saying something else; the subscription changes; it ends.
    await _paid(client, tenant.id, session, created=at + 900, consent={"terms_of_service": "accepted"},
                subscription="sub_other")
    await store.save_billing(tenant.id, customer_id="", subscription_id="", status="past_due",
                             cancel_at_period_end=True, current_period_end=datetime.now(UTC) + timedelta(days=9))
    await _ended(client, tenant.id)
    assert await _row(store, tenant.id, session) == paid


async def test_stripes_report_that_the_box_was_ticked_is_recorded(client, store, stripe):
    tenant, headers = await _account(store)
    session = await _open(client, headers)
    at = int(time.time()) - 5
    assert (await _paid(client, tenant.id, session, consent={"terms_of_service": "accepted"}, created=at)
            ).status_code == 200
    assert (await _row(store, tenant.id, session))["terms_accepted_at"] == datetime.fromtimestamp(at, UTC)


@pytest.mark.parametrize("consent", [None, {}, {"terms_of_service": None}, {"terms_of_service": "none"}])
async def test_no_tick_is_recorded_when_stripe_reports_none(client, store, stripe, consent):
    tenant, headers = await _account(store)
    session = await _open(client, headers)
    assert (await _paid(client, tenant.id, session, consent=consent)).json()["plan"] == "pro"
    row = await _row(store, tenant.id, session)
    assert row["paid_at"] is not None and row["terms_accepted_at"] is None


async def test_a_checkout_stripe_refused_records_nothing(client, store, stripe):
    tenant, headers = await _account(store)
    stripe.status = 400
    assert (await client.post("/v1/billing/checkout", headers=headers)).status_code == 502
    assert await store.checkouts_for(tenant.id) == [] and await store.billing_for(tenant.id) is None


async def test_the_site_address_in_the_links_follows_the_setting(client, store, stripe, monkeypatch):
    monkeypatch.setenv("OKWAN_SITE_URL", "https://staging.okwan.test/")
    _, headers = await _account(store)
    await _open(client, headers)
    assert "(https://staging.okwan.test/refunds)" in stripe.forms[0]["custom_text[submit][message]"]


async def test_an_account_opens_only_so_many_checkouts_a_day(client, store, stripe):
    tenant, headers = await _account(store)
    for _ in range(billing.MAX_OPEN_CHECKOUTS):
        await _open(client, headers)
    r = await client.post("/v1/billing/checkout", headers=headers)
    assert r.status_code == 429 and len(stripe.forms) == billing.MAX_OPEN_CHECKOUTS, "Stripe is not asked again"
    assert len(await store.checkouts_for(tenant.id)) == billing.MAX_OPEN_CHECKOUTS


async def test_only_unpaid_sessions_stripe_has_long_ended_are_dropped(store):
    tenant = await store.create_tenant("Paying ISV")
    now = datetime.now(UTC).replace(microsecond=0)
    old = now - UNPAID_CHECKOUT_KEPT - timedelta(hours=1)
    tag = os.urandom(4).hex()
    await store.open_checkout(tenant.id, f"cs_{tag}_paid", policy_version="2026-11-13", at=old, ip="203.0.113.24")
    assert await store.checkout_paid(tenant.id, f"cs_{tag}_paid", at=old, subscription_id="sub_1",
                                     terms_accepted_at=None)
    await store.open_checkout(tenant.id, f"cs_{tag}_left", policy_version="2026-11-13", at=old, ip="203.0.113.24")
    await store.open_checkout(tenant.id, f"cs_{tag}_recent", policy_version="2026-11-13",
                              at=now - timedelta(days=2), ip="203.0.113.24")
    await store.open_checkout(tenant.id, f"cs_{tag}_now", policy_version="2026-11-13", at=now, ip="203.0.113.24")
    assert [c["session_id"] for c in await store.checkouts_for(tenant.id)] == [
        f"cs_{tag}_now", f"cs_{tag}_recent", f"cs_{tag}_paid"], "newest first; the paid one is kept whatever its age"


# ── attacker's pass 1: billing is a signed-in person's, never a key's ──

async def test_an_api_key_with_a_session_shaped_header_beside_it_is_refused(client, store, stripe):
    """The key was the identity and a made-up `oks_` bearer satisfied the
    "from the dashboard" check: a script opened a checkout, and the record
    said a person had been shown the policy."""
    tenant = await store.create_tenant("No one has signed in")
    key, _ = await store.issue_key(tenant.id, scope="admin")
    made_up = {"X-Okwan-Key": key, "Authorization": f"Bearer {SESSION_PREFIX}_not-a-session"}
    for method, path in (("POST", "/v1/billing/checkout"), ("GET", "/v1/billing"), ("POST", "/v1/billing/portal")):
        assert (await client.request(method, path, headers=made_up)).status_code == 403, path
        assert (await client.request(method, path, headers={"Authorization": f"Bearer {key}"})).status_code == 403
        assert (await client.request(
            method, path, headers={"Authorization": f"Bearer {SESSION_PREFIX}_not-a-session"})).status_code == 401
    assert stripe.forms == [] and await store.checkouts_for(tenant.id) == []
    assert await store.billing_for(tenant.id) is None


async def test_a_key_does_not_ride_on_another_accounts_real_session(client, store, stripe):
    """A real session with someone else's key beside it: refused, and
    nothing is opened for either account."""
    mine, headers = await _account(store, "Signed in")
    other = await store.create_tenant("Key holder")
    key, _ = await store.issue_key(other.id, scope="admin")
    r = await client.post("/v1/billing/checkout", headers={**headers, "X-Okwan-Key": key})
    assert r.status_code == 403 and stripe.forms == []
    assert await store.checkouts_for(mine.id) == [] == await store.checkouts_for(other.id)


async def test_the_portal_of_a_paying_account_is_not_open_to_its_key(client, store, stripe):
    tenant, _ = await _account(store)
    await store.save_billing(tenant.id, customer_id="cus_paying", subscription_id="sub_1", status="active",
                             cancel_at_period_end=False, current_period_end=None)
    key, _ = await store.issue_key(tenant.id, scope="admin")
    r = await client.post("/v1/billing/portal", headers={"X-Okwan-Key": key, "Authorization": "Bearer oks_x"})
    assert r.status_code == 403 and stripe.forms == []


# ── attacker's pass 2: a later checkout cannot rewrite a paid one ──────

async def test_a_cancelled_customer_cannot_replace_the_record_of_the_payment_they_dispute(
        client, store, stripe, monkeypatch):
    """Pay, cancel, and once free again click Upgrade from somewhere else
    under a newer policy. The record of the payment that was made stays
    as it was; the new click is its own row, unpaid."""
    monkeypatch.setenv("OKWAN_BILLING_REQUIRE_TERMS", "on")
    tenant, headers = await _account(store)
    session = await _open(client, headers)
    ticked = int(time.time()) - 3
    assert (await _paid(client, tenant.id, session, consent={"terms_of_service": "accepted"}, created=ticked)
            ).json()["plan"] == "pro"
    paid_for = await _row(store, tenant.id, session)
    assert (await client.post("/v1/billing/checkout", headers=headers)).status_code == 409, "already Pro"
    assert (await _ended(client, tenant.id)).json()["plan"] == "free"

    monkeypatch.setattr(policy, "VERSION", "2027-02-01")
    monkeypatch.setenv("OKWAN_BILLING_REQUIRE_TERMS", "off")
    later = await _open(client, {**headers, "x-okwan-client-ip": "192.0.2.66"})

    assert await _row(store, tenant.id, session) == paid_for
    assert (paid_for["policy_version"], paid_for["ip"], paid_for["subscription_id"]) == (
        "2026-11-13", "203.0.113.24", "sub_1")
    assert paid_for["terms_accepted_at"] == paid_for["paid_at"] == datetime.fromtimestamp(ticked, UTC)
    assert paid_for["opened_at"] <= datetime.now(UTC)
    new = await _row(store, tenant.id, later)
    assert (new["policy_version"], new["ip"], new["paid_at"], new["terms_accepted_at"]) == (
        "2027-02-01", "192.0.2.66", None, None)


async def test_the_record_marked_paid_is_the_session_that_was_paid(client, store, stripe, monkeypatch):
    """Two sessions open across a change of policy; the older is paid. Its
    own row is marked, with the version it showed, and the newer is not."""
    monkeypatch.setenv("OKWAN_BILLING_REQUIRE_TERMS", "on")
    tenant, headers = await _account(store)
    first = await _open(client, headers)
    monkeypatch.setattr(policy, "VERSION", "2027-02-01")
    second = await _open(client, {**headers, "x-okwan-client-ip": "192.0.2.66"})
    assert (await _paid(client, tenant.id, first, consent={"terms_of_service": "accepted"},
                        metadata={"tenant_id": tenant.id, "refund_policy": "2026-11-13"})).json()["plan"] == "pro"
    paid, unpaid = await _row(store, tenant.id, first), await _row(store, tenant.id, second)
    assert (paid["policy_version"], paid["ip"]) == ("2026-11-13", "203.0.113.24")
    assert paid["paid_at"] is not None and paid["terms_accepted_at"] is not None
    assert (unpaid["policy_version"], unpaid["ip"], unpaid["paid_at"], unpaid["terms_accepted_at"]) == (
        "2027-02-01", "192.0.2.66", None, None)


async def test_a_second_click_before_stripes_report_lands_does_not_postdate_the_payment(client, store, stripe):
    """Between paying on Stripe's page and Stripe's event arriving the
    account is still free, so Upgrade can be clicked again from elsewhere.
    The paid session's record still says it was opened before it was paid."""
    tenant, headers = await _account(store)
    first = await _open(client, headers)
    paid_at = int(time.time()) + 1
    second = await _open(client, {**headers, "x-okwan-client-ip": "192.0.2.66"})
    assert (await _paid(client, tenant.id, first, created=paid_at)).json()["plan"] == "pro"
    row = await _row(store, tenant.id, first)
    assert row["ip"] == "203.0.113.24" and row["opened_at"] <= row["paid_at"] == datetime.fromtimestamp(paid_at, UTC)
    assert (await _row(store, tenant.id, second))["paid_at"] is None


# ── attacker's pass 3: a payment names its account only through Okwan ──

async def test_a_session_okwan_never_opened_cannot_name_an_account(client, store, stripe):
    """A payment link's `client_reference_id` is a value the buyer writes
    into the address. It named the victim's account: Pro, the buyer's
    customer id on the victim's billing record, and "terms agreed"."""
    victim, headers = await _account(store, "Victim ISV")
    looked = await _open(client, headers)                    # the victim once looked at the price
    r = await _paid(client, victim.id, "cs_live_not_okwans", metadata={}, customer="cus_attacker",
                    subscription="sub_attackers", consent={"terms_of_service": "accepted"})
    assert r.json() == {"received": True}
    assert (await store.get_plan(victim.id))[0] == "free" and await store.billing_for(victim.id) is None
    row = await _row(store, victim.id, looked)
    assert (row["paid_at"], row["subscription_id"], row["terms_accepted_at"]) == (None, None, None)


async def test_a_payment_marks_only_its_own_accounts_session(client, store, stripe):
    """Okwan's metadata names account A; the session id is one account B
    opened. A is paid for; B's record of what it was shown is not touched."""
    a, _ = await _account(store, "Account A")
    b, b_headers = await _account(store, "Account B")
    bs = await _open(client, b_headers)
    assert (await _paid(client, a.id, bs, consent={"terms_of_service": "accepted"})).json()["plan"] == "pro"
    row = await _row(store, b.id, bs)
    assert (row["paid_at"], row["subscription_id"], row["terms_accepted_at"]) == (None, None, None)
    assert await store.checkouts_for(a.id) == [] and (await store.get_plan(b.id))[0] == "free"
    assert not await store.checkout_paid(a.id, bs, at=datetime.now(UTC), subscription_id="s", terms_accepted_at=None)


# ── attacker's pass 4: the page cannot say what the policy does not ────

REAL_PAGE = (ROOT / PAGE).read_text()


def _edited(old: str, new: str) -> str:
    assert REAL_PAGE.count(old) == 1, old
    return REAL_PAGE.replace(old, new)


@pytest.mark.parametrize(("what", "page"), [
    ("a promise the policy does not make, as a list item",
     _edited("<li key={j}>{linked(line)}</li>",
             "<li key={j}>{linked(line)}</li>\n        <li>A payment is refunded in full, for any reason.</li>")),
    ("a paragraph of its own under the introduction",
     _edited("intro={<p>{linked(policy.intro)}</p>}",
             "intro={<><p>{linked(policy.intro)}</p><p>Refunds within 30 days, no questions asked.</p></>}")),
    ("one word taken out of every line",
     _edited("out.push(line.slice(at));", 'out.push(line.slice(at).replace("not ", ""));')),
    ("another date",
     _edited("updated={policy.updated}", 'updated="January 5, 2027"')),
    ("another title, with the real one in a comment",
     _edited("title={policy.title}", 'title="Refunds on request" /* title={policy.title} */')),
    ("words from a second file",
     _edited('import policy from "./policy.json";', 'import policy from "./policy.json";\nimport more from "./more.json";')),
    ("words in a template",
     _edited("<li key={j}>{linked(line)}</li>", "<li key={j}>{`${line} Unless you ask nicely.`}</li>")),
])
def test_a_word_added_to_the_page_is_seen(what, page):
    assert own_words(page), what


def test_the_real_page_passes_the_same_reading():
    assert own_words(REAL_PAGE) == []


def test_a_changed_file_or_changed_words_are_seen():
    """The file edited by hand, and the words changed with the version left alone."""
    softer = policy.page_file().replace("is not refunded, in whole or in part,", "is refunded on request")
    assert softer != policy.page_file() and _digest(softer) != PINNED[policy.VERSION]
    hidden = _read(TERMS).replace("Fees are", "A Pro payment is refunded in full on request within 60 days. Fees are", 1)
    assert _digest(_plans_section(hidden)) != TERMS_PLANS
