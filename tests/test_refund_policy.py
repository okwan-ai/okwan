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

import asyncio
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
from okwan_api.ratelimit import BILLING_TENANT
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

#: Everything each version of the policy says, by digest of policy.said():
#: the page, the words at checkout and beside the tick box, the copy for a
#: bank and the statement of how it was shown. A line here is never
#: edited. New words anywhere are a new version: change VERSION and UPDATED
#: in okwan_api/policy.py, put what said() returned before into its
#: ARCHIVE under the old version, and add the new line.
PINNED = {
    "2026-11-13": "b3730adab829c8c01d18546062a77c05e533d4d719070367546d44edf9d28cfa",
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


def _pin(said: dict[str, str]) -> str:
    """The digest of everything one version says."""
    return _digest(json.dumps(said, sort_keys=True, ensure_ascii=False))


def _versions_are_pinned() -> None:
    """The check itself, so a forged archive can be put through it below."""
    assert _pin(policy.said()) == PINNED[policy.VERSION], (
        "something the policy says has changed: give it a new VERSION (see PINNED above)")
    assert set(PINNED) == {policy.VERSION} | set(policy.ARCHIVE), "every earlier version is kept, whole"
    for version in policy.ARCHIVE:
        kept = policy.said(version)
        assert _pin(kept) == PINNED[version], f"version {version} in the archive is not what was pinned"
        assert policy.text(version) == kept["text"] and f"(version {version})" in kept["text"]


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


def test_the_words_cannot_change_without_the_version(monkeypatch):
    monkeypatch.delenv("OKWAN_SITE_URL", raising=False)
    _versions_are_pinned()
    assert set(policy.said()) == {"page", "plan_page", "at_checkout", "acceptance", "text", "disclosure"}
    assert policy.said()["page"] == _read(PAGE_FILE)


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


def test_the_plan_page_shows_the_policys_own_words_before_the_upgrade_button():
    card = _read("apps/dashboard/app/_components/settings/billing-card.tsx")
    shown, button = card.index("{notice}"), card.index("Upgrade for $")
    assert shown < button, "ahead of the button in the page: beside it on a wide screen, above it on a phone"
    assert "<p data-policy-notice" in card and card.count("data-upgrade") == 2, "what the layout check reads in a browser"
    assert "href={refundsUrl}" in card and "href={termsUrl}" in card
    # The card has none of the policy's words of its own to drift: they come from the API.
    for word in ("refunded", "renews", "okwan.ai/refunds"):
        assert word not in card, word
    page = _read("apps/dashboard/app/settings/page.tsx")
    assert "notice={bill.notice}" in page and "refundsUrl={bill.refunds_url}" in page and "termsUrl={bill.terms_url}" in page
    # And the layout check holds the rendered page to the same sentence.
    check = _read("apps/dashboard/scripts/layout-check.mjs")
    assert f'const PLAN_NOTICE = "{policy.plan_page()} Refunds and cancellation · Terms";' in check
    assert "await checkPlanNotice(page, w, h);" in check
    assert f"for ${policy.PRO_PRICE_USD} a month. Cancel any time;" in check, "the card's own line, read whole in a browser"
    assert f'"Upgrade for ${policy.PRO_PRICE_USD}/month"' in check


def test_what_a_bank_is_told_was_shown_is_what_both_places_show():
    """Second pass: the statement sent to a bank said the plan page shows
    how to cancel. It said that it could be cancelled, not how."""
    told = policy.disclosure()
    for claimed, shown in (("the price", f"{policy.PRO_PRICE_USD} US dollars a month"),
                           ("renews monthly until cancelled", "renews each month until you cancel"),
                           ("how to cancel", "Manage billing"),
                           ("not refunded once its month has started", "not refunded once its month has started")):
        assert claimed in told, claimed
        assert shown in policy.plan_page(), f"the plan page does not show {claimed}"
        assert shown in policy.at_checkout(), f"Stripe's page does not show {claimed}"
    assert "next to the Upgrade button" in told and "beside the pay button" in told


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
        self.delay = 0.0
        self.tag = os.urandom(4).hex()
        self.ended: list[str] = []            # sessions Okwan asked Stripe to end, and Stripe ended
        self.will_not_end: set[str] = set()   # already paid, as Stripe sees it
        self.ends = True

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/expire"):
            session = request.url.path.split("/")[-2]
            if not self.ends or session in self.will_not_end or session in self.ended:    # ended once, as at Stripe
                return httpx.Response(400, json={"error": {"type": "invalid_request_error"}})
            self.ended.append(session)
            return httpx.Response(200, json={"id": session, "status": "expired"})
        self.forms.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        nth = len(self.forms)
        await asyncio.sleep(self.delay)      # Stripe takes its time to answer
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"type": "invalid_request_error"}})
        if request.url.path.endswith("/checkout/sessions"):
            session = f"cs_test_{self.tag}_{nth}"
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


class Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_verification(self, email, link):  # pragma: no cover
        raise AssertionError("not here")

    async def send_notice(self, to, subject, text, html=None, reply_to=None, headers=None):
        self.sent.append({"to": to, "subject": subject, "text": text})


@pytest.fixture
def outbox(monkeypatch):
    from okwan_api import mail

    box = Outbox()
    mail.set_mailer(box)
    monkeypatch.setenv("OKWAN_SUPPORT_ESCALATION_TO", "owner-inbox@okwan.test")
    yield box
    mail._overridden = False
    mail._mailer = None


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
    assert (shown["notice"], shown["refunds_url"], shown["terms_url"]) == (
        policy.plan_page(), "https://okwan.ai/refunds", "https://okwan.ai/terms"), "the plan page's words"


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


async def test_an_account_keeps_only_so_many_unpaid_checkouts_from_one_day(client, store, stripe):
    tenant, headers = await _account(store)
    now = datetime.now(UTC)
    for n in range(billing.MAX_OPEN_CHECKOUTS):          # opened over the day, a few an hour
        await store.open_checkout(tenant.id, f"cs_{stripe.tag}_{n}", policy_version=policy.VERSION,
                                  at=now - timedelta(hours=23) + timedelta(minutes=n), ip="203.0.113.24")
    r = await client.post("/v1/billing/checkout", headers=headers)
    assert r.status_code == 429 and stripe.forms == [], "Stripe is not asked"
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
    stripe.will_not_end.add(first)                       # being paid at Stripe as the second is opened
    monkeypatch.setattr(policy, "VERSION", "2027-02-01")
    second = await _open(client, {**headers, "x-okwan-client-ip": "192.0.2.66"})
    assert (await _paid(client, tenant.id, first, consent={"terms_of_service": "accepted"},
                        metadata={"tenant_id": tenant.id, "refund_policy": "2026-11-13"})).json()["plan"] == "pro"
    paid = await _row(store, tenant.id, first)
    assert (paid["policy_version"], paid["ip"]) == ("2026-11-13", "203.0.113.24")
    assert paid["paid_at"] is not None and paid["terms_accepted_at"] is not None
    # The other was never paid, and paying the first ended it at Stripe.
    assert stripe.ended == [second] and [c["session_id"] for c in await store.checkouts_for(tenant.id)] == [first]


async def test_a_second_click_before_stripes_report_lands_does_not_postdate_the_payment(client, store, stripe):
    """Between paying on Stripe's page and Stripe's event arriving the
    account is still free, so Upgrade can be clicked again from elsewhere.
    The paid session's record still says it was opened before it was paid."""
    tenant, headers = await _account(store)
    first = await _open(client, headers)
    stripe.will_not_end.add(first)                       # paid on Stripe's page; Stripe will not end it
    paid_at = int(time.time()) + 1
    second = await _open(client, {**headers, "x-okwan-client-ip": "192.0.2.66"})
    assert (await _paid(client, tenant.id, first, created=paid_at)).json()["plan"] == "pro"
    row = await _row(store, tenant.id, first)
    assert row["ip"] == "203.0.113.24" and row["opened_at"] <= row["paid_at"] == datetime.fromtimestamp(paid_at, UTC)
    assert second in stripe.ended and [c["session_id"] for c in await store.checkouts_for(tenant.id)] == [first]


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


# ── second pass 1: the record is marked whatever order Stripe's events come in ──

async def _sub(client, tenant_id: str, status: str, kind: str = "customer.subscription.updated") -> httpx.Response:
    return await _send(client, {"type": kind, "data": {"object": {
        "id": "sub_1", "object": "subscription", "customer": "cus_1", "status": status,
        "cancel_at_period_end": False, "metadata": {"tenant_id": tenant_id}}}})


async def _backdate(store, session: str, to: datetime) -> None:
    """Move a checkout's row back in time: all that waiting does."""
    if hasattr(store, "pool"):
        await store.pool.execute("UPDATE billing_checkouts SET opened_at = $2 WHERE session_id = $1", session, to)
    else:
        store._checkouts[session]["opened_at"] = to


async def test_the_paid_session_is_marked_when_the_subscription_is_heard_of_first(client, store, stripe, monkeypatch):
    """Stripe reports a new subscription as `incomplete` until its first
    payment is confirmed, and does not order its events. The guard meant
    for a late retry took the session's event: the account paid and had
    Pro, and the record of the session that took the money stayed blank."""
    monkeypatch.setenv("OKWAN_BILLING_REQUIRE_TERMS", "on")
    tenant, headers = await _account(store)
    session = await _open(client, headers)
    assert (await _sub(client, tenant.id, "incomplete", "customer.subscription.created")).json()["plan"] == "free"
    at = int(time.time()) - 2
    r = await _paid(client, tenant.id, session, consent={"terms_of_service": "accepted"}, created=at)
    assert r.json() == {"received": True, "plan": "pro"}, "a paid session is the first payment: Pro does not wait"
    when = datetime.fromtimestamp(at, UTC)
    row = await _row(store, tenant.id, session)
    assert (row["paid_at"], row["subscription_id"], row["terms_accepted_at"]) == (when, "sub_1", when)
    assert (await _sub(client, tenant.id, "active")).json()["plan"] == "pro"

    # And so it is not an "unpaid" row that the account's next click drops a week on.
    assert (await _ended(client, tenant.id)).json()["plan"] == "free"
    await _backdate(store, session, datetime.now(UTC) - UNPAID_CHECKOUT_KEPT - timedelta(minutes=1))
    later = await _open(client, {**headers, "x-okwan-client-ip": "192.0.2.66"})
    kept = await _row(store, tenant.id, session)
    assert (kept["paid_at"], kept["ip"], kept["subscription_id"]) == (when, "203.0.113.24", "sub_1")
    assert {c["session_id"] for c in await store.checkouts_for(tenant.id)} == {session, later}


async def test_a_session_event_sent_after_the_subscription_ended_marks_the_record_and_not_the_plan(
        client, store, stripe):
    tenant, headers = await _account(store)
    session = await _open(client, headers)
    assert (await _sub(client, tenant.id, "active")).json()["plan"] == "pro"
    assert (await _ended(client, tenant.id)).json()["plan"] == "free"
    assert (await _paid(client, tenant.id, session)).json() == {"received": True}
    assert (await _row(store, tenant.id, session))["paid_at"] is not None
    assert (await store.get_plan(tenant.id))[0] == "free", "a late event does not bring Pro back"


async def test_the_two_events_together_in_either_order_leave_the_record_marked(client, store, stripe):
    tenant, headers = await _account(store)
    for n in range(6):
        sub = f"sub_{n}"
        session = await _open(client, headers)
        events = [
            _paid(client, tenant.id, session, subscription=sub),
            _send(client, {"type": "customer.subscription.updated", "data": {"object": {
                "id": sub, "object": "subscription", "customer": "cus_1", "status": "active",
                "cancel_at_period_end": False, "metadata": {"tenant_id": tenant.id}}}}),
        ]
        await _send(client, {"type": "customer.subscription.created", "data": {"object": {
            "id": sub, "object": "subscription", "customer": "cus_1", "status": "incomplete",
            "metadata": {"tenant_id": tenant.id}}}})
        await asyncio.gather(*(events if n % 2 else reversed(events)))
        row = await _row(store, tenant.id, session)
        assert (row["paid_at"] is not None, row["subscription_id"]) == (True, sub), n
        assert (await store.get_plan(tenant.id))[0] == "pro"
        await _send(client, {"type": "customer.subscription.deleted", "data": {"object": {
            "id": sub, "object": "subscription", "customer": "cus_1", "status": "canceled",
            "metadata": {"tenant_id": tenant.id}}}})


# ── second pass 2: the limit is taken before Stripe is asked ───────────

async def test_a_burst_of_clicks_reaches_stripe_only_as_often_as_the_limit(client, store, stripe):
    """The count of rows was read, then Stripe was asked, then the row was
    written: two hundred requests at once all passed the count."""
    tenant, headers = await _account(store)
    stripe.delay = 0.05
    answers = await asyncio.gather(*(client.post("/v1/billing/checkout", headers=headers) for _ in range(60)))
    opened = [r for r in answers if r.status_code == 200]
    refused = [r for r in answers if r.status_code == 429]
    assert len(opened) == len(stripe.forms) == BILLING_TENANT.limit and len(refused) == 60 - BILLING_TENANT.limit
    assert all(int(r.headers["Retry-After"]) > 0 for r in refused)
    left = await store.checkouts_for(tenant.id)
    assert 1 <= len(left) <= BILLING_TENANT.limit and len(left) + len(stripe.ended) == BILLING_TENANT.limit
    assert left[0]["session_id"] not in stripe.ended, "the newest is never the one ended"


async def test_a_call_stripe_refuses_still_counts(client, store, stripe):
    tenant, headers = await _account(store)
    stripe.status = 429
    codes = [(await client.post("/v1/billing/checkout", headers=headers)).status_code for _ in range(40)]
    assert codes == [502] * BILLING_TENANT.limit + [429] * (40 - BILLING_TENANT.limit)
    assert len(stripe.forms) == BILLING_TENANT.limit and await store.checkouts_for(tenant.id) == []


async def test_the_portal_is_limited_with_it_and_another_account_is_not_held_up(client, store, stripe):
    tenant, headers = await _account(store)
    await store.save_billing(tenant.id, customer_id="cus_x", subscription_id="sub_x", status="canceled",
                             cancel_at_period_end=False, current_period_end=None)
    codes = [(await client.post("/v1/billing/portal", headers=headers)).status_code for _ in range(30)]
    assert codes == [200] * BILLING_TENANT.limit + [429] * (30 - BILLING_TENANT.limit)
    assert (await client.post("/v1/billing/checkout", headers=headers)).status_code == 429, "one allowance for both"
    _, other = await _account(store, "Another account")
    await _open(client, other)


async def test_no_number_of_unpaid_sessions_pushes_a_paid_one_out_of_the_record(store):
    """The list was the newest 500 of both kinds together. An account that
    paid, then opened checkouts in bursts over a few days, had more unpaid
    rows than that, all newer: the paid one was no longer in what the
    store returned."""
    from okwan_vault.usage import CHECKOUTS_LISTED

    tenant = await store.create_tenant("Paying ISV")
    now = datetime.now(UTC).replace(microsecond=0)
    tag = os.urandom(4).hex()
    paid_at = now - timedelta(days=5)
    await store.open_checkout(tenant.id, f"cs_{tag}_paid", policy_version="2026-11-13", at=paid_at, ip="203.0.113.24")
    assert await store.checkout_paid(tenant.id, f"cs_{tag}_paid", at=paid_at, subscription_id="sub_1",
                                     terms_accepted_at=None)
    for n in range(CHECKOUTS_LISTED + 30):
        await store.open_checkout(tenant.id, f"cs_{tag}_{n:04d}", policy_version="2026-11-13",
                                  at=now - timedelta(minutes=CHECKOUTS_LISTED + 30 - n), ip="192.0.2.66")
    listed = await store.checkouts_for(tenant.id)
    assert len(listed) == CHECKOUTS_LISTED + 1 and listed[-1]["session_id"] == f"cs_{tag}_paid"
    assert listed[0]["session_id"] == f"cs_{tag}_{CHECKOUTS_LISTED + 29:04d}", "still newest first"


# ── second pass 3: one account, one payable checkout ──────────────────

async def test_opening_another_checkout_ends_the_one_before_it(client, store, stripe):
    """A second tab or a second click left two pages at Stripe that could
    each take a card for a day: two subscriptions, a month charged twice."""
    tenant, headers = await _account(store)
    first = await _open(client, headers)
    second = await _open(client, headers)
    assert stripe.ended == [first], "the earlier page can no longer take a payment"
    assert [c["session_id"] for c in await store.checkouts_for(tenant.id)] == [second]
    third = await _open(client, headers)
    assert stripe.ended == [first, second]
    assert [c["session_id"] for c in await store.checkouts_for(tenant.id)] == [third]


async def test_a_session_stripe_will_not_end_keeps_its_record(client, store, stripe):
    """It is being paid, or Stripe did not answer: the row stays, for the
    payment that may be on its way."""
    tenant, headers = await _account(store)
    first = await _open(client, headers)
    stripe.ends = False
    second = await _open(client, headers)
    assert stripe.ended == [] and {c["session_id"] for c in await store.checkouts_for(tenant.id)} == {first, second}


async def test_a_payment_ends_every_other_session_the_account_has_open(client, store, stripe):
    tenant, headers = await _account(store)
    stripe.ends = False
    a, b, c = [await _open(client, headers) for _ in range(3)]
    stripe.ends = True
    assert (await _paid(client, tenant.id, b)).json()["plan"] == "pro"
    assert sorted(stripe.ended) == sorted([a, c])
    left = await store.checkouts_for(tenant.id)
    assert [x["session_id"] for x in left] == [b] and left[0]["paid_at"] is not None


async def test_a_second_subscription_does_not_take_over_the_record_and_the_operator_is_told(
        client, store, stripe, outbox):
    """Both pages were paid before either was heard of. The record keeps
    the first subscription, the one Manage billing opens; the second's
    events change nothing; and a person is told to cancel and refund it."""
    tenant, headers = await _account(store)
    stripe.ends = False                                  # Stripe ended neither in time
    a = await _open(client, headers)
    b = await _open(client, headers)
    assert (await _paid(client, tenant.id, a, customer="cus_A", subscription="sub_A")).json()["plan"] == "pro"
    r = await _paid(client, tenant.id, b, customer="cus_B", subscription="sub_B")
    assert r.json() == {"received": True, "plan": "pro", "second_subscription": True}
    bill = await store.billing_for(tenant.id)
    assert (bill["customer_id"], bill["subscription_id"], bill["status"]) == ("cus_A", "sub_A", "active")
    # Both payments are on record, each under the session that took it.
    assert {c["session_id"]: c["subscription_id"] for c in await store.checkouts_for(tenant.id)} == {a: "sub_A", b: "sub_B"}

    def second(kind: str, status: str) -> dict:
        return {"type": kind, "data": {"object": {"id": "sub_B", "object": "subscription", "customer": "cus_B",
                                                  "status": status, "metadata": {"tenant_id": tenant.id}}}}
    assert (await _send(client, second("customer.subscription.created", "active"))).json()["plan"] == "pro"
    assert (await _send(client, second("customer.subscription.updated", "active"))).json()["plan"] == "pro"
    [mail] = outbox.sent
    assert mail["to"] == "owner-inbox@okwan.test" and mail["subject"] == "[Billing] An account has paid for Pro twice"
    for said in (tenant.id, "Kept:     sub_A", "Second:   sub_B, Stripe customer cus_B", "refund its payment in full"):
        assert said in mail["text"], said

    # The operator cancels the second in Stripe: the account stays on Pro, through the first.
    assert (await _send(client, second("customer.subscription.deleted", "canceled"))).json()["plan"] == "pro"
    bill = await store.billing_for(tenant.id)
    assert (bill["customer_id"], bill["subscription_id"], bill["status"]) == ("cus_A", "sub_A", "active")
    assert (await store.get_plan(tenant.id))[0] == "pro"
    assert (await client.post("/v1/billing/portal", headers=headers)).status_code == 200
    assert stripe.forms[-1]["customer"] == "cus_A", "Manage billing opens the subscription that is kept"


async def test_an_old_subscription_ending_late_does_not_end_the_one_that_replaced_it(client, store, stripe):
    tenant, _ = await _account(store)

    def sub(n: int, kind: str, status: str) -> dict:
        return {"type": kind, "data": {"object": {"id": f"sub_{n}", "object": "subscription", "customer": "cus_1",
                                                  "status": status, "metadata": {"tenant_id": tenant.id}}}}
    assert (await _send(client, sub(1, "customer.subscription.updated", "active"))).json()["plan"] == "pro"
    assert (await _send(client, sub(1, "customer.subscription.updated", "canceled"))).json()["plan"] == "free"
    assert (await _send(client, sub(2, "customer.subscription.updated", "active"))).json()["plan"] == "pro"
    # Stripe sends the first one's end again, late.
    assert (await _send(client, sub(1, "customer.subscription.deleted", "canceled"))).json()["plan"] == "pro"
    assert (await store.billing_for(tenant.id))["subscription_id"] == "sub_2"
    # The one on record ending is still an end.
    assert (await _send(client, sub(2, "customer.subscription.deleted", "canceled"))).json()["plan"] == "free"


# ── second pass 4: two older faults on the same paths ─────────────────

async def test_a_signature_no_one_could_have_made_is_refused_not_an_error(client, store, stripe):
    """A header arrives decoded, and a character outside ASCII in it made
    the comparison raise: anyone could draw a 500 from the webhook."""
    stamp = int(time.time())
    for forged in (f"t={stamp},v1=é", f"t={stamp},v1=\u2603", f"t={stamp},v1=", f"t={stamp}", "v1=00", ""):
        assert billing.verify_signature(b"{}", forged, SECRET) is False, forged
    r = await client.post("/v1/billing/webhook", content=b"{}",
                          headers={"Stripe-Signature": f"t={stamp},v1=é".encode("latin-1")})
    assert r.status_code == 400


async def test_instances_starting_together_all_start():
    """Four at once, on a database with nothing in it and then on a current
    one. Postgres aborted one of two schema runs side by side ("tuple
    concurrently updated", a deadlock, a duplicate); only a deadlock was
    tried again."""
    if not DSN:
        pytest.skip("no vault database configured")
    import asyncpg

    name = "start_race_" + os.urandom(4).hex()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE SCHEMA {name}")
    scoped = f"{DSN}?search_path={name}"
    try:
        for _ in range(3):
            started = await asyncio.gather(
                *(PostgresStore(scoped, EnvMasterKey(new_key())).connect() for _ in range(4)), return_exceptions=True)
            for one in started:
                if not isinstance(one, BaseException):
                    await one.close()
            assert [repr(one) for one in started if isinstance(one, BaseException)] == []
    finally:
        await admin.execute(f"DROP SCHEMA {name} CASCADE")
        await admin.close()


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


@pytest.mark.parametrize(("where", "now"), [
    ("plan_page", "Not for you? Tell us within 30 days and we refund the payment in full."),
    ("at_checkout", "A payment is refunded in full if you ask within 30 days."),
    ("acceptance", "I agree to the Terms, and I give up any right to dispute a payment"),
    ("disclosure", "Before paying, the customer ticked a box agreeing to the policy."),
    ("text", "By paying, the customer agreed never to ask their bank to reverse a payment."),
])
def test_words_changed_anywhere_the_policy_is_said_are_seen(monkeypatch, where, now):
    """Second pass: only the page's file was pinned. The words beside the
    pay button, beside the tick box, sent to a bank and said of how it was
    shown could all change with the version, and every test, unmoved."""
    monkeypatch.delenv("OKWAN_SITE_URL", raising=False)
    monkeypatch.setattr(policy, where, lambda *_: now)
    assert policy.said()[where] == now
    with pytest.raises(AssertionError, match="give it a new VERSION"):
        _versions_are_pinned()


def test_an_archived_version_must_be_word_for_word_what_was_pinned(monkeypatch):
    """Second pass: the next change of policy, done as the runbook says,
    with the old version typed into the archive a little differently."""
    monkeypatch.delenv("OKWAN_SITE_URL", raising=False)
    before = policy.said()
    monkeypatch.setattr(policy, "VERSION", "2027-02-01")
    monkeypatch.setattr(policy, "UPDATED", "February 1, 2027")
    monkeypatch.setitem(PINNED, "2027-02-01", _pin(policy.said()))
    monkeypatch.setattr(policy, "ARCHIVE", {"2026-11-13": before})
    _versions_are_pinned()                                # done honestly, it passes
    assert policy.text("2026-11-13") == before["text"] and policy.said("2026-11-13") == before
    forged = {**before, "text": before["text"].replace("is not refunded, in whole or in part, once its month has "
                                                       "started", "is not refunded for any reason")}
    assert forged != before
    monkeypatch.setattr(policy, "ARCHIVE", {"2026-11-13": forged})
    with pytest.raises(AssertionError, match="is not what was pinned"):
        _versions_are_pinned()


def test_a_changed_file_or_changed_words_are_seen():
    """The file edited by hand, and the words changed with the version left alone."""
    softer = policy.page_file().replace("is not refunded, in whole or in part,", "is refunded on request")
    assert softer != policy.page_file() and _pin({**policy.said(), "page": softer}) != PINNED[policy.VERSION]
    hidden = _read(TERMS).replace("Fees are", "A Pro payment is refunded in full on request within 60 days. Fees are", 1)
    assert _digest(_plans_section(hidden)) != TERMS_PLANS
