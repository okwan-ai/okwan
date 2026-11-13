"""A dispute on one of Okwan's own payments is known the hour it arrives,
measured against the payments around it, and answered from what is
already put together.

What must hold:

- Stripe's signed events are kept: each payment taken, each dispute, each
  bank's early warning. The newest event wins whatever order they arrive
  in, and an event that cannot be read is acknowledged, never a crash;
- the operator is mailed when a dispute or warning arrives and when a
  dispute is decided, once each; a mail that was not accepted is sent on
  the next round; an open dispute with no answer is mailed again inside
  three days of its date, once a day;
- the rate is disputes in the last 30 days against payments in them, set
  beside 0.5% and 0.75%;
- the evidence is what the record shows, in the fields of Stripe's form:
  the customer, the address they paid from, the policy in the version
  they were shown and when, and their own use of the service after the
  payment. Okwan's scheduled reads are not the customer's use;
- only an unlocked operator sees any of it, and each look is logged.

Stripe and the mail provider are fakes. Both stores.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from okwan_api import disputes, policy
from okwan_api.main import app
from okwan_vault import EnvMasterKey, MemoryStore, PostgresStore, accounts, new_key
from okwan_vault.accounts import SESSION_PREFIX, new_token

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
SECRET = "whsec_test_billing_secret"
INBOX = "owner-inbox@okwan.test"
NOW = datetime.now(UTC).replace(microsecond=0)


def ts(at: datetime) -> int:
    return int(at.timestamp())


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
    await s.pool.execute("DELETE FROM billing_disputes; DELETE FROM billing_payments")
    try:
        yield s
    finally:
        await s.pool.execute("DELETE FROM billing_disputes; DELETE FROM billing_payments")
        for tenant_id in reversed(created):
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await s.pool.execute("DELETE FROM operator_factors WHERE email LIKE 'ops-%@okwan.test'")
        await s.close()


class Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.refuse = False

    async def send_verification(self, email, link):  # pragma: no cover
        raise AssertionError("not here")

    async def send_notice(self, to, subject, text, html=None, reply_to=None, headers=None):
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


async def send(client, kind: str, obj: dict, created: datetime | None = None, signed: bool = True) -> httpx.Response:
    body = json.dumps({"type": kind, "created": ts(created or NOW), "data": {"object": obj}}).encode()
    stamp = int(time.time())
    sig = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return await client.post("/v1/billing/webhook", content=body, headers={
        "Stripe-Signature": f"t={stamp},v1={sig if signed else '0' * 64}", "Content-Type": "application/json"})


class Paying:
    """A paying account: linked to its Stripe customer, with a paid checkout on record."""

    def __init__(self, tenant, email, customer, tag):
        self.tenant, self.email, self.customer, self.tag = tenant, email, customer, tag

    def charge(self, n: int = 1) -> str:
        return f"ch_{self.tag}_{n}"

    def dp(self, n: int = 1) -> str:
        return f"dp_{self.tag}_{n}"


async def paying(store, name: str = "Acme ISV", *, checkout: bool = True, paid_at: datetime | None = None) -> Paying:
    tag = os.urandom(4).hex()
    tenant = await store.create_tenant(name)
    email = f"founder-{tag}@acme.test"
    await store.add_account(tenant.id, email, accounts.hash_password("correct horse battery"))
    await store.save_billing(tenant.id, customer_id=f"cus_{tag}", subscription_id=f"sub_{tag}", status="active",
                             cancel_at_period_end=False, current_period_end=NOW + timedelta(days=20))
    if checkout:
        opened = (paid_at or NOW - timedelta(days=10)) - timedelta(minutes=3)
        await store.open_checkout(tenant.id, f"cs_{tag}", policy_version=policy.VERSION, at=opened, ip="203.0.113.24")
        await store.checkout_paid(tenant.id, f"cs_{tag}", at=opened + timedelta(minutes=3),
                                  subscription_id=f"sub_{tag}", terms_accepted_at=opened + timedelta(minutes=3))
    return Paying(tenant, email, f"cus_{tag}", tag)


def charge(who: Paying, n: int = 1, at: datetime | None = None, **over) -> dict:
    return {"id": who.charge(n), "object": "charge", "amount": 9900, "currency": "usd", "paid": True,
            "customer": who.customer, "created": ts(at or NOW - timedelta(days=10)), **over}


def dispute(who: Paying, n: int = 1, *, status: str = "needs_response", reason: str = "subscription_canceled",
            due: datetime | None = None, has_evidence: bool = False, opened: datetime | None = None, **over) -> dict:
    return {"id": who.dp(n), "object": "dispute", "amount": 9900, "currency": "usd", "charge": who.charge(n),
            "reason": reason, "status": status, "created": ts(opened or NOW),
            "evidence_details": {"due_by": ts(due or NOW + timedelta(days=9, hours=2)), "has_evidence": has_evidence,
                                 "past_due": False, "submission_count": int(has_evidence)}, **over}


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
    given = {"customer_name", "customer_email_address", "customer_purchase_ip", "product_description",
             "service_date", "refund_policy_disclosure", "cancellation_policy_disclosure",
             "refund_refusal_explanation", "cancellation_rebuttal", "access_activity_log", "duplicate_charge_id",
             "duplicate_charge_explanation", "uncategorized_text"}
    for reason in (*disputes.REASONS.values(), disputes.OTHER_REASON):
        assert set(reason["fields"]) <= given
    assert f"{policy.PRO_PRICE_USD} US dollars a month" in disputes.PRODUCT


# ── kept from Stripe's events ────────────────────────────────────────

async def test_a_payment_taken_is_kept_under_its_account(client, store):
    who = await paying(store)
    r = await send(client, "charge.succeeded", charge(who))
    assert r.json() == {"received": True, "payment": who.charge()}
    paid = await store.payment(who.charge())
    assert (paid["tenant_id"], paid["customer_id"], paid["amount_minor"], paid["currency"]) == (
        who.tenant.id, who.customer, 9900, "usd")
    assert paid["paid_at"] == NOW - timedelta(days=10)
    # Sent again, or with other figures: the first record stands.
    await send(client, "charge.succeeded", charge(who, amount=1))
    assert (await store.payment(who.charge()))["amount_minor"] == 9900


async def test_a_dispute_is_kept_and_the_operator_is_mailed_the_account_amount_reason_and_date(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    r = await send(client, "charge.dispute.created", dispute(who))
    assert r.json() == {"received": True, "dispute": who.dp(), "outcome": "new"}
    row = await store.dispute(who.dp())
    assert (row["kind"], row["tenant_id"], row["charge_id"], row["status"], row["reason"]) == (
        "dispute", who.tenant.id, who.charge(), "needs_response", "subscription_canceled")
    assert (row["amount_minor"], row["currency"], row["has_evidence"]) == (9900, "usd", False)
    assert row["due_by"] == NOW + timedelta(days=9, hours=2) and row["reminded_at"] is not None

    [mail] = outbox.sent
    due = NOW + timedelta(days=9, hours=2)
    assert mail["to"] == INBOX
    assert mail["subject"] == f"[Dispute] $99.00 disputed, answer due {due.day} {due:%b %Y}"
    assert "Acme" not in mail["subject"], "an account's name is never in a subject"
    for said in (f"Acme ISV ({who.tenant.id}), {who.email}", "$99.00, taken", who.charge(),
                 "subscription_canceled. The cardholder says they were charged after cancelling.",
                 "(9 days). After that date the dispute is lost, and an answer can be sent once.",
                 f"https://app.okwan.test/ops/disputes/{who.dp()}",
                 "Last 30 days: 1 disputed and 0 flagged by a bank, against 1 payment: 100.00% disputed."):
        assert said in mail["text"], said


async def test_a_dispute_on_a_payment_not_on_record_is_still_kept_and_mailed(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.dispute.created", dispute(who))
    assert (await store.dispute(who.dp()))["tenant_id"] is None
    [mail] = outbox.sent
    assert "an account Okwan could not match to the payment" in mail["text"]
    assert "No payment is on record in those days, so there is no rate to give." in mail["text"]


async def test_a_payment_that_arrived_before_its_customer_was_linked_still_finds_its_account(client, store, outbox):
    tag = os.urandom(4).hex()
    tenant = await store.create_tenant("Linked later")
    who = Paying(tenant, "", f"cus_{tag}", tag)
    await send(client, "charge.succeeded", charge(who))
    assert (await store.payment(who.charge()))["tenant_id"] is None, "Stripe's checkout event had not landed yet"
    await store.save_billing(tenant.id, customer_id=who.customer, subscription_id="sub_1", status="active",
                             cancel_at_period_end=False, current_period_end=None)
    await send(client, "charge.dispute.created", dispute(who))
    assert (await store.dispute(who.dp()))["tenant_id"] == tenant.id


async def test_the_newest_event_wins_whatever_order_they_arrive_in(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    answered = dispute(who, status="under_review", has_evidence=True)
    assert (await send(client, "charge.dispute.updated", answered, NOW + timedelta(hours=5))).json()["outcome"] == "new"
    late = await send(client, "charge.dispute.created", dispute(who), NOW)
    assert late.json()["outcome"] == "stale"
    row = await store.dispute(who.dp())
    assert (row["status"], row["has_evidence"]) == ("under_review", True)
    assert len(outbox.sent) == 1, "told once, by the event that arrived first"


async def test_a_decided_dispute_is_mailed_once(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.created", dispute(who))
    await send(client, "charge.dispute.updated", dispute(who, status="under_review", has_evidence=True),
               NOW + timedelta(days=1))
    assert len(outbox.sent) == 1, "an answer going in is not news to the person who sent it"
    won = dispute(who, status="won", has_evidence=True)
    await send(client, "charge.dispute.closed", won, NOW + timedelta(days=30))
    await send(client, "charge.dispute.closed", won, NOW + timedelta(days=30))
    await send(client, "charge.dispute.funds_reinstated", won, NOW + timedelta(days=30, minutes=1))
    assert [m["subject"] for m in outbox.sent[1:]] == ["[Dispute won] The $99.00 dispute is closed"]
    row = await store.dispute(who.dp())
    assert row["status"] == "won" and row["closed_at"] is not None


async def test_a_banks_early_warning_is_kept_apart_from_disputes(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    warning = {"id": f"issfr_{who.tag}", "object": "radar.early_fraud_warning", "charge": who.charge(),
               "fraud_type": "made_with_stolen_card", "actionable": True, "created": ts(NOW)}
    assert (await send(client, "radar.early_fraud_warning.created", warning)).json()["outcome"] == "new"
    row = await store.dispute(f"issfr_{who.tag}")
    assert (row["kind"], row["status"], row["reason"], row["amount_minor"], row["due_by"]) == (
        "warning", "warning", "made_with_stolen_card", 9900, None)
    [mail] = outbox.sent
    assert mail["subject"] == "[Payment flagged] A bank has flagged a $99.00 payment as possible fraud"
    assert "No money has moved and nothing has to be answered." in mail["text"]
    act = await disputes.activity(NOW)
    assert (act["disputes"], act["warnings"], act["payments"], act["rate"]) == (0, 1, 1, "0.00%")


@pytest.mark.parametrize("obj", [
    {}, {"id": "dp_x"}, {"charge": "ch_x"}, {"id": 7, "charge": ["ch_x"]}, {"id": "", "charge": "ch_x"},
    {"id": "dp_" + "x" * 400, "charge": "ch_x"}, {"id": None, "charge": None, "evidence_details": "soon"},
])
async def test_an_event_that_cannot_be_read_is_acknowledged_and_kept_out(client, store, outbox, obj):
    assert (await send(client, "charge.dispute.created", obj)).json() == {"received": True}
    assert (await send(client, "charge.succeeded", obj)).json() == {"received": True}
    assert await store.disputes() == [] and outbox.sent == []


async def test_odd_values_in_a_readable_event_do_not_break_it(client, store, outbox):
    who = await paying(store)
    odd = dispute(who, reason=None, amount="9900", currency=7, status=None,
                  evidence_details={"due_by": "tomorrow", "has_evidence": "yes"}, created="then")
    assert (await send(client, "charge.dispute.created", odd)).json()["outcome"] == "new"
    row = await store.dispute(who.dp())
    assert (row["amount_minor"], row["currency"], row["reason"], row["status"], row["due_by"], row["has_evidence"]) == (
        None, None, None, "needs_response", None, False)
    [mail] = outbox.sent
    assert "an amount Stripe did not give" in mail["subject"] and "no date given by Stripe" in mail["text"]


async def test_an_unsigned_event_keeps_nothing(client, store, outbox):
    who = await paying(store)
    assert (await send(client, "charge.dispute.created", dispute(who), signed=False)).status_code == 400
    assert (await send(client, "charge.succeeded", charge(who), signed=False)).status_code == 400
    assert await store.disputes() == [] and await store.payment(who.charge()) is None and outbox.sent == []


async def test_a_merchants_name_cannot_write_the_operators_mail(client, store, outbox):
    who = await paying(store, "Acme\nBcc: thief@evil.test\nhttps://evil.test/pay now")
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.created", dispute(who))
    [mail] = outbox.sent
    line = next(text for text in mail["text"].split("\n") if text.startswith("Account:"))
    assert who.tenant.id in line and "https://" not in line and "\nBcc:" not in mail["text"]


# ── the rate ─────────────────────────────────────────────────────────

async def _seed(store, payments: int, disputed: int, at: datetime, tag: str) -> None:
    for n in range(payments):
        await store.note_payment(f"ch_{tag}_{n}", tenant_id=None, customer_id="cus_x", amount_minor=9900,
                                 currency="usd", paid_at=at)
    for n in range(disputed):
        await store.save_dispute(f"dp_{tag}_{n}", kind="dispute", charge_id=f"ch_{tag}_{n}", tenant_id=None,
                                 amount_minor=9900, currency="usd", reason="general", status="needs_response",
                                 due_by=None, has_evidence=False, opened_at=at, closed_at=None, event_at=at)


@pytest.mark.parametrize(("payments", "disputed", "rate", "over"), [
    (12, 1, "8.33%", [True, True]), (400, 1, "0.25%", [False, False]), (200, 1, "0.50%", [True, False]),
    (400, 3, "0.75%", [True, True]), (150, 1, "0.66%", [True, False]), (40, 0, "0.00%", [False, False]),
])
async def test_the_rate_is_disputes_against_payments_beside_the_two_lines(store, outbox, payments, disputed, rate, over):
    from okwan_api.auth import set_store

    set_store(store)
    await _seed(store, payments, disputed, NOW - timedelta(days=3), os.urandom(3).hex())
    await _seed(store, 5, 5, NOW - timedelta(days=31), os.urandom(3).hex())     # outside the 30 days
    act = await disputes.activity(NOW)
    assert (act["payments"], act["disputes"], act["rate"]) == (payments, disputed, rate)
    assert [line["over"] for line in act["lines"]] == over
    assert [line["at"] for line in act["lines"]] == ["0.50%", "0.75%"]
    said = disputes.activity_line(act)
    assert ("That is over" in said) == any(over)


async def test_no_payments_is_no_rate_not_a_division(store, outbox):
    from okwan_api.auth import set_store

    set_store(store)
    act = await disputes.activity(NOW)
    assert (act["payments"], act["rate"], act["rate_hundredths"]) == (0, None, None)
    assert "there is no rate to give" in disputes.activity_line(act)


# ── mailed again when it has to be ───────────────────────────────────

async def test_a_mail_that_was_not_accepted_is_sent_on_the_next_round(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    outbox.refuse = True
    assert (await send(client, "charge.dispute.created", dispute(who))).json()["outcome"] == "new"
    assert outbox.sent == [] and (await store.dispute(who.dp()))["reminded_at"] is None
    outbox.refuse = False
    assert await disputes.remind(NOW + timedelta(hours=1)) == 1
    [mail] = outbox.sent
    assert "was not able to mail about when it arrived" in mail["text"] and who.charge() in mail["text"]
    assert await disputes.remind(NOW + timedelta(hours=2)) == 0, "and then not again"


async def test_an_unanswered_dispute_is_mailed_again_inside_three_days_of_its_date_once_a_day(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    due = NOW + timedelta(days=9)
    await send(client, "charge.dispute.created", dispute(who, due=due))
    assert await disputes.remind(due - timedelta(days=4)) == 0, "not yet"
    assert await disputes.remind(due - timedelta(days=3)) == 1
    assert await disputes.remind(due - timedelta(days=3) + timedelta(hours=5)) == 0, "once a day"
    assert await disputes.remind(due - timedelta(days=2) + timedelta(minutes=1)) == 1
    assert outbox.sent[-1]["subject"] == f"[Dispute due] $99.00 dispute unanswered, due {due.day} {due:%b %Y}"
    # Answered: no more reminders. Decided: none either.
    await send(client, "charge.dispute.updated", dispute(who, due=due, status="under_review", has_evidence=True),
               NOW + timedelta(days=7))
    assert await disputes.remind(due - timedelta(hours=6)) == 0
    assert len(outbox.sent) == 3


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
    assert pack["reason"]["code"] == "subscription_canceled" and "cancellation_rebuttal" in pack["for_this_reason"]
    assert (f["customer_name"], f["customer_email_address"], f["customer_purchase_ip"]) == (
        "Acme ISV", who.email, "203.0.113.24")
    assert f["product_description"] == disputes.PRODUCT
    assert f["service_date"].startswith(disputes.day(paid_at))
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
    assert "(2 on record)" in log[0]
    assert log[1:] == [f"{disputes.stamp(paid_at + timedelta(days=1))}  198.51.100.9  sql:/v1/query",
                       f"{disputes.stamp(paid_at + timedelta(days=2))}  198.51.100.7  rest:/v1/square/payments/list"]
    assert "okw_abcd1234" not in f["access_activity_log"] and "schedule" not in f["access_activity_log"]
    assert f["duplicate_charge_id"] == who.charge(0)
    assert f"{who.charge(0)} was taken {disputes.day(earlier)}" in f["duplicate_charge_explanation"]
    assert [a["field"] for a in pack["attach"]] == ["refund_policy", "cancellation_policy", "customer_communication",
                                                    "receipt"]
    assert 0 < pack["characters"] < 150_000, "Stripe's limit across every field"


async def test_the_access_log_in_an_answer_is_bounded(client, store, outbox):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    for n in range(disputes.LOG_LINES + 15):
        await store.record_access(who.tenant.id, credential="okw_abcd1234", ip="198.51.100.7",
                                  network="198.51.100.0/24", surface="rest:/v1/stripe/charges/list",
                                  at=NOW - timedelta(days=9) + timedelta(minutes=n))
    await send(client, "charge.dispute.created", dispute(who))
    log = (await disputes.evidence(await store.dispute(who.dp()), NOW))["fields"]["access_activity_log"]["text"]
    assert len(log.split("\n")) == disputes.LOG_LINES + 1
    assert f"({disputes.LOG_LINES + 15} on record, the first {disputes.LOG_LINES} shown)" in log


async def test_what_is_not_on_record_is_said_to_be_missing_never_filled_in(client, store, outbox):
    who = await paying(store, checkout=False)
    await send(client, "charge.dispute.created", dispute(who, reason="fraudulent"))     # payment never reported
    pack = await disputes.evidence(await store.dispute(who.dp()), NOW)
    fields = pack["fields"]
    for name in ("customer_name", "customer_email_address", "customer_purchase_ip", "service_date",
                 "access_activity_log", "duplicate_charge_id", "duplicate_charge_explanation",
                 "cancellation_rebuttal"):
        assert fields[name]["text"] is None, name
    assert fields["customer_name"]["add"].startswith("The account could not be matched")
    assert "No checkout is on record" in fields["refund_policy_disclosure"]["add"]
    assert pack["reason"]["says"] == "The cardholder says they did not authorise the payment."

    # With the payment on record but no checkout: the customer is known, the address is not.
    await send(client, "charge.succeeded", charge(who, 2))
    await send(client, "charge.dispute.created", dispute(who, 2, reason="nothing Stripe has named yet"))
    pack = await disputes.evidence(await store.dispute(who.dp(2)), NOW)
    assert pack["fields"]["customer_name"]["text"] == "Acme ISV"
    assert pack["fields"]["customer_purchase_ip"] == {"text": None, "add": "No checkout is on record for this payment."}
    assert pack["reason"]["says"] == disputes.OTHER_REASON["says"]


async def test_a_payment_under_an_earlier_policy_is_answered_with_that_version(client, store, outbox, monkeypatch):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.created", dispute(who, reason="credit_not_processed"))
    earlier = {**policy.said(), "text": "THE POLICY AS IT WAS (version 2026-11-13)\n",
               "disclosure": "How it was shown then."}
    monkeypatch.setattr(policy, "ARCHIVE", {policy.VERSION: earlier})
    monkeypatch.setattr(policy, "VERSION", "2027-02-01")
    fields = (await disputes.evidence(await store.dispute(who.dp()), NOW))["fields"]
    assert fields["uncategorized_text"]["text"] == earlier["text"]
    assert fields["refund_policy_disclosure"]["text"].startswith("How it was shown then. This customer opened")
    # A version that is not on file is said to be missing, not answered with today's words.
    monkeypatch.setattr(policy, "ARCHIVE", {})
    fields = (await disputes.evidence(await store.dispute(who.dp()), NOW))["fields"]
    assert fields["uncategorized_text"]["text"] is None and "is not on file" in fields["uncategorized_text"]["add"]
    assert "is not on file" in fields["refund_policy_disclosure"]["add"]
    assert policy.disclosure() not in (fields["refund_policy_disclosure"]["text"] or "")


# ── only the operator ────────────────────────────────────────────────

async def _operator(store, monkeypatch, *, unlocked: bool = True) -> dict:
    email = f"ops-{os.urandom(3).hex()}@okwan.test"
    monkeypatch.setenv("OKWAN_OPERATOR_EMAILS", email)
    hq = await store.create_tenant("Okwan HQ")
    await store.add_account(hq.id, email, accounts.hash_password("correct horse battery"))
    full, token_hash = new_token(SESSION_PREFIX)
    await store.create_session(hq.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    if unlocked:
        await store.set_session_operator(token_hash, datetime.now(UTC) + timedelta(minutes=20))
    return {"Authorization": f"Bearer {full}"}


async def test_the_console_lists_disputes_with_the_days_left_and_the_rate(client, store, outbox, monkeypatch):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.created", dispute(who, due=datetime.now(UTC) + timedelta(days=9, hours=2)))
    ops = await _operator(store, monkeypatch)
    listed = (await client.get("/v1/ops/disputes", headers=ops)).json()
    [row] = listed["data"]
    assert (row["id"], row["amount"], row["reason"], row["days_left"], row["needs_answer"], row["ended"]) == (
        who.dp(), "$99.00", "subscription_canceled", 9, True, False)
    assert row["account"] == {"id": who.tenant.id, "name": "Acme ISV"}
    assert listed["activity"]["rate"] == "100.00%" and listed["activity"]["said"].startswith("Last 30 days: 1 disputed")

    # What waits for an answer comes first, the nearest date at the top; then the rest, newest first.
    soon = datetime.now(UTC) + timedelta(days=2, hours=2)
    await send(client, "charge.dispute.created", dispute(who, 2, due=soon, opened=NOW - timedelta(days=5)))
    await send(client, "charge.dispute.created", dispute(who, 3, status="won", has_evidence=True,
                                                         opened=NOW - timedelta(days=20)))
    await send(client, "charge.dispute.created", dispute(who, 4, status="under_review", has_evidence=True,
                                                         opened=NOW - timedelta(days=1)))
    order = [(r["id"], r["days_left"]) for r in (await client.get("/v1/ops/disputes", headers=ops)).json()["data"]]
    assert order == [(who.dp(2), 2), (who.dp(), 9), (who.dp(4), 9), (who.dp(3), 9)]

    one = (await client.get(f"/v1/ops/disputes/{who.dp()}", headers=ops)).json()
    assert one["stripe_url"] == f"https://dashboard.stripe.com/payments/{who.charge()}"
    assert one["evidence"]["fields"]["customer_purchase_ip"]["text"] == "203.0.113.24"
    assert (await client.get("/v1/ops/disputes/dp_nothing", headers=ops)).status_code == 404
    actions = [(a["action"], a["target"]) for a in await store.operator_log(10)]
    assert ("view_dispute", who.dp()) in actions and ("list_disputes", None) in actions


async def test_no_one_but_an_unlocked_operator_sees_a_dispute(client, store, outbox, monkeypatch):
    who = await paying(store)
    await send(client, "charge.succeeded", charge(who))
    await send(client, "charge.dispute.created", dispute(who))
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
