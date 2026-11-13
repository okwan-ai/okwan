"""A dispute on a payment for Okwan Pro is known the hour it arrives,
measured against the payments around it, and answered from what is
already put together.

What must hold:

- Stripe's signed events are kept: each payment, each dispute, the inquiry
  before one, each bank's early fraud warning. The newest event wins
  whatever order they arrive in, an ended state is not undone by another
  event of the same second, and an event that cannot be read is
  acknowledged, never a crash;
- the Stripe account takes payments for other products too. A payment is
  Okwan's when its customer is linked to an Okwan account; another is kept
  a week and forgotten. A dispute on a charge that is not a known Okwan
  payment is kept and mailed, said to be unmatched, counted apart, and
  given no evidence;
- the operator is mailed what they have not heard, decided from the
  dispute's state and not from which event arrived: that it exists, that
  an inquiry became a dispute, that it was decided or reopened, and, for
  an unanswered one, once a day inside three days of its date. Evidence
  saved in Stripe and not sent is not an answer. A mail that is not
  accepted is sent on the next round; of two events or two rounds at once
  one mails;
- the rate is Okwan's disputes in the last 30 days against Okwan's
  payments in them; an inquiry is not a dispute; fraud warnings are
  counted with disputes for Visa's line;
- the evidence is what the record shows, in the fields of Stripe's form,
  and says what it does not know: no checkout on record means no policy
  is claimed, two payments days apart are not called two months, an
  address is given only if it is one, and the customer's own requests are
  read oldest first whatever came after them;
- only an unlocked operator sees any of it, and each look is logged.

Stripe and the mail provider are fakes. Both stores. Every id carries
this run's own tag, and nothing another run wrote is read or deleted.
The sections marked "attacker's pass" keep what one found.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from okwan_api import disputes, policy
from okwan_api.main import app
from okwan_api.ratelimit import address_from
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, accounts, new_key
from okwan_vault import access as rules
from okwan_vault.accounts import SESSION_PREFIX, new_token
from okwan_vault.usage import DISPUTE_NOTICE_HELD, UNMATCHED_PAYMENT_KEPT, dispute_heard

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
SECRET = "whsec_test_billing_secret"
INBOX = "owner-inbox@okwan.test"
NOW = datetime.now(UTC).replace(microsecond=0)


def ts(at: datetime) -> int:
    return int(at.timestamp())


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    tag = os.urandom(4).hex()
    if request.param == "memory":
        s = MemoryStore(EnvMasterKey(new_key()))
        s.tag = tag
        yield s
        return
    if not DSN:
        pytest.skip("no vault database configured")
    s = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    s.tag = tag
    created: list[str] = []
    s.made = created
    create = s.create_tenant

    async def tracked(name, parent_id=None):
        tenant = await create(name, parent_id=parent_id)
        created.append(tenant.id)
        return tenant

    s.create_tenant = tracked
    # The database is shared with other runs. What the rounds and the rate
    # read across accounts is narrowed to this run's own rows, by its tag.
    listed, due = s.disputes, s.disputes_to_settle

    async def mine(*a, **k):
        return [d for d in await listed(*a, **k) if tag in d["dispute_id"]]

    async def mine_due(*a, **k):
        return [d for d in await due(*a, **k) if tag in d["dispute_id"]]

    async def my_counts(since, until):
        like = f"%{tag}%"
        row = await s.pool.fetchrow(
            "SELECT (SELECT count(*) FROM billing_payments WHERE okwan AND charge_id LIKE $3 AND paid_at >= $1 "
            "AND paid_at < $2) AS payments, "
            "count(*) FILTER (WHERE okwan AND kind = 'dispute' AND disputed_at >= $1 AND disputed_at < $2) AS disputes, "
            "count(*) FILTER (WHERE okwan AND kind = 'dispute' AND disputed_at IS NULL AND opened_at >= $1) AS inquiries, "
            "count(*) FILTER (WHERE okwan AND kind = 'warning' AND opened_at >= $1) AS warnings, "
            "count(*) FILTER (WHERE NOT okwan AND COALESCE(disputed_at, opened_at) >= $1 "
            "AND COALESCE(disputed_at, opened_at) < $2) AS unmatched "
            "FROM billing_disputes WHERE dispute_id LIKE $3 AND opened_at < $2", since, until, like)
        return {k: int(v) for k, v in dict(row).items()}

    s.disputes, s.disputes_to_settle, s.dispute_counts = mine, mine_due, my_counts
    try:
        yield s
    finally:
        await s.pool.execute("DELETE FROM billing_disputes WHERE dispute_id LIKE $1", f"%{tag}%")
        await s.pool.execute("DELETE FROM billing_payments WHERE charge_id LIKE $1", f"%{tag}%")
        await s.pool.execute("DELETE FROM billing_customers WHERE customer_id LIKE $1", f"%{tag}%")
        for tenant_id in reversed(created):
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await s.pool.execute("DELETE FROM operator_factors WHERE email LIKE $1", f"ops-{tag}%")
        await s.close()


def test_the_narrowed_counts_are_the_stores_own_query():
    """The fixture's count for a shared database is the store's query with
    this run's tag added, so the rate tested on Postgres is the rate shipped."""
    import inspect

    shipped = " ".join(inspect.getsource(PostgresStore.dispute_counts).split())
    for clause in ("WHERE okwan AND kind = 'dispute' AND disputed_at >= $1 AND disputed_at < $2) AS disputes",
                   "WHERE okwan AND kind = 'dispute' AND disputed_at IS NULL AND opened_at >= $1) AS inquiries",
                   "WHERE okwan AND kind = 'warning' AND opened_at >= $1) AS warnings",
                   ("WHERE NOT okwan AND COALESCE(disputed_at, opened_at) >= $1 AND COALESCE(disputed_at, "
                    "opened_at) < $2) AS unmatched"), "FROM billing_disputes WHERE opened_at < $2",
                   "FROM billing_payments WHERE okwan AND paid_at >= $1 AND paid_at < $2) AS payments"):
        assert clause.replace('"', "").replace("  ", " ") in shipped.replace('" "', "").replace('"', ""), clause


class Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.refuse = False
        self.delay = 0.0

    async def send_verification(self, email, link):  # pragma: no cover
        raise AssertionError("not here")

    async def send_notice(self, to, subject, text, html=None, reply_to=None, headers=None):
        await asyncio.sleep(self.delay)
        if self.refuse:
            raise RuntimeError("provider said no")
        self.sent.append({"to": to, "subject": subject, "text": text})


@pytest.fixture
def outbox(monkeypatch):
    from okwan_api import mail

    box = Outbox()
    mail.set_mailer(box)
    monkeypatch.setenv("OKWAN_SUPPORT_ESCALATION_TO", INBOX)
    monkeypatch.setenv("OKWAN_BILLING_STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("OKWAN_DASHBOARD_URL", "https://app.okwan.test")
    monkeypatch.delenv("OKWAN_SITE_URL", raising=False)
    yield box
    mail._overridden = False
    mail._mailer = None


@pytest.fixture
async def client(store, outbox):
    from okwan_api.auth import set_store

    set_store(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        yield c


async def send(client, kind: str, obj, created: datetime | None = None, signed: bool = True) -> httpx.Response:
    body = json.dumps({"type": kind, "created": ts(created or NOW), "data": {"object": obj}}).encode()
    stamp = int(time.time())
    sig = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return await client.post("/v1/billing/webhook", content=body, headers={
        "Stripe-Signature": f"t={stamp},v1={sig if signed else '0' * 64}", "Content-Type": "application/json"})


class Paying:
    """An account and its Stripe customer, with ids that carry this run's tag."""

    def __init__(self, tenant, email, tag):
        self.tenant, self.email, self.tag, self.customer = tenant, email, tag, f"cus_{tag}"

    def charge(self, n: int = 1) -> str:
        return f"ch_{self.tag}_{n}"

    def dp(self, n: int = 1) -> str:
        return f"dp_{self.tag}_{n}"


async def paying(store, name: str = "Acme ISV", *, checkout: bool = True, linked: bool = True,
                 paid_at: datetime | None = None, ip: str = "203.0.113.24") -> Paying:
    """A paying account: linked to its Stripe customer, with a paid checkout on record."""
    tag = f"{store.tag}{os.urandom(2).hex()}"
    tenant = await store.create_tenant(name)
    email = f"founder-{tag}@acme.test"
    await store.add_account(tenant.id, email, accounts.hash_password("correct horse battery"))
    if linked:
        await store.save_billing(tenant.id, customer_id=f"cus_{tag}", subscription_id=f"sub_{tag}", status="active",
                                 cancel_at_period_end=False, current_period_end=NOW + timedelta(days=20))
    if checkout:
        opened = (paid_at or NOW - timedelta(days=10)) - timedelta(minutes=3)
        await store.open_checkout(tenant.id, f"cs_{tag}", policy_version=policy.VERSION, at=opened, ip=ip)
        await store.checkout_paid(tenant.id, f"cs_{tag}", at=opened + timedelta(minutes=3),
                                  subscription_id=f"sub_{tag}", terms_accepted_at=opened + timedelta(minutes=3))
    return Paying(tenant, email, tag)


def charge(who: Paying, n: int = 1, at: datetime | None = None, **over) -> dict:
    return {"id": who.charge(n), "object": "charge", "amount": 9900, "currency": "usd", "paid": True,
            "customer": who.customer, "created": ts(at or NOW - timedelta(days=10)), **over}


def dispute(who: Paying, n: int = 1, *, status: str = "needs_response", reason: str = "subscription_canceled",
            due: datetime | None = None, saved: bool = False, sent: int = 0, opened: datetime | None = None,
            **over) -> dict:
    """A dispute as Stripe sends it. `saved`: evidence staged in Stripe; `sent`: times it was submitted."""
    return {"id": who.dp(n), "object": "dispute", "amount": 9900, "currency": "usd", "charge": who.charge(n),
            "reason": reason, "status": status, "created": ts(opened or NOW),
            "evidence_details": {"due_by": ts(due or NOW + timedelta(days=9, hours=2)), "has_evidence": saved,
                                 "past_due": False, "submission_count": sent}, **over}


async def disputed(client, store, **kw) -> Paying:
    """An account whose payment is on record and has just been disputed."""
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.created", dispute(who, **kw))
    return who


# ── small things ─────────────────────────────────────────────────────

def test_days_left_counts_whole_days_and_goes_negative():
    assert disputes.days_left(NOW + timedelta(days=9, hours=2), NOW) == 9
    assert disputes.days_left(NOW + timedelta(hours=23), NOW) == 0
    assert disputes.days_left(NOW - timedelta(hours=1), NOW) == -1
    assert disputes.days_left(None, NOW) is None


def test_amounts_are_minor_units_and_read_as_money():
    assert disputes.amount({"amount_minor": 9900, "currency": "usd"}) == "$99.00"
    assert disputes.amount({"amount_minor": 12345, "currency": "eur"}) == "123.45 EUR"
    assert disputes.amount({"amount_minor": None, "currency": None}) == "an amount Stripe did not give"
    assert (disputes.percent(833), disputes.percent(50), disputes.percent(7)) == ("8.33%", "0.50%", "0.07%")


def test_every_reason_names_only_fields_the_evidence_gives():
    for reason in (*disputes.REASONS.values(), disputes.OTHER_REASON):
        assert set(reason["fields"]) <= set(disputes.FIELDS)
    assert f"{policy.PRO_PRICE_USD} US dollars a month" in disputes.PRODUCT


def test_the_recorded_address_of_a_caller_has_to_be_an_address(monkeypatch):
    """Attacker's pass: with the dashboard's secret, whatever was in the
    header was stored as the caller's address, and went from there into
    an answer to a bank. A value that is not an address is not taken."""
    monkeypatch.setenv("OKWAN_DASHBOARD_SECRET", "s3cret-for-this-test")
    vouched = {"x-okwan-dashboard-secret": "s3cret-for-this-test"}
    assert address_from({**vouched, "x-okwan-client-ip": "203.0.113.24"}, "10.0.0.1") == "203.0.113.24"
    odd = "the cardholder's own address, see attachment"
    assert address_from({**vouched, "x-okwan-client-ip": odd}, "10.0.0.1") == "10.0.0.1"
    assert address_from({**vouched, "x-okwan-client-ip": odd, "cf-connecting-ip": "198.51.100.9"}, None) == "198.51.100.9"
    assert address_from({"x-forwarded-for": "not an address"}, "10.0.0.1") == "10.0.0.1"
    assert address_from({"x-forwarded-for": "198.51.100.7"}, "10.0.0.1") == "198.51.100.7"


def test_an_address_with_a_zone_after_it_is_not_an_address(monkeypatch):
    """Second attacker's pass: the rule was "whatever Python's parser
    takes", and it takes anything after a percent sign as an IPv6 zone,
    spaces and sentences included. "::1%<words>" was recorded as the
    caller's address and given to a bank as where the customer paid from."""
    words = "::1%an address Okwan has confirmed is not the cardholder's. Okwan accepts this dispute"
    monkeypatch.setenv("OKWAN_DASHBOARD_SECRET", "s3cret-for-this-test")
    vouched = {"x-okwan-dashboard-secret": "s3cret-for-this-test"}
    for headers in ({"cf-connecting-ip": words}, {"x-forwarded-for": f"1.1.1.1, {words}"},
                    {**vouched, "x-okwan-client-ip": words}, {"cf-connecting-ip": "fe80::1%eth0"},
                    {"cf-connecting-ip": "::1%" + "A" * 50_000}):
        assert address_from(headers, "10.0.0.9") == "10.0.0.9", str(headers)[:80]
    assert disputes._address(words) is None and disputes._address("fe80::1%x") is None
    assert address_from({"cf-connecting-ip": " 2001:DB8:0:0::1 "}, None) == "2001:db8::1", "one spelling of one address"
    assert max(len(disputes._address(a) or "") for a in ("::ffff:255.255.255.255", "ffff:" * 7 + "ffff")) <= 45


# ── kept from Stripe's events ────────────────────────────────────────

async def test_a_payment_taken_is_kept_under_its_account(client, store):
    who = await paying(store)
    assert (await send(client, "charge.succeeded", charge(who))).json() == {"received": True, "payment": who.charge()}
    paid = await store.payment(who.charge())
    assert (paid["tenant_id"], paid["customer_id"], paid["okwan"], paid["amount_minor"], paid["currency"]) == (
        who.tenant.id, who.customer, True, 9900, "usd")
    assert paid["paid_at"] == NOW - timedelta(days=10)
    await send(client, "charge.succeeded", charge(who, amount=1))          # sent again, with other figures
    assert (await store.payment(who.charge()))["amount_minor"] == 9900


async def test_another_products_payment_is_kept_a_week_unmatched_and_then_forgotten(client, store, outbox):
    """The Stripe account takes payments for other products, and Stripe
    sends this endpoint all of them."""
    other = Paying(None, "", f"{store.tag}{os.urandom(2).hex()}")           # a customer no Okwan account is linked to
    old = NOW - UNMATCHED_PAYMENT_KEPT - timedelta(hours=1)
    await send(client, "charge.succeeded", charge(other, 1, old))
    await send(client, "charge.succeeded", charge(other, 2, NOW - timedelta(days=2)))
    ours = await paying(store)
    await send(client, "charge.succeeded", charge(ours, 1, old))
    kept = await store.payment(other.charge(2))
    assert (kept["okwan"], kept["tenant_id"]) == (False, None)
    assert (await disputes.activity(NOW))["payments"] == 1, "Okwan's own, and not the other product's two"
    await disputes.remind(NOW)
    assert await store.payment(other.charge(1)) is None, "a week unmatched: another product's, forgotten"
    assert await store.payment(other.charge(2)) is not None, "inside the week it waits: its account may be linked yet"
    assert (await store.payment(ours.charge(1)))["okwan"] is True, "Okwan's own are kept whatever their age"


async def test_a_dispute_is_kept_and_the_operator_is_mailed_the_account_amount_reason_and_date(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    r = await send(client, "charge.dispute.created", dispute(who))
    assert r.json() == {"received": True, "dispute": who.dp(), "applied": True}
    row = await store.dispute(who.dp())
    assert (row["kind"], row["tenant_id"], row["okwan"], row["charge_id"], row["status"], row["reason"]) == (
        "dispute", who.tenant.id, True, who.charge(), "needs_response", "subscription_canceled")
    assert (row["amount_minor"], row["currency"], row["has_evidence"], row["submitted"], row["told"]) == (
        9900, "usd", False, False, "open")
    assert row["due_by"] == NOW + timedelta(days=9, hours=2)

    [mail] = outbox.sent
    due = NOW + timedelta(days=9, hours=2)
    assert mail["to"] == INBOX
    assert mail["subject"] == f"[Dispute] $99.00 disputed, answer due {due.day} {due:%b %Y}"
    assert "Acme" not in mail["subject"] + mail["text"], "an account's name is never in the mail"
    for said in (f"Account:  {who.tenant.id} (its name and sign-in address are on the page below)", "$99.00, taken",
                 who.charge(),
                 "subscription_canceled. The cardholder says they were charged after cancelling.",
                 "After that date it is lost, and an answer can be sent once.",
                 f"https://app.okwan.test/ops/disputes/{who.dp()}",
                 ("Okwan's payments, last 30 days: 1 disputed, 0 inquiries not yet a dispute and 0 flagged for "
                  "fraud, against 1 payment: 100.00% disputed"),
                 "The card networks measure the whole Stripe account, other products included"):
        assert said in mail["text"], said
    assert who.email not in mail["text"], "the sign-in address is on the console page, not in the mail"


async def test_a_dispute_on_a_charge_that_is_not_an_okwan_payment_is_kept_apart(client, store, outbox):
    """Another product's on the same Stripe account, or an Okwan payment
    from before the record began. Said so, counted apart, no evidence."""
    other = Paying(None, "", f"{store.tag}{os.urandom(2).hex()}")
    ours = await disputed(client, store)
    await send(client, "charge.dispute.created", dispute(other))
    row = await store.dispute(other.dp())
    assert (row["okwan"], row["tenant_id"], row["told"]) == (False, None, "open:unmatched")
    mail = outbox.sent[-1]
    assert "Account:  not matched to an Okwan payment (it may be another product's on this Stripe account)" in mail["text"]
    assert "Okwan holds no evidence for a payment it cannot match:" in mail["text"]
    act = await disputes.activity(NOW)
    assert (act["disputes"], act["unmatched"], act["payments"]) == (1, 1, 1), "only Okwan's own is in the rate"
    assert "1 more is on this Stripe account and not matched to an Okwan payment: not counted here." in (
        disputes.activity_line(act))
    pack = await disputes.evidence(row, NOW)
    assert pack["matched"] is False and pack["policy_version"] is None and pack["characters"] == 0
    assert all(field["text"] is None for field in pack["fields"].values())
    assert pack["fields"]["customer_name"]["add"] == disputes.UNMATCHED
    assert (await disputes.evidence(await store.dispute(ours.dp()), NOW))["matched"] is True


async def test_a_payment_that_arrived_before_its_customer_was_linked_still_finds_its_account(client, store, outbox):
    who = await paying(store, linked=False, checkout=False)
    await send(client, "charge.succeeded", charge(who))
    assert (await store.payment(who.charge()))["okwan"] is False, "Stripe's checkout event had not landed yet"
    await store.save_billing(who.tenant.id, customer_id=who.customer, subscription_id="sub_1", status="active",
                             cancel_at_period_end=False, current_period_end=None)
    await send(client, "charge.dispute.created", dispute(who))
    row = await store.dispute(who.dp())
    assert (row["tenant_id"], row["okwan"]) == (who.tenant.id, True)
    assert (await store.payment(who.charge()))["okwan"] is True


async def test_a_banks_early_warning_is_kept_apart_from_disputes(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    warning = {"id": f"issfr_{who.tag}", "object": "radar.early_fraud_warning", "charge": who.charge(),
               "fraud_type": "made_with_stolen_card", "actionable": True, "created": ts(NOW)}
    assert (await send(client, "radar.early_fraud_warning.created", warning)).json()["applied"] is True
    row = await store.dispute(f"issfr_{who.tag}")
    assert (row["kind"], row["status"], row["reason"], row["amount_minor"], row["due_by"]) == (
        "warning", "flagged", "made_with_stolen_card", 9900, None)
    [mail] = outbox.sent
    assert mail["subject"] == "[Payment flagged] A bank has flagged a $99.00 payment as possible fraud"
    assert "No money has moved and nothing has to be answered." in mail["text"]
    act = await disputes.activity(NOW)
    assert (act["disputes"], act["warnings"], act["payments"], act["rate"], act["with_warnings"]) == (
        0, 1, 1, "0.00%", "100.00%"), "not a dispute, and counted with them for Visa's line"
    assert await disputes.remind(NOW + timedelta(days=2)) == 0, "told once"


@pytest.mark.parametrize("obj", [
    {}, {"id": "dp_x"}, {"charge": "ch_x"}, {"id": 7, "charge": ["ch_x"]}, {"id": "", "charge": "ch_x"},
    {"id": "dp_" + "x" * 400, "charge": "ch_x"}, {"id": None, "charge": None, "evidence_details": "soon"},
    {"id": "dp_x\u0000y", "charge": "ch_x"}, {"id": "dp_x", "charge": "ch_\u0000"}, {"id": "dp x", "charge": "ch_x"},
    "a string", ["a", "list"], 7, None,
])
async def test_an_event_that_cannot_be_read_is_acknowledged_and_kept_out(client, store, outbox, obj):
    """Attacker's pass: an object that was not a mapping drew a 500, and a
    NUL in an id was an error on Postgres and stored by the other store."""
    for kind in ("charge.dispute.created", "charge.succeeded", "radar.early_fraud_warning.created"):
        r = await send(client, kind, obj)
        assert (r.status_code, r.json()) == (200, {"received": True}), kind
    assert await store.disputes() == [] and outbox.sent == []


async def test_odd_values_in_a_readable_event_do_not_break_it(client, store, outbox):
    who = await paying(store)
    odd = dispute(who, reason="Not\u0000 a reason", amount="9900", currency=7, status=None,
                  evidence_details={"due_by": "tomorrow", "has_evidence": "yes", "submission_count": "1"}, created="then")
    assert (await send(client, "charge.dispute.created", odd)).json()["applied"] is True
    row = await store.dispute(who.dp())
    assert (row["amount_minor"], row["currency"], row["reason"], row["status"], row["due_by"], row["has_evidence"],
            row["submitted"]) == (None, None, None, "needs_response", None, False, False)
    [mail] = outbox.sent
    assert "an amount Stripe did not give" in mail["subject"] and "no date given by Stripe" in mail["text"]


async def test_nothing_from_an_event_is_written_to_the_log(client, store, outbox, caplog):
    """CodeQL, on the pull request: the line that says an event was not
    kept wrote the event's type, which is text from the request, and a
    type can carry a line break and a made-up log line after it. Only
    Stripe can sign one; the log says it in Okwan's own words all the same."""
    forged = "charge.dispute.created\n2026-11-13 ERROR okwan.vault: master key rotated by ops@okwan.test"
    with caplog.at_level("WARNING", logger="okwan.disputes"):
        r = await send(client, forged, {"id": "not an id", "charge": "nor this"})
        r2 = await send(client, "radar.early_fraud_warning.created\nforged", {"id": 7})
    assert r.json() == r2.json() == {"received": True}
    said = [record.getMessage() for record in caplog.records if record.name == "okwan.disputes"]
    assert said == ["disputes: a dispute event without a readable id or charge, not kept",
                    "disputes: an early fraud warning without a readable id or charge, not kept"]
    assert await store.disputes() == [] and outbox.sent == []


async def test_an_unsigned_event_keeps_nothing(client, store, outbox):
    who = await paying(store)
    assert (await send(client, "charge.dispute.created", dispute(who), signed=False)).status_code == 400
    assert (await send(client, "charge.succeeded", charge(who), signed=False)).status_code == 400
    assert await store.disputes() == [] and await store.payment(who.charge()) is None and outbox.sent == []


async def signed_up(store, email: str | None = None) -> Paying:
    """An account made the way sign-up makes one: the verification link's
    `complete_signup` names it, by its sign-in address."""
    tag = f"{store.tag}{os.urandom(2).hex()}"
    email = email.format(tag=tag) if email else f"jane-{tag}@acme.test"
    _, token_hash = new_token("vt")
    await store.add_signup(email, accounts.hash_password("correct horse battery"), token_hash,
                           datetime.now(UTC) + timedelta(hours=1))
    tenant = await store.complete_signup(token_hash)
    if hasattr(store, "pool"):
        store.made.append(tenant.id)
    await store.save_billing(tenant.id, customer_id=f"cus_{tag}", subscription_id=f"sub_{tag}", status="active",
                             cancel_at_period_end=False, current_period_end=NOW + timedelta(days=20))
    return Paying(tenant, email, tag)


@pytest.mark.parametrize("email", [None, "pay.example.com/r-{tag}@evil.test"])
async def test_the_operators_mail_names_an_account_by_its_id_and_nothing_a_customer_wrote(client, store, outbox, email):
    """Two attacker's passes. First: the sign-in address was printed raw,
    and one can be shaped to read as a web address. Second: the address
    was taken out and the account's name left in, cleaned as its own, but
    an account made by signing up is named by its sign-in address, so the
    mail still printed it, dots and all. Neither is in the mail now."""
    who = await signed_up(store, email)
    assert who.tenant.name == who.email, "sign-up names the account by its address"
    assert re.fullmatch(accounts.EMAIL_PATTERN, who.email), "an address the sign-up form takes"
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.created", dispute(who))
    [mail] = outbox.sent
    line = next(text for text in mail["text"].split("\n") if text.startswith("Account:"))
    assert line == f"Account:  {who.tenant.id} (its name and sign-in address are on the page below)"
    every = mail["subject"] + mail["text"]
    assert who.email not in every and who.email.split("@")[0] not in every and "example.com" not in every


async def test_a_name_an_operator_gave_an_account_is_not_in_the_mail_either(client, store, outbox):
    who = await paying(store, "Acme\nBcc: thief@evil.test\nhttps://evil.test/pay now")
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.created", dispute(who))
    [mail] = outbox.sent
    every = mail["subject"] + mail["text"]
    assert "Acme" not in every and "evil.test" not in every and "Bcc" not in every
    assert who.email not in every and "founder-" not in every, "the sign-in address is not in the mail"


# ── attacker's pass: the order of events, and two at once ────────────

async def test_the_newest_event_wins_whatever_order_they_arrive_in(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    answered = dispute(who, status="under_review", saved=True, sent=1)
    assert (await send(client, "charge.dispute.updated", answered, NOW + timedelta(hours=5))).json()["applied"] is True
    assert (await send(client, "charge.dispute.created", dispute(who), NOW)).json()["applied"] is False
    row = await store.dispute(who.dp())
    assert (row["status"], row["submitted"]) == ("under_review", True)
    assert len(outbox.sent) == 1, "told once, by the event that arrived first"


async def test_a_decision_is_not_undone_by_another_event_of_the_same_second(client, store, outbox):
    """`created` is in whole seconds. A closing event and an earlier state
    stamped the same second left the dispute open again, and the next
    closing event mailed the decision twice."""
    who = await disputed(client, store)
    at = NOW + timedelta(days=30)
    won = dispute(who, status="won", saved=True, sent=1)
    assert (await send(client, "charge.dispute.closed", won, at)).json()["applied"] is True
    assert (await send(client, "charge.dispute.updated", dispute(who, status="under_review", sent=1), at)
            ).json()["applied"] is False
    row = await store.dispute(who.dp())
    assert row["status"] == "won" and row["closed_at"] is not None
    await send(client, "charge.dispute.funds_reinstated", won, at)
    assert [m["subject"] for m in outbox.sent[1:]] == ["[Dispute won] The $99.00 dispute is closed"]


async def test_two_closing_events_at_once_mail_the_decision_once(client, store, outbox):
    who = await disputed(client, store)
    outbox.delay = 0.05                                    # the mail provider takes its time
    won = dispute(who, status="won", saved=True, sent=1)
    at = NOW + timedelta(days=30)
    await asyncio.gather(send(client, "charge.dispute.closed", won, at),
                         send(client, "charge.dispute.funds_reinstated", won, at + timedelta(seconds=1)),
                         send(client, "charge.dispute.updated", won, at + timedelta(seconds=2)))
    assert [m["subject"] for m in outbox.sent[1:]] == ["[Dispute won] The $99.00 dispute is closed"]


async def test_two_rounds_at_once_mail_a_reminder_once(client, store, outbox):
    due = NOW + timedelta(days=9)
    await disputed(client, store, due=due)
    outbox.delay = 0.05
    sent = await asyncio.gather(*(disputes.remind(due - timedelta(days=2)) for _ in range(4)))
    assert sum(sent) == 1 and len(outbox.sent) == 2, "the first notice, and one reminder"


# ── attacker's pass: the mail that never came, or said the wrong thing ──

async def test_a_dispute_first_heard_of_when_already_decided_is_mailed_as_decided(client, store, outbox):
    """It went out as "[Dispute] disputed, answer due": every dispute
    already closed on the day this shipped would have."""
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.closed", dispute(who, status="lost"))
    [mail] = outbox.sent
    assert mail["subject"] == "[Dispute lost] The $99.00 dispute is closed"
    assert "Okwan first heard of this dispute when it was already decided." in mail["text"]
    assert await disputes.remind(NOW + timedelta(hours=1)) == 0


async def test_a_mail_that_was_not_accepted_is_sent_on_the_next_round_whatever_it_was(client, store, outbox):
    """The first notice, and also the decision: an ended dispute was
    skipped by the round, so a refused "decided" mail was never sent."""
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    outbox.refuse = True
    assert (await send(client, "charge.dispute.created", dispute(who))).json()["applied"] is True
    assert outbox.sent == [] and (await store.dispute(who.dp()))["told"] is None
    outbox.refuse = False
    assert await disputes.remind(NOW + timedelta(hours=1)) == 1
    assert outbox.sent[0]["subject"].startswith("[Dispute] $99.00 disputed") and who.charge() in outbox.sent[0]["text"]
    assert await disputes.remind(NOW + timedelta(hours=2)) == 0, "and then not again"

    outbox.refuse = True
    await send(client, "charge.dispute.closed", dispute(who, status="lost"), NOW + timedelta(days=12))
    assert len(outbox.sent) == 1 and (await store.dispute(who.dp()))["told"] == "open"
    outbox.refuse = False
    assert await disputes.remind(NOW + timedelta(days=12, hours=1)) == 1
    assert outbox.sent[-1]["subject"] == "[Dispute lost] The $99.00 dispute is closed"


async def test_a_dispute_that_ends_before_its_first_mail_got_out_is_still_mailed(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    outbox.refuse = True
    await send(client, "charge.dispute.created", dispute(who))
    await send(client, "charge.dispute.closed", dispute(who, status="lost"), NOW + timedelta(minutes=30))
    outbox.refuse = False
    assert await disputes.remind(NOW + timedelta(hours=1)) == 1
    [mail] = outbox.sent
    assert mail["subject"] == "[Dispute lost] The $99.00 dispute is closed" and "first heard" in mail["text"]


async def test_a_decided_dispute_opened_again_is_mailed(client, store, outbox):
    who = await disputed(client, store)
    await send(client, "charge.dispute.closed", dispute(who, status="won", sent=1), NOW + timedelta(days=20))
    again = NOW + timedelta(days=40)
    await send(client, "charge.dispute.updated", dispute(who, status="needs_response", due=again), NOW + timedelta(days=25))
    assert [m["subject"].split(" ")[0] + " " + m["text"].split("\n")[0] for m in outbox.sent] == [
        "[Dispute] A cardholder's bank has opened a dispute on a payment.",
        "[Dispute The bank has decided: won.",
        "[Dispute] A dispute that was decided has been opened again."]
    row = await store.dispute(who.dp())
    assert (row["status"], row["closed_at"], row["told"]) == ("needs_response", None, "open")


async def test_an_inquiry_is_told_as_one_is_not_a_dispute_in_the_rate_and_is_told_again_when_it_becomes_one(
        client, store, outbox):
    """Stripe's documentation: an inquiry that does not escalate is not
    counted by the card networks, and answering it can settle it."""
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.created", dispute(who, status="warning_needs_response", reason="unrecognized"))
    [mail] = outbox.sent
    assert mail["subject"].startswith("[Inquiry] A bank is asking about a $99.00 payment, answer due")
    assert "an answer, or a full refund, can settle it with no dispute and no fee" in mail["text"]
    act = await disputes.activity(NOW)
    assert (act["disputes"], act["inquiries"], act["rate"]) == (0, 1, "0.00%")
    assert [line["over"] for line in act["lines"]] == [False, False]

    await send(client, "charge.dispute.updated", dispute(who, status="needs_response", reason="unrecognized"),
               NOW + timedelta(days=5))
    assert outbox.sent[-1]["text"].startswith("The bank's inquiry has become a dispute.")
    act = await disputes.activity(NOW + timedelta(days=5))
    assert (act["disputes"], act["inquiries"], act["rate"]) == (1, 0, "100.00%")


# ── attacker's pass: saved in Stripe is not sent ─────────────────────

async def test_evidence_saved_in_stripe_and_not_sent_is_still_unanswered(client, store, outbox, monkeypatch):
    """`has_evidence` is evidence staged on Stripe's form. It was read as
    "answered": the reminders stopped, the console said "Answered, with
    the bank", and the date passed."""
    due = NOW + timedelta(days=9)
    who = await disputed(client, store, due=due)
    await send(client, "charge.dispute.updated", dispute(who, due=due, saved=True, sent=0), NOW + timedelta(days=1))
    row = await store.dispute(who.dp())
    assert (row["has_evidence"], row["submitted"], disputes.waits(row)) == (True, False, True)
    assert await disputes.remind(due - timedelta(days=3)) == 1
    assert await disputes.remind(due - timedelta(days=2)) == 1
    assert await disputes.remind(due - timedelta(hours=20)) == 1
    assert outbox.sent[-1]["subject"].startswith("[Dispute due] $99.00 dispute unanswered")
    ops = await _operator(store, monkeypatch)
    [listed] = (await client.get("/v1/ops/disputes", headers=ops)).json()["data"]
    assert (listed["needs_answer"], listed["saved"], listed["submitted"]) == (True, True, False)

    # Sent: Stripe's state says the bank is reviewing it. The reminders stop.
    await send(client, "charge.dispute.updated", dispute(who, due=due, status="under_review", saved=True, sent=1),
               NOW + timedelta(days=8, hours=6))
    row = await store.dispute(who.dp())
    assert (row["submitted"], disputes.waits(row)) == (True, False)
    assert await disputes.remind(due - timedelta(hours=1)) == 0
    [listed] = (await client.get("/v1/ops/disputes", headers=ops)).json()["data"]
    assert (listed["needs_answer"], listed["saved"], listed["submitted"], listed["answered_before"]) == (
        False, False, True, False)


async def test_an_unanswered_dispute_is_mailed_again_inside_three_days_of_its_date_once_a_day(client, store, outbox):
    due = NOW + timedelta(days=9)
    who = await disputed(client, store, due=due)
    assert await disputes.remind(due - timedelta(days=4)) == 0, "not yet"
    assert await disputes.remind(due - timedelta(days=3)) == 1
    assert await disputes.remind(due - timedelta(days=3) + timedelta(hours=5)) == 0, "once a day"
    assert await disputes.remind(due - timedelta(days=2) + timedelta(minutes=1)) == 1
    assert outbox.sent[-1]["subject"] == f"[Dispute due] $99.00 dispute unanswered, due {due.day} {due:%b %Y}"
    assert await disputes.remind(due + timedelta(hours=30)) == 0, "the date has passed: the bank's decision is the next mail"
    await send(client, "charge.dispute.closed", dispute(who, due=due, status="lost"), due + timedelta(days=2))
    assert outbox.sent[-1]["subject"] == "[Dispute lost] The $99.00 dispute is closed" and len(outbox.sent) == 4


async def test_an_old_dispute_still_waiting_is_listed_first_and_reminded_however_many_came_after(client, store, outbox):
    """The round and the console read the newest rows: an older open
    dispute behind enough newer ones was neither reminded nor listed."""
    who = await paying(store)
    due = NOW + timedelta(days=2)
    await send(client, "charge.dispute.created", dispute(who, 1, due=due, opened=NOW - timedelta(days=19)))
    for n in range(2, 8):
        await send(client, "charge.dispute.closed", dispute(who, n, status="won", sent=1, opened=NOW - timedelta(days=n)))
    assert next(d["dispute_id"] for d in await store.disputes(3)) == who.dp(1)
    before = len(outbox.sent)
    assert await disputes.remind(NOW + timedelta(hours=30)) == 1
    assert outbox.sent[before]["subject"].startswith("[Dispute due]")


# ── the rate ─────────────────────────────────────────────────────────

async def _seed(store, payments: int, disputed_n: int, at: datetime, *, warnings: int = 0) -> None:
    tag = f"{store.tag}{os.urandom(2).hex()}"
    tenant = await store.create_tenant("Paying ISV")
    rows = {"tenant_id": tenant.id, "okwan": True, "amount_minor": 9900, "currency": "usd", "due_by": None,
            "has_evidence": False, "submitted": False, "opened_at": at, "closed_at": None, "event_at": at}
    await store.link_customer(f"cus_{tag}", tenant.id)
    for n in range(payments):
        await store.note_payment(f"ch_{tag}_{n}", customer_id=f"cus_{tag}", amount_minor=9900, currency="usd",
                                 paid_at=at)
    for n in range(disputed_n):
        await store.save_dispute(f"dp_{tag}_{n}", kind="dispute", charge_id=f"ch_{tag}_{n}", reason="general",
                                 status="needs_response", **rows)
    for n in range(warnings):
        await store.save_dispute(f"issfr_{tag}_{n}", kind="warning", charge_id=f"ch_{tag}_{n}", reason=None,
                                 status="flagged", **rows)


@pytest.mark.parametrize(("payments", "disputed_n", "warnings", "rate", "with_warnings", "over"), [
    (12, 1, 0, "8.33%", "8.33%", [True, True]), (400, 1, 0, "0.25%", "0.25%", [False, False]),
    (200, 1, 0, "0.50%", "0.50%", [False, True]), (400, 3, 0, "0.75%", "0.75%", [True, True]),
    (400, 1, 1, "0.25%", "0.50%", [False, True]), (40, 0, 0, "0.00%", "0.00%", [False, False]),
])
async def test_the_rate_is_okwans_disputes_against_okwans_payments_beside_the_two_lines(
        store, outbox, payments, disputed_n, warnings, rate, with_warnings, over):
    from okwan_api.auth import set_store

    set_store(store)
    await _seed(store, payments, disputed_n, NOW - timedelta(days=3), warnings=warnings)
    await _seed(store, 5, 5, NOW - timedelta(days=31))                       # outside the 30 days
    act = await disputes.activity(NOW)
    assert (act["payments"], act["disputes"], act["warnings"]) == (payments, disputed_n, warnings)
    assert (act["rate"], act["with_warnings"]) == (rate, with_warnings)
    assert [(line["at"], line["over"]) for line in act["lines"]] == [("0.75%", over[0]), ("0.50%", over[1])]
    assert ("one dispute crosses a line" in disputes.activity_line(act)) == any(over)


async def test_no_payments_is_no_rate_not_a_division(store, outbox):
    from okwan_api.auth import set_store

    set_store(store)
    act = await disputes.activity(NOW)
    assert (act["payments"], act["rate"], act["rate_hundredths"], act["with_warnings"]) == (0, None, None, None)
    assert "there is no rate to give" in disputes.activity_line(act)


# ── the answer, prepared ─────────────────────────────────────────────

async def test_the_evidence_is_what_the_record_shows_in_stripes_fields(client, store, outbox):
    paid_at = NOW - timedelta(days=10)
    who = await paying(store, paid_at=paid_at)
    earlier = NOW - timedelta(days=40)
    await send(client, "charge.succeeded", charge(who, 0, earlier))
    await send(client, "charge.succeeded", charge(who, 1, paid_at))
    shop = await store.create_tenant("Kofi's Store", parent_id=who.tenant.id)
    seen = {"credential": "okw_abcd1234", "network": "198.51.100.0/24"}
    await store.record_access(who.tenant.id, ip="198.51.100.7", surface="rest:/v1/stripe/charges/list",
                              at=paid_at - timedelta(days=1), **seen)            # before the payment
    await store.record_access(shop.id, ip="198.51.100.7", surface="rest:/v1/square/payments/list",
                              at=paid_at + timedelta(days=2), **seen)
    await store.record_access(who.tenant.id, ip="198.51.100.9", surface="sql:/v1/query",
                              at=paid_at + timedelta(days=1), **seen)
    await store.record_access(shop.id, credential="schedule", ip="unknown", network="unknown",
                              surface="schedule:across", at=paid_at + timedelta(days=3))   # Okwan's own daily check
    await send(client, "charge.dispute.created", dispute(who))

    pack = await disputes.evidence(await store.dispute(who.dp()), NOW)
    f = {name: field["text"] for name, field in pack["fields"].items()}
    assert set(pack["fields"]) == set(disputes.FIELDS) and pack["matched"] is True
    assert pack["reason"]["code"] == "subscription_canceled" and "cancellation_rebuttal" in pack["for_this_reason"]
    assert (f["customer_name"], f["customer_email_address"], f["customer_purchase_ip"]) == (
        "Acme ISV", who.email, "203.0.113.24")
    assert pack["fields"]["customer_purchase_ip"]["add"].startswith("Matched by time: the account's most recent paid")
    assert f["product_description"] == disputes.PRODUCT
    assert f["service_date"] == f"{disputes.day(paid_at)}: the day this payment was taken."
    shown = f["refund_policy_disclosure"]
    assert shown == f["cancellation_policy_disclosure"] and shown.startswith(policy.disclosure())
    for said in ((f"opened Okwan's checkout at {disputes.stamp(paid_at - timedelta(minutes=3))} from the address "
                  "203.0.113.24"), f"showed version {policy.VERSION} of the policy",
                 f"Stripe reported it paid at {disputes.stamp(paid_at)}",
                 f"Stripe reported it ticked at {disputes.stamp(paid_at)}"):
        assert said in shown, said
    assert f["uncategorized_text"] == policy.text(policy.VERSION) and pack["policy_version"] == policy.VERSION
    assert 'the subscription\'s state at Stripe is "active"' in f["cancellation_rebuttal"]
    assert pack["fields"]["cancellation_rebuttal"]["add"].startswith("When the customer cancelled, if they did")
    # The customer's own use after the payment, oldest first, and nothing else.
    log = f["access_activity_log"].split("\n")
    assert "2 on record, the first 2 here" in log[0]
    assert log[1:] == [f"{disputes.stamp(paid_at + timedelta(days=1))}  198.51.100.9  sql:/v1/query",
                       f"{disputes.stamp(paid_at + timedelta(days=2))}  198.51.100.7  rest:/v1/square/payments/list"]
    assert "okw_abcd1234" not in f["access_activity_log"] and "schedule" not in f["access_activity_log"]
    assert f["duplicate_charge_id"] == who.charge(0)
    assert f"{who.charge(0)}, was taken {disputes.day(earlier)}" in f["duplicate_charge_explanation"]
    assert "30 days later" in f["duplicate_charge_explanation"]
    assert [a["field"] for a in pack["attach"]] == ["refund_policy", "cancellation_policy", "customer_communication",
                                                    "receipt"]
    assert 0 < pack["characters"] < disputes.EVIDENCE_CHARACTERS, "Stripe's limit across every field"


async def test_the_customers_first_requests_are_in_the_answer_whatever_came_after(client, store, outbox):
    """Attacker's pass: the log was the newest 1,000 rows, read before
    Okwan's scheduled reads were dropped. Thirty days of daily checks on
    a few dozen merchants, or a thousand later reads from elsewhere,
    pushed the customer's own first requests out: "No use of the service
    is on record"."""
    paid_at = NOW - timedelta(days=28)
    who = await paying(store, paid_at=paid_at)
    await send(client, "charge.succeeded", charge(who, 1, paid_at))
    own = {"credential": "okw_abcd1234", "network": "203.0.113.0/24", "ip": "203.0.113.24", "surface": "sql:/v1/query"}
    for n in range(40):
        await store.record_access(who.tenant.id, at=paid_at + timedelta(hours=1, minutes=n), **own)
    for n in range(1_050):                                                  # Okwan's own daily checks since
        await store.record_access(who.tenant.id, credential="schedule", ip="unknown", network="unknown",
                                  surface="schedule:across", at=paid_at + timedelta(days=2, minutes=n))
    for n in range(1_030):                                                  # and later reads from somewhere else
        await store.record_access(who.tenant.id, credential="okw_abcd1234", network="192.0.2.0/24", ip="192.0.2.66",
                                  surface="rest:/v1/stripe/charges/list", at=paid_at + timedelta(days=5, minutes=n))
    await send(client, "charge.dispute.created", dispute(who, reason="product_not_received"))
    log = (await disputes.evidence(await store.dispute(who.dp()), NOW))["fields"]["access_activity_log"]["text"].split("\n")
    assert f"1070 on record, the first {disputes.LOG_LINES} here" in log[0]
    assert len(log) == disputes.LOG_LINES + 1
    assert all("203.0.113.24  sql:/v1/query" in line for line in log[1:41]), "the first requests, from where they paid"
    assert log[1].startswith(disputes.stamp(paid_at + timedelta(hours=1)))


@pytest.mark.parametrize("words", ["the cardholder was abroad that week; see the attached letter",
                                   "::1%the cardholder was abroad that week; see the attached letter"])
async def test_an_address_that_is_not_one_never_reaches_the_answer_and_the_answer_fits(client, store, outbox, words):
    """Attacker's pass: what a header said was copied into the answer as
    the address the customer paid from; and nothing held the answer to
    Stripe's 150,000 characters. Second pass: the same, written after a
    percent sign, as a row recorded before that was refused would hold it."""
    paid_at = NOW - timedelta(days=10)
    who = await paying(store, paid_at=paid_at, ip=words)
    await send(client, "charge.succeeded", charge(who, 1, paid_at))
    seen = {"credential": "okw_abcd1234", "network": "unknown"}
    for n in range(disputes.LOG_LINES):
        await store.record_access(who.tenant.id, ip=words * 50, surface="rest:/v1/x " + "y" * 3000,
                                  at=paid_at + timedelta(minutes=n), **seen)
    await store.record_access(who.tenant.id, ip="198.51.100.9", surface="sql:/v1/query  " + "z" * 3000,
                              at=paid_at + timedelta(hours=3), **seen)
    await send(client, "charge.dispute.created", dispute(who, reason="fraudulent"))
    pack = await disputes.evidence(await store.dispute(who.dp()), NOW)
    assert pack["fields"]["customer_purchase_ip"] == {
        "text": None, "add": "The checkout's record holds no address that reads as one."}
    assert "from the address" not in pack["fields"]["refund_policy_disclosure"]["text"]
    every = json.dumps(pack)
    assert "abroad" not in every and "attached letter" not in every
    assert pack["characters"] <= disputes.EVIDENCE_CHARACTERS
    assert pack["fields"]["access_activity_log"]["add"].startswith("The addresses are what the network in front of Okwan")


async def test_two_payments_days_apart_are_not_called_two_months(client, store, outbox):
    """Attacker's pass: the one case where the cardholder is right. Two
    payments three minutes apart were explained as "different months",
    and an account's first payment, heard of before its customer was
    linked, was not found at all."""
    first = NOW - timedelta(days=10)
    who = await paying(store, linked=False, checkout=False)
    await send(client, "charge.succeeded", charge(who, 0, first))            # before the customer is linked
    await store.save_billing(who.tenant.id, customer_id=who.customer, subscription_id="sub_1", status="active",
                             cancel_at_period_end=False, current_period_end=None)
    await send(client, "charge.succeeded", charge(who, 1, first + timedelta(minutes=3)))
    await send(client, "charge.dispute.created", dispute(who, reason="duplicate"))
    fields = (await disputes.evidence(await store.dispute(who.dp()), NOW))["fields"]
    assert fields["duplicate_charge_id"]["text"] == who.charge(0), "the first payment is found"
    said = fields["duplicate_charge_explanation"]
    assert said["text"].startswith("Two payments by this account 0 days apart:") and "month" not in said["text"]
    assert "the cardholder is right: accept the dispute" in said["add"]
    assert fields["service_date"]["text"].endswith("the day this payment was taken.")
    assert "Take the month this payment covers from its invoice" in fields["service_date"]["add"]


async def test_a_payment_with_no_checkout_on_record_is_not_given_a_policy(client, store, outbox, monkeypatch):
    """Attacker's pass: it was answered with today's policy, and "Before
    paying, the customer was shown…", for a payment made before today's
    version existed."""
    who = await paying(store, checkout=False)
    await send(client, "charge.succeeded", charge(who, 1, NOW - timedelta(days=45)))
    await send(client, "charge.dispute.created", dispute(who, reason="credit_not_processed"))
    monkeypatch.setattr(policy, "ARCHIVE", {policy.VERSION: policy.said()})
    monkeypatch.setattr(policy, "VERSION", "2027-02-01")
    pack = await disputes.evidence(await store.dispute(who.dp()), NOW)
    fields = pack["fields"]
    assert pack["policy_version"] is None
    for name in ("refund_policy_disclosure", "cancellation_policy_disclosure", "uncategorized_text",
                 "refund_refusal_explanation", "customer_purchase_ip"):
        assert fields[name]["text"] is None, name
    assert "No checkout is on record for this payment, so what it showed is not known here" in (
        fields["refund_policy_disclosure"]["add"])
    assert fields["customer_name"]["text"] == "Acme ISV", "who the customer is, is on record"


async def test_a_payment_under_an_earlier_policy_is_answered_with_that_version(client, store, outbox, monkeypatch):
    who = await disputed(client, store, reason="credit_not_processed")
    earlier = {**policy.said(), "text": "THE POLICY AS IT WAS (version 2026-11-13)\n",
               "disclosure": "How it was shown then."}
    monkeypatch.setattr(policy, "ARCHIVE", {policy.VERSION: earlier})
    monkeypatch.setattr(policy, "VERSION", "2027-02-01")
    pack = await disputes.evidence(await store.dispute(who.dp()), NOW)
    assert pack["policy_version"] == "2026-11-13"
    assert pack["fields"]["uncategorized_text"]["text"] == earlier["text"]
    assert pack["fields"]["refund_policy_disclosure"]["text"].startswith("How it was shown then. This customer opened")
    # A version that is not on file is said to be missing, not answered with today's words.
    monkeypatch.setattr(policy, "ARCHIVE", {})
    pack = await disputes.evidence(await store.dispute(who.dp()), NOW)
    assert pack["policy_version"] is None and pack["fields"]["uncategorized_text"]["text"] is None
    assert "is not on file" in pack["fields"]["refund_policy_disclosure"]["add"]


async def test_a_deleted_accounts_dispute_is_still_there_to_answer(client, store, outbox):
    who = await disputed(client, store)
    if hasattr(store, "pool"):
        await store.pool.execute("DELETE FROM tenants WHERE id = $1", who.tenant.id)
    else:
        await store.delete_tenant(who.tenant.id)
    row = await store.dispute(who.dp())
    assert (row["tenant_id"], row["okwan"]) == (None, True)
    assert (await store.payment(who.charge()))["okwan"] is True and (await disputes.activity(NOW))["payments"] == 1
    pack = await disputes.evidence(row, NOW)
    assert pack["fields"]["customer_name"] == {
        "text": None, "add": "The account has been deleted: its name, address and use are no longer on record."}
    assert pack["fields"]["product_description"]["text"] == disputes.PRODUCT
    _, text = await disputes.message(row, "due", NOW)
    assert "an Okwan account that no longer exists" in text


# ── second attacker's pass ───────────────────────────────────────────

async def test_use_recorded_without_a_readable_address_is_still_use(client, store, outbox):
    """Rows whose address did not have the shape of one were left out of
    the list and of the count. Use made from such an address was answered
    with "No use of the service is on record after this payment"."""
    paid_at = NOW - timedelta(days=10)
    who = await paying(store, paid_at=paid_at)
    await send(client, "charge.succeeded", charge(who, 1, paid_at))
    seen = {"credential": "okw_abcd1234", "network": "unknown", "surface": "rest:/v1/files/bank_deposits/list"}
    for n in range(5):
        await store.record_access(who.tenant.id, ip="fe80::1%x", at=paid_at + timedelta(hours=1, minutes=n), **seen)
    await store.record_access(who.tenant.id, ip="198.51.100.9", at=paid_at + timedelta(hours=2), **seen)
    await send(client, "charge.dispute.created", dispute(who, reason="product_not_received"))
    log = (await disputes.evidence(await store.dispute(who.dp()), NOW))["fields"]["access_activity_log"]
    head, *lines = log["text"].split("\n")
    assert "6 on record, the first 6 here" in head
    assert all(line.endswith(f"  {disputes.NO_ADDRESS}  rest:/v1/files/bank_deposits/list") for line in lines[:5])
    assert lines[5].endswith("  198.51.100.9  rest:/v1/files/bank_deposits/list")
    assert "fe80" not in log["text"], "what was recorded as the address is not given as one"
    assert await store.customer_access(who.tenant.id, paid_at, 2) == (6, [
        {"at": paid_at + timedelta(hours=1, minutes=n), "ip": "fe80::1%x", "surface": seen["surface"]} for n in (0, 1)])


async def test_the_answer_fits_stripes_limit_whatever_the_record_holds(client, store, outbox, monkeypatch):
    """The trimming knew two fields that could grow, the log and the
    policy's text. An address 50,000 characters long, in the answer three
    times, went past Stripe's limit. That value is refused now; and the
    limit holds for a record nobody foresaw: the longest field goes."""
    who = await disputed(client, store)
    monkeypatch.setattr(disputes, "PRODUCT", "x" * 200_000)
    pack = await disputes.evidence(await store.dispute(who.dp()), NOW)
    assert pack["characters"] <= disputes.EVIDENCE_CHARACTERS
    assert pack["fields"]["product_description"] == {
        "text": None, "add": "Left out to fit Stripe's limit on an answer's length: say it in your own words."}
    assert pack["fields"]["customer_name"]["text"] == "Acme ISV", "and nothing else was lost for it"


@pytest.mark.parametrize("count_after", [0, 1])
async def test_an_answered_inquiry_that_becomes_a_dispute_is_waiting_again(client, store, outbox, monkeypatch,
                                                                            count_after):
    """An answer once sent was remembered for ever: `submitted` only went
    up, and a dispute counted as waiting only without it. An inquiry that
    was answered and became a dispute all the same was mailed once and
    never reminded, and the console listed it as needing nothing, while
    Stripe's documentation says it has to be answered again. Stripe's
    state decides now, whatever its count of answers says."""
    who = await paying(store)
    due, t = NOW + timedelta(days=2), NOW - timedelta(hours=3)
    await send(client, "charge.succeeded", charge(who, 1))
    await send(client, "charge.dispute.created", dispute(who, 1, status="warning_needs_response", due=due), t)
    await send(client, "charge.dispute.updated",
               dispute(who, 1, status="warning_under_review", due=due, saved=True, sent=1), t + timedelta(hours=1))
    assert not disputes.waits(await store.dispute(who.dp(1))), "answered: with the bank"
    await send(client, "charge.dispute.funds_withdrawn",
               dispute(who, 1, status="needs_response", due=due, saved=True, sent=count_after), t + timedelta(hours=2))
    assert [m["subject"].split("]")[0] for m in outbox.sent] == ["[Inquiry", "[Dispute"]
    assert "The bank's inquiry has become a dispute." in outbox.sent[-1]["text"]
    row = await store.dispute(who.dp(1))
    assert row["status"] == "needs_response" and disputes.waits(row)
    assert await disputes.remind(NOW + timedelta(hours=25)) == 1
    assert outbox.sent[-1]["subject"].startswith("[Dispute due]") and who.dp(1) in outbox.sent[-1]["text"]
    [listed] = (await client.get("/v1/ops/disputes", headers=await _operator(store, monkeypatch))).json()["data"]
    assert (listed["needs_answer"], listed["submitted"], listed["answered_before"]) == (True, False, count_after == 1)


async def test_a_decided_dispute_opened_again_is_reminded_like_any_other(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who, 1))
    t, due = NOW - timedelta(days=20), NOW + timedelta(days=2, hours=12)
    await send(client, "charge.dispute.created", dispute(who, 1, opened=t), t)
    await send(client, "charge.dispute.updated", dispute(who, 1, status="under_review", opened=t, sent=1),
               t + timedelta(days=1))
    await send(client, "charge.dispute.closed", dispute(who, 1, status="won", opened=t, sent=1), t + timedelta(days=10))
    await send(client, "charge.dispute.updated", dispute(who, 1, status="needs_response", opened=t, due=due, sent=1),
               NOW - timedelta(hours=1))
    assert "A dispute that was decided has been opened again." in outbox.sent[-1]["text"]
    assert disputes.waits(await store.dispute(who.dp(1)))
    assert await disputes.remind(NOW + timedelta(hours=25)) == 1, "a day after the mail that said it was opened again"
    assert await disputes.remind(NOW + timedelta(hours=30)) == 0, "once a day"
    assert await disputes.remind(NOW + timedelta(hours=50)) == 1


def _paid_session(who: Paying, n: int, customer: str) -> dict:
    return {"id": f"cs_{who.tag}_{n}", "mode": "subscription", "payment_status": "paid", "customer": customer,
            "subscription": f"sub_{who.tag}_{n}",
            "metadata": {"tenant_id": who.tenant.id, "refund_policy": policy.VERSION}}


async def test_a_payment_by_an_accounts_second_stripe_customer_is_okwans_and_the_accounts(client, store, outbox,
                                                                                         monkeypatch):
    """A payment was Okwan's only through the one customer on the
    account's billing record. Two pay pages opened before either was paid
    are two customers at Stripe; billing keeps the first, and the second
    one's payment, the one most likely to be disputed, was "another
    product's": no account, no evidence, outside Okwan's rate, forgotten
    in a week. Every customer that pays for an account is remembered."""
    from okwan_api import billing

    async def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"type": "invalid_request_error"}})

    monkeypatch.setattr(billing, "_told_twice", set())
    billing.set_transport(httpx.MockTransport(refuse))
    try:
        who = await paying(store, linked=False, checkout=False)
        first, second = f"cus_{who.tag}_a", f"cus_{who.tag}_b"
        paid = NOW - timedelta(minutes=5)
        again = paid + timedelta(seconds=40)
        assert (await send(client, "checkout.session.completed", _paid_session(who, 1, first), paid)).json()["plan"] == "pro"
        await send(client, "charge.succeeded", {**charge(who, 1, paid), "customer": first}, paid)
        r = await send(client, "checkout.session.completed", _paid_session(who, 2, second), again)
        assert r.json() == {"received": True, "plan": "pro", "second_subscription": True}
        await send(client, "charge.succeeded", {**charge(who, 2, again), "customer": second}, again)
    finally:
        billing.set_transport(None)
    assert (await store.billing_for(who.tenant.id))["customer_id"] == first, "billing keeps the first"
    for n in (1, 2):
        kept = await store.payment(who.charge(n))
        assert (kept["okwan"], kept["tenant_id"]) == (True, who.tenant.id), n

    outbox.sent.clear()
    await send(client, "charge.dispute.created", dispute(who, 2, reason="duplicate"))
    [mail] = outbox.sent
    assert f"Account:  {who.tenant.id} " in mail["text"] and "not matched" not in mail["text"]
    row = await store.dispute(who.dp(2))
    assert (row["okwan"], row["tenant_id"]) == (True, who.tenant.id)
    fields = (await disputes.evidence(row, datetime.now(UTC)))["fields"]
    assert fields["duplicate_charge_id"]["text"] == who.charge(1), "the account's first payment is put beside it"
    assert fields["duplicate_charge_explanation"]["text"].startswith("Two payments by this account 0 days apart:")
    assert "the cardholder is right: accept the dispute" in fields["duplicate_charge_explanation"]["add"]
    act = await disputes.activity(datetime.now(UTC))
    assert (act["payments"], act["disputes"], act["unmatched"]) == (2, 1, 0), "and it is in Okwan's rate"
    # A week on it is still on record: it was never another product's.
    await disputes.remind(NOW + UNMATCHED_PAYMENT_KEPT + timedelta(hours=1))
    assert (await store.payment(who.charge(2)))["okwan"] is True


async def test_a_second_customer_heard_of_after_its_payment_is_matched_on_the_next_round(client, store, outbox):
    """Stripe does not order its events: the charge can be reported before
    the session that says whose it is."""
    who = await paying(store)
    second = f"cus_{who.tag}_b"
    await send(client, "charge.succeeded", {**charge(who, 2), "customer": second})
    assert (await store.payment(who.charge(2)))["okwan"] is False
    await send(client, "customer.subscription.created", {
        "id": f"sub_{who.tag}_2", "customer": second, "status": "active", "metadata": {"tenant_id": who.tenant.id}})
    await disputes.remind(NOW)
    kept = await store.payment(who.charge(2))
    assert (kept["okwan"], kept["tenant_id"]) == (True, who.tenant.id)
    assert await store.okwan_customer(second) == (True, who.tenant.id)
    assert await store.okwan_customer(f"cus_{who.tag}_nobody") == (False, None)


async def test_a_month_charged_after_its_account_was_deleted_is_still_okwans(client, store, outbox):
    """Deleting an account took its billing record with it, and with that
    the only link from its Stripe customer to Okwan. If the subscription
    was left running, the next month's payment, the one a customer who
    deleted their account will dispute, was "another product's"."""
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who, 1, NOW - timedelta(days=31)))
    if hasattr(store, "pool"):
        await store.pool.execute("DELETE FROM tenants WHERE id = $1", who.tenant.id)
    else:
        await store.delete_tenant(who.tenant.id)
    assert await store.tenant_for_billing_customer(who.customer) is None
    assert await store.okwan_customer(who.customer) == (True, None), "the customer is still one of Okwan's"
    await send(client, "charge.succeeded", charge(who, 2, NOW - timedelta(hours=2)))
    later = await store.payment(who.charge(2))
    assert (later["okwan"], later["tenant_id"]) == (True, None)
    await send(client, "charge.dispute.created", dispute(who, 2))
    [mail] = outbox.sent
    assert "Account:  an Okwan account that no longer exists" in mail["text"] and "not matched" not in mail["text"]
    row = await store.dispute(who.dp(2))
    assert (row["okwan"], row["tenant_id"]) == (True, None)
    pack = await disputes.evidence(row, datetime.now(UTC))
    assert pack["matched"] is True and pack["fields"]["customer_name"]["add"].startswith("The account has been deleted")
    assert (await disputes.activity(datetime.now(UTC)))["unmatched"] == 0


async def _use(store, who: Paying, at: datetime, n: int = 1) -> None:
    for i in range(n):
        await store.record_access(who.tenant.id, credential="session", ip="203.0.113.24", network="203.0.113.0/24",
                                  surface="rest:/v1/stripe/charges/list", at=at + timedelta(minutes=i))


async def _sweep_the_log(store, who: Paying, now: datetime) -> None:
    """The hourly sweep's own condition, on this account's rows only: the database is shared."""
    if hasattr(store, "pool"):
        await store.pool.execute("DELETE FROM access_log WHERE tenant_id = $1 AND at < $2", who.tenant.id,
                                 now - rules.RETENTION)
    else:
        await store.sweep_access(now)


async def test_a_payment_older_than_the_log_is_not_said_to_have_gone_unused(client, store, outbox):
    """The log of requests is kept 90 days and a cardholder has longer
    than that to dispute. For an older payment the answer said "No use of
    the service is on record after this payment", of a month the customer
    had used; the customer chooses when to dispute."""
    paid = NOW - timedelta(days=100)
    who = await paying(store, paid_at=paid)
    await send(client, "charge.succeeded", charge(who, 1, paid), paid)
    await _use(store, who, paid + timedelta(hours=1), n=30)
    await _sweep_the_log(store, who, NOW)
    await send(client, "charge.dispute.created", dispute(who, reason="product_not_received"))
    log = (await disputes.evidence(await store.dispute(who.dp()), NOW))["fields"]["access_activity_log"]
    kept_from = disputes.day(NOW - rules.RETENTION)
    assert log["text"] is None
    assert log["add"] == (
        f"Okwan keeps its record of requests for 90 days and this payment was taken {disputes.day(paid)}, longer ago "
        f"than that: nothing is on record since {kept_from}, and whether the service was used before that can no "
        "longer be shown from here. Do not tell the bank there was no use.")


async def test_use_still_on_record_for_an_older_payment_is_said_to_start_where_the_log_does(client, store, outbox):
    """With later use still on record the heading read "after the payment:
    2 on record, the first 2 here" over lines that began forty days after
    it: not the count since the payment, and not the first."""
    paid = NOW - timedelta(days=100)
    who = await paying(store, paid_at=paid)
    await send(client, "charge.succeeded", charge(who, 1, paid), paid)
    await _use(store, who, paid + timedelta(hours=1), n=30)
    await _use(store, who, NOW - timedelta(days=60), n=2)
    await _sweep_the_log(store, who, NOW)
    await send(client, "charge.dispute.created", dispute(who, reason="fraudulent"))
    log = (await disputes.evidence(await store.dispute(who.dp()), NOW))["fields"]["access_activity_log"]
    head, *lines = log["text"].split("\n")
    assert head == (
        "Okwan keeps its record of requests for 90 days. Requests made with this account's own sign-in and keys "
        f"since {disputes.stamp(NOW - rules.RETENTION)}, as far back as that record now reaches: 2 on record, the "
        "first 2 here. Each line: time, the caller's address, and what was read.")
    assert "after the payment" not in log["text"] and len(lines) == 2
    assert lines[0].startswith(f"{NOW - timedelta(days=60):%Y-%m-%d}")
    assert f"requests from before {disputes.day(NOW - rules.RETENTION)} are no longer on record" in log["add"]
    # A payment inside the 90 days is told as before: from the payment on.
    recent = await paying(store, paid_at=NOW - timedelta(days=10))
    await send(client, "charge.succeeded", charge(recent, 1, NOW - timedelta(days=10)))
    await _use(store, recent, NOW - timedelta(days=9), n=1)
    await send(client, "charge.dispute.created", dispute(recent, reason="fraudulent"))
    said = (await disputes.evidence(await store.dispute(recent.dp()), NOW))["fields"]["access_activity_log"]
    assert said["text"].startswith("Requests to Okwan made with this account's own sign-in and keys after the payment: 1")
    assert "90 days" not in said["text"] + said["add"]


async def _stopped_inside_a_send(outbox, now: datetime) -> None:
    """A round that is cancelled while the provider is answering: what
    happens to the hourly round when the process is told to stop."""
    outbox.delay = 5.0
    round_ = asyncio.create_task(disputes.remind(now))
    await asyncio.sleep(0.3)
    round_.cancel()
    with pytest.raises(asyncio.CancelledError):
        await round_
    outbox.delay = 0.0


async def test_a_mail_whose_send_never_reported_back_is_sent_by_a_later_round(client, store, outbox):
    """The right to mail was taken by writing down that the operator had
    been told, and taken back only when the provider refused. A round
    stopped inside the send (a deploy), or a database that went away
    before the refusal could be written, left the record saying "told"
    with no mail sent, for good. The right is now a time that runs out."""
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    outbox.refuse = True
    await send(client, "charge.dispute.created", dispute(who))
    outbox.refuse = False
    assert outbox.sent == [] and (await store.dispute(who.dp()))["told"] is None
    now = datetime.now(UTC)
    await _stopped_inside_a_send(outbox, now)
    row = await store.dispute(who.dp())
    assert outbox.sent == [] and row["told"] is None and row["claimed_at"] == now, "not told, and not said to be"
    assert await disputes.remind(now + timedelta(minutes=1)) == 0, "the send may still be going: nobody mails twice"
    assert await disputes.remind(now + DISPUTE_NOTICE_HELD + timedelta(minutes=1)) == 1
    assert outbox.sent[0]["subject"].startswith("[Dispute] $99.00 disputed")
    row = await store.dispute(who.dp())
    assert (row["told"], row["claimed_at"]) == ("open", None)


@pytest.mark.parametrize("accepted", [False, True])
async def test_a_decision_whose_outcome_could_not_be_recorded_is_still_mailed(client, store, outbox, monkeypatch,
                                                                              accepted):
    """The database goes away between the send and writing down how it
    went. Stripe's event is still acknowledged (it was applied), and the
    next round mails: once more than needed if the provider had accepted
    the first, which is the side to err on."""
    who = await disputed(client, store)
    assert len(outbox.sent) == 1
    outbox.refuse = not accepted
    end = store.end_dispute_notice

    async def gone(*a, **k):
        raise ConnectionError("connection was closed in the middle of operation")

    monkeypatch.setattr(store, "end_dispute_notice", gone)
    r = await send(client, "charge.dispute.closed", dispute(who, status="lost"), NOW + timedelta(seconds=30))
    assert r.status_code == 200 and r.json()["applied"] is True
    monkeypatch.setattr(store, "end_dispute_notice", end)
    outbox.refuse = False
    assert (await store.dispute(who.dp()))["told"] == "open", "the record does not say the decision was told"
    assert await disputes.remind(datetime.now(UTC) + DISPUTE_NOTICE_HELD + timedelta(minutes=1)) == 1
    assert outbox.sent[-1]["subject"] == "[Dispute lost] The $99.00 dispute is closed"
    assert [m["subject"].split("]")[0] for m in outbox.sent].count("[Dispute lost") == (2 if accepted else 1)
    assert await disputes.remind(datetime.now(UTC) + timedelta(hours=2)) == 0


async def test_an_event_that_lands_while_a_mail_is_going_out_is_told_straight_after_it(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    outbox.delay = 0.3
    opened = asyncio.create_task(send(client, "charge.dispute.created", dispute(who)))
    await asyncio.sleep(0.1)                                 # the first mail is with the provider
    decided = await send(client, "charge.dispute.closed", dispute(who, status="won"), NOW + timedelta(minutes=5))
    assert decided.json()["applied"] is True
    await opened
    assert [m["subject"].split("]")[0] for m in outbox.sent] == ["[Dispute", "[Dispute won"]
    assert (await store.dispute(who.dp()))["told"] == "won"


async def test_an_inquiry_that_becomes_a_dispute_is_counted_from_the_day_it_became_one(client, store, outbox):
    """The rate counted a dispute by the day its object was opened, which
    for an inquiry the bank escalated is the day of the inquiry: escalated
    after 30 days it was in no window at all, and the mail that announced
    it carried a rate with no dispute in it."""
    who = await paying(store)
    asked = NOW - timedelta(days=33)
    for n in range(1, 4):
        await send(client, "charge.succeeded", charge(who, n, NOW - timedelta(days=n)))
    await send(client, "charge.dispute.created",
               dispute(who, 1, status="warning_needs_response", opened=asked, due=asked + timedelta(days=20)), asked)
    act = await disputes.activity(NOW)
    assert (act["disputes"], act["inquiries"]) == (0, 0), "an inquiry is not a dispute, and this one is 33 days old"
    await send(client, "charge.dispute.funds_withdrawn",
               dispute(who, 1, status="needs_response", opened=asked, due=NOW + timedelta(days=9)), NOW)
    row = await store.dispute(who.dp(1))
    assert (row["opened_at"], row["disputed_at"]) == (asked, NOW)
    assert "against 3 payments: 33.33% disputed" in outbox.sent[-1]["text"], "the mail that says so carries the rate"
    for days in (0, 15, 29):
        act = await disputes.activity(NOW + timedelta(days=days, minutes=1))
        assert (act["disputes"], act["inquiries"]) == (1, 0), days
    assert (await disputes.activity(NOW + timedelta(days=31)))["disputes"] == 0
    # One opened as a dispute is counted from the day it was opened, whenever Okwan heard of it.
    await send(client, "charge.dispute.closed", dispute(who, 2, status="lost", opened=NOW - timedelta(days=40)), NOW)
    assert (await store.dispute(who.dp(2)))["disputed_at"] == NOW - timedelta(days=40)
    assert (await disputes.activity(NOW + timedelta(minutes=1)))["disputes"] == 1


async def test_a_decision_that_changes_is_mailed(client, store, outbox):
    """What the operator had been told was "ended", not how. A dispute
    mailed as won that Stripe later reported lost was mailed to nobody."""
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.closed", dispute(who, status="won"), NOW - timedelta(days=3))
    await send(client, "charge.dispute.closed", dispute(who, status="lost"), NOW - timedelta(days=1))
    assert [m["subject"] for m in outbox.sent] == ["[Dispute won] The $99.00 dispute is closed",
                                                   "[Dispute lost] The $99.00 dispute is closed"]
    assert ("The decision on this dispute has changed. Okwan mailed it as won; Stripe now reports it lost."
            in outbox.sent[1]["text"])
    await send(client, "charge.dispute.closed", dispute(who, status="lost"), NOW)
    assert len(outbox.sent) == 2 and await disputes.remind(NOW + timedelta(hours=1)) == 0, "the same decision, once"


async def test_a_dispute_matched_after_its_first_mail_is_mailed_again(client, store, outbox):
    """Heard of before its payment, a dispute was mailed "not matched: it
    may be another product's" and, once matched, said nothing more until
    three days before its date."""
    who = await paying(store)
    await send(client, "charge.dispute.created", dispute(who))
    assert "not matched to an Okwan payment" in outbox.sent[0]["text"]
    assert (await store.dispute(who.dp()))["told"] == "open:unmatched"
    await send(client, "charge.succeeded", charge(who))
    assert await disputes.remind(NOW + timedelta(hours=1)) == 1
    text = outbox.sent[1]["text"]
    assert text.startswith("Okwan mailed about this before as not matched to an Okwan payment. The payment's record "
                           "has since arrived: it is a payment for Okwan Pro.")
    assert f"Account:  {who.tenant.id} " in text and "What Okwan holds is already put into Stripe's fields" in text
    assert (await store.dispute(who.dp()))["told"] == "open"
    assert await disputes.remind(NOW + timedelta(hours=2)) == 0


async def test_an_appeal_is_told_and_one_that_needs_an_answer_is_reminded(client, store, outbox, monkeypatch):
    """Stripe's states include an appeal that needs a response. Read from
    its documentation while answering the second pass; neither pass
    raised it. It was "open", so nothing new to tell, and not among the
    states that wait, so never reminded."""
    who = await disputed(client, store)
    due = NOW + timedelta(days=2, hours=12)
    await send(client, "charge.dispute.updated", dispute(who, status="under_review", sent=1), NOW + timedelta(hours=1))
    await send(client, "charge.dispute.updated", dispute(who, status="appeal_needs_response", due=due, sent=1),
               NOW + timedelta(hours=2))
    row = await store.dispute(who.dp())
    assert (disputes.stage(row), disputes.waits(row), row["told"]) == ("appeal", True, "appeal")
    assert "A dispute has gone to appeal: Stripe reports it as needing an answer to the appeal." in outbox.sent[-1]["text"]
    assert await disputes.remind(NOW + timedelta(hours=27)) == 1
    assert outbox.sent[-1]["subject"].startswith("[Dispute due]")
    [listed] = (await client.get("/v1/ops/disputes", headers=await _operator(store, monkeypatch))).json()["data"]
    assert (listed["stage"], listed["needs_answer"], listed["answered_before"]) == ("appeal", True, True)
    await send(client, "charge.dispute.updated", dispute(who, status="appeal_under_review", due=due, sent=2),
               NOW + timedelta(hours=28))
    assert not disputes.waits(await store.dispute(who.dp()))
    assert await disputes.remind(NOW + timedelta(hours=52)) == 0


def test_the_two_stores_say_what_was_heard_in_the_same_words():
    """What the operator has heard is said once in Python (`dispute_heard`,
    which the in-memory store and the mails use) and once in SQL (the
    Postgres round). Every state Stripe documents, matched and not."""
    from okwan_vault import postgres

    states = ["warning_needs_response", "warning_under_review", "warning_closed", "needs_response", "under_review",
              "appeal_needs_response", "appeal_under_review", "won", "lost", "prevented", "flagged", "something_new"]
    said = {(s, ok): dispute_heard(s, ok) for s in states for ok in (True, False)}
    assert said[("won", False)] == "won" and said[("needs_response", False)] == "open:unmatched"
    assert said[("appeal_needs_response", True)] == "appeal" and said[("warning_under_review", True)] == "inquiry"
    assert said[("something_new", True)] == "open", "a state Stripe adds later is an open dispute, not a crash"
    for word in ("'inquiry'", "'appeal'", "'open'", "':unmatched'", "starts_with(status, 'warning_')",
                 "starts_with(status, 'appeal_')", "status = ANY($1::text[]) THEN status"):
        assert word in postgres._HEARD, word


async def test_the_round_reads_what_is_owed_the_same_from_both_stores(client, store, outbox):
    """And by behaviour: one dispute in each state, matched and not, with
    nothing told yet. The round finds all of them, tells each, and then
    finds none."""
    who = await paying(store)
    states = ["warning_needs_response", "warning_under_review", "warning_closed", "needs_response", "under_review",
              "appeal_needs_response", "appeal_under_review", "won", "lost", "prevented"]
    rows = {"tenant_id": who.tenant.id, "amount_minor": 9900, "currency": "usd", "reason": "general", "due_by": None,
            "has_evidence": False, "submitted": False, "opened_at": NOW, "closed_at": None, "event_at": NOW}
    for n, status in enumerate(states):
        for ok in (True, False):
            await store.save_dispute(f"dp_{who.tag}_{n}_{int(ok)}", kind="dispute", charge_id=who.charge(n),
                                     status=status, okwan=ok, **rows)
    owed = await store.disputes_to_settle(NOW, disputes.REMIND_WITHIN, disputes.REMIND_EVERY)
    assert len(owed) == 2 * len(states)
    assert await disputes.remind(NOW) == 2 * len(states)
    told = {(d["status"], d["okwan"]): d["told"] for d in await store.disputes()}
    assert told == {(s, ok): dispute_heard(s, ok) for s in states for ok in (True, False)}
    assert await store.disputes_to_settle(NOW, disputes.REMIND_WITHIN, disputes.REMIND_EVERY) == []


async def test_a_dispute_first_heard_of_when_already_answered_is_not_mailed_as_one_to_answer(client, store, outbox):
    """Seen by the second pass and not run: first heard of with an answer
    already with the bank, a dispute was mailed "answer due"."""
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.updated", dispute(who, status="under_review", saved=True, sent=1))
    [mail] = outbox.sent
    assert mail["subject"] == "[Dispute] $99.00 disputed, answered and with the bank"
    assert "Answered: Stripe reports that an answer has been sent and the bank is reviewing it." in mail["text"]
    assert "Answer by" not in mail["text"] and await disputes.remind(NOW + timedelta(days=8)) == 0


async def test_a_reminder_is_not_sent_for_a_dispute_answered_since_the_round_read_it(client, store, outbox):
    """Seen by the second pass and not run: a round that read a dispute
    just before Stripe reported it answered still mailed "unanswered".
    The mail is written from the dispute as it stands once the right to
    send is held."""
    due = NOW + timedelta(days=2)
    who = await disputed(client, store, due=due)
    stale = await store.dispute(who.dp())                    # what the round read
    await send(client, "charge.dispute.updated", dispute(who, status="under_review", due=due, saved=True, sent=1),
               NOW + timedelta(hours=1))
    before = len(outbox.sent)
    assert await disputes.settle(stale, NOW + timedelta(hours=25)) == 0
    assert len(outbox.sent) == before and (await store.dispute(who.dp()))["claimed_at"] is None


async def test_the_two_stores_agree_on_random_events_claims_rounds_and_the_shipped_counts():
    """From the second pass's own checks, kept: several hundred random
    events with ties in the second, and claims between them, some never
    given back. The same `applied`, the same rows, the same round, the
    same listing order, and the same counts from the Postgres query as
    shipped, which the fixture above narrows by tag everywhere else. The
    clock is decades back, so the counts are this test's own on a shared
    database."""
    if not DSN:
        pytest.skip("no vault database configured")
    import random

    tag = os.urandom(4).hex()
    statuses = ["needs_response", "under_review", "won", "lost", "warning_needs_response", "warning_under_review",
                "warning_closed", "prevented", "appeal_needs_response", "appeal_under_review", "flagged", "new_state"]
    base = datetime(1975 + os.urandom(1)[0] % 20, 3, 1, tzinfo=UTC)
    rnd = random.Random(8)
    mem = MemoryStore(EnvMasterKey(new_key()))
    pg = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    ta, tb = await mem.create_tenant("A"), await pg.create_tenant("A")
    try:
        for st, tenant in ((mem, ta), (pg, tb)):
            await st.link_customer(f"cus_{tag}", tenant.id)
            for n in range(30):
                await st.note_payment(f"ch_{tag}_{n:03d}", customer_id=f"cus_{tag}" if n % 3 else f"cus_{tag}_other",
                                      amount_minor=9900, currency="usd", paid_at=base + timedelta(days=n))
        for n in range(120):
            did = f"dp_{tag}_{n:03d}"
            for step in range(rnd.randint(1, 7)):
                ev = base + timedelta(days=n % 40, seconds=rnd.randint(0, 2))
                kw = {"kind": rnd.choice(["dispute", "warning"]), "charge_id": f"ch_{tag}_{n:03d}",
                      "okwan": rnd.random() < .6, "amount_minor": rnd.choice([None, 9900, 0]),
                      "currency": rnd.choice([None, "usd"]), "reason": rnd.choice([None, "fraudulent", "duplicate"]),
                      "status": rnd.choice(statuses),
                      "due_by": rnd.choice([None, base + timedelta(days=n % 40, hours=rnd.randint(-5, 80))]),
                      "has_evidence": rnd.random() < .3, "submitted": rnd.random() < .3,
                      "opened_at": base + timedelta(days=n % 40, seconds=step), "closed_at": None, "event_at": ev}
                assert (await mem.save_dispute(did, tenant_id=ta.id if kw["okwan"] else None, **kw)
                        == await pg.save_dispute(did, tenant_id=tb.id if kw["okwan"] else None, **kw))
                if rnd.random() < .4:
                    ra, rb = await mem.dispute(did), await pg.dispute(did)
                    at = ev + timedelta(minutes=rnd.choice([0, 1, 20]))
                    took = await mem.claim_dispute_notice(did, told_was=ra["told"], reminded_was=ra["reminded_at"], at=at)
                    assert took == await pg.claim_dispute_notice(did, told_was=rb["told"],
                                                                 reminded_was=rb["reminded_at"], at=at)
                    if took and rnd.random() < .8:          # the rest are sends that never report back
                        how = {"sent": rnd.random() < .7, "reminded_at": rnd.choice([None, ev]),
                               "told": rnd.choice([None, "open", "inquiry", "appeal", "won", "open:unmatched"])}
                        for st in (mem, pg):
                            await st.end_dispute_notice(did, claimed_at=at, **how)
            ra, rb = await mem.dispute(did), await pg.dispute(did)
            assert {**ra, "tenant_id": bool(ra["tenant_id"])} == {**rb, "tenant_id": bool(rb["tenant_id"])}
        assert ([d["dispute_id"] for d in await mem.disputes(10_000)]
                == [d["dispute_id"] for d in await pg.disputes(10_000_000) if tag in d["dispute_id"]])
        for hours in (0, 30, 24 * 20 + 3, 24 * 39):
            now = base + timedelta(hours=hours)
            args = (now, timedelta(days=3), timedelta(hours=24), 10_000_000)
            assert ([d["dispute_id"] for d in await mem.disputes_to_settle(*args)]
                    == [d["dispute_id"] for d in await pg.disputes_to_settle(*args) if tag in d["dispute_id"]])
            window = (now - timedelta(days=30), now + timedelta(seconds=1))
            counted = await pg.dispute_counts(*window)
            assert await mem.dispute_counts(*window) == counted
        assert counted["payments"] > 0 and counted["disputes"] + counted["unmatched"] > 0, "and there was something to count"
    finally:
        await pg.pool.execute("DELETE FROM billing_disputes WHERE dispute_id LIKE $1", f"%{tag}%")
        await pg.pool.execute("DELETE FROM billing_payments WHERE charge_id LIKE $1", f"%{tag}%")
        await pg.pool.execute("DELETE FROM billing_customers WHERE customer_id LIKE $1", f"%{tag}%")
        await pg.pool.execute("DELETE FROM tenants WHERE id = $1", tb.id)
        await pg.close()


@pytest.mark.parametrize("body", [
    b"this is not json", b"", b"\xff\xfe{}", b"[" * 100_000 + b"]" * 100_000,
    b'{"type":"charge.dispute.created","created":' + b"9" * 5000 + b',"data":{"object":{}}}', b'"a string"',
], ids=["not-json", "empty", "not-utf8", "nested-100000-deep", "integer-of-5000-digits", "a-string"])
async def test_a_signed_body_that_cannot_be_read_is_acknowledged(client, store, outbox, body):
    """A signed JSON object of the wrong shape was acknowledged; a signed
    body that is not JSON at all was an error, which Stripe sends again
    for three days. Only Stripe can sign one, so nobody could arrange it."""
    stamp = int(time.time())
    sig = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    r = await client.post("/v1/billing/webhook", content=body, headers={"Stripe-Signature": f"t={stamp},v1={sig}"})
    assert (r.status_code, r.json()) == (200, {"received": True})
    assert await store.disputes() == [] and outbox.sent == []


async def test_a_charge_that_is_not_the_price_of_pro_is_said_to_be_one_to_check(client, store, outbox):
    """A payment is Okwan's by its Stripe customer: nothing Stripe sends
    with a charge says which product it was for. If another product on the
    same Stripe account charges a customer Okwan knows, that charge was
    answered for as Okwan Pro without a word, and made the Okwan payment
    beside it look charged twice. Okwan cannot tell them apart for
    certain; it says when a charge is not what Pro costs, and it no longer
    calls two charges of different amounts the same thing charged twice."""
    paid = NOW - timedelta(days=10)
    who = await paying(store, paid_at=paid)
    await send(client, "charge.succeeded", charge(who, 1, paid), paid)
    await send(client, "charge.succeeded", charge(who, 2, paid + timedelta(days=1), amount=1500, currency="eur"))
    await send(client, "charge.dispute.created", {**dispute(who, 2, reason="product_not_received"), "amount": 1500,
                                                  "currency": "eur"})
    note = ("15.00 EUR is not the price of Pro. This Stripe customer has paid for Okwan Pro, and the Stripe account "
            "takes other products' payments too: check in Stripe that this charge is for Okwan Pro before answering "
            "for it as one.")
    assert f"Note:     {note}" in outbox.sent[-1]["text"]
    pack = await disputes.evidence(await store.dispute(who.dp(2)), NOW)
    assert pack["fields"]["product_description"]["add"] == note
    # The Okwan payment a day later is not answered as a duplicate of it.
    await send(client, "charge.succeeded", charge(who, 3, paid + timedelta(days=2)))
    await send(client, "charge.dispute.created", dispute(who, 3, reason="duplicate"))
    assert "Note:" not in outbox.sent[-1]["text"], "a payment of Pro's price carries no note"
    fields = (await disputes.evidence(await store.dispute(who.dp(3)), NOW))["fields"]
    assert fields["product_description"]["add"] is None
    assert fields["duplicate_charge_id"]["text"] == who.charge(1), "the payment of the same amount before it"
    assert "accept the dispute" in fields["duplicate_charge_explanation"]["add"], "two days apart: that one is real"
    # With no earlier payment of that amount, there is nothing to call a duplicate.
    alone = await paying(store, paid_at=paid)
    await send(client, "charge.succeeded", charge(alone, 1, paid, amount=1500, currency="eur"))
    await send(client, "charge.succeeded", charge(alone, 2, paid + timedelta(days=1)))
    await send(client, "charge.dispute.created", dispute(alone, 2, reason="duplicate"))
    fields = (await disputes.evidence(await store.dispute(alone.dp(2)), NOW))["fields"]
    assert fields["duplicate_charge_id"] == {
        "text": None, "add": "No earlier payment of this amount by this account is on record."}


def test_a_currency_counted_in_thousandths_is_read_in_thousandths():
    """Stripe sends Kuwaiti, Bahraini, Jordanian and Tunisian dinars and
    Omani rials in thousandths. They were read as hundredths, so another
    product's 9.900 KWD dispute was mailed as 99.00 KWD."""
    for currency in ("kwd", "bhd", "jod", "omr", "tnd"):
        assert disputes.amount({"amount_minor": 9900, "currency": currency}) == f"9.900 {currency.upper()}"
    assert disputes.amount({"amount_minor": 9900, "currency": "usd"}) == "$99.00"
    assert disputes.amount({"amount_minor": 9900, "currency": "jpy"}) == "9,900 JPY"


# ── only the operator ────────────────────────────────────────────────

async def _operator(store, monkeypatch, *, unlocked: bool = True) -> dict:
    email = f"ops-{store.tag}{os.urandom(2).hex()}@okwan.test"
    monkeypatch.setenv("OKWAN_OPERATOR_EMAILS", email)
    hq = await store.create_tenant("Okwan HQ")
    await store.add_account(hq.id, email, accounts.hash_password("correct horse battery"))
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(hq.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    if unlocked:
        await store.set_session_operator(token_hash, datetime.now(UTC) + timedelta(minutes=20))
    return {"Authorization": f"Bearer {full}"}


async def test_the_console_lists_what_waits_first_with_the_days_left_and_the_rate(client, store, outbox, monkeypatch):
    who = await paying(store)
    for n in range(1, 5):
        await send(client, "charge.succeeded", charge(who, n))
    real = datetime.now(UTC)
    await send(client, "charge.dispute.created", dispute(who, 1, due=real + timedelta(days=9, hours=2)))
    await send(client, "charge.dispute.created", dispute(who, 2, due=real + timedelta(days=2, hours=2),
                                                         opened=NOW - timedelta(days=5)))
    await send(client, "charge.dispute.created", dispute(who, 3, status="won", sent=1, opened=NOW - timedelta(days=20)))
    await send(client, "charge.dispute.created", dispute(who, 4, status="under_review", sent=1,
                                                         opened=NOW - timedelta(days=1)))
    ops = await _operator(store, monkeypatch)
    listed = (await client.get("/v1/ops/disputes", headers=ops)).json()
    assert [(r["id"], r["needs_answer"]) for r in listed["data"]] == [
        (who.dp(2), True), (who.dp(1), True), (who.dp(4), False), (who.dp(3), False)]
    first = listed["data"][0]
    assert (first["amount"], first["reason"], first["days_left"], first["stage"], first["matched"]) == (
        "$99.00", "subscription_canceled", 2, "open", True)
    assert first["account"] == {"id": who.tenant.id, "name": "Acme ISV"}
    assert listed["activity"]["disputes"] == 4 and listed["activity"]["said"].startswith("Okwan's payments, last 30 days")

    one = (await client.get(f"/v1/ops/disputes/{who.dp()}", headers=ops)).json()
    assert one["stripe_url"] == f"https://dashboard.stripe.com/payments/{who.charge()}"
    assert one["evidence"]["fields"]["customer_purchase_ip"]["text"] == "203.0.113.24"
    assert (await client.get("/v1/ops/disputes/dp_nothing", headers=ops)).status_code == 404
    actions = [(a["action"], a["target"]) for a in await store.operator_log(10)]
    assert ("view_dispute", who.dp()) in actions and ("list_disputes", None) in actions


async def test_no_one_but_an_unlocked_operator_sees_a_dispute(client, store, outbox, monkeypatch):
    who = await disputed(client, store)
    locked = await _operator(store, monkeypatch, unlocked=False)
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(who.tenant.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    key, _ = await store.issue_key(who.tenant.id, scope="admin")
    for path in ("/v1/ops/disputes", f"/v1/ops/disputes/{who.dp()}"):
        assert (await client.get(path)).status_code == 404
        assert (await client.get(path, headers=locked)).status_code == 403
        assert (await client.get(path, headers={"Authorization": f"Bearer {full}"})).status_code == 404, "the customer"
        assert (await client.get(path, headers={"Authorization": f"Bearer {key}"})).status_code == 404
        assert (await client.get(path, headers={"X-Okwan-Key": key})).status_code == 404
