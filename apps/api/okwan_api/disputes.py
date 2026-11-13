"""Disputes on Okwan's own payments: known the hour they arrive, measured
before a card network measures them, and answered from what is already
put together.

A cardholder can ask their bank to take back a payment for Pro. The bank
decides on what the seller shows it, by a date (7 to 21 days, by card
network); a missed date is a lost dispute, an answer can be sent once,
and every dispute counts against the account whether it is won or lost.
So, from Stripe's signed events on the billing webhook:

* **Known when it arrives.** A dispute, and a bank's early warning that
  one may follow, is kept and the operator is mailed the account, the
  amount, the bank's reason and the date an answer is due. An open one
  with no answer is mailed again as the date nears. Each payment Stripe
  reports succeeding is kept too: it is how a disputed charge finds its
  account, and what dispute activity is measured against.
* **The rate.** Disputes in the last 30 days against payments in the same
  30 days, beside the two lines Stripe's documentation gives: 0.5%,
  Visa's monitoring line, and 0.75%, the level it calls excessive.
* **The answer, prepared.** `evidence()` puts what Okwan holds into the
  fields of Stripe's dispute form: who the customer is, what they bought
  and when, the address they bought from, their use of the service since,
  the refund policy in the version they were shown, and how and when it
  was shown. A person reads it, adds what only a person knows, and
  submits it in Stripe. Okwan does not submit it and its Stripe key
  cannot.

Nothing here is a connector and nothing reads or writes a customer's
rails. Amounts are minor units, as Stripe sends them.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from okwan_core.currency import format_minor

from . import policy
from .auth import get_store
from .mail import get_mailer
from .schedule import shown

logger = logging.getLogger("okwan.disputes")

#: What dispute activity is measured over, as the card networks measure a month.
WINDOW = timedelta(days=30)
#: The lines, in hundredths of a percent, with whose they are (Stripe's
#: documentation, read 2026-11-13).
LINES: tuple[tuple[int, str], ...] = (
    (50, "0.5%, Visa's monitoring line"),
    (75, "0.75%, the level Stripe's documentation calls excessive"),
)
#: An open dispute with no answer is mailed again from this long before its date.
REMIND_WITHIN = timedelta(days=3)
REMIND_EVERY = timedelta(hours=24)
SWEEP_SECONDS = 3600.0
#: The most lines of the access log one answer carries. Stripe takes
#: 150,000 characters across every field.
LOG_LINES = 60

DISPUTE, WARNING = "dispute", "warning"
#: States in which the bank is waiting for the seller.
NEEDS_ANSWER = frozenset({"needs_response", "warning_needs_response"})
ENDED = frozenset({"won", "lost", "warning_closed", "prevented"})

PRODUCT = (
    "Okwan Pro: a monthly subscription to Okwan's hosted service, which reads the customer's own payment and "
    "commerce systems and serves that data to the customer over an API, SQL and a dashboard, with 100,000 "
    f"requests a month. {policy.PRO_PRICE_USD} US dollars a month, billed monthly in advance by card. A digital "
    "service: nothing is shipped."
)

#: The bank's reason, as Stripe names it: what the cardholder is saying,
#: what overturns it, and which of the form's fields carry that (Stripe's
#: documentation, "Dispute reason codes", for a digital service).
REASONS: dict[str, dict[str, Any]] = {
    "fraudulent": {
        "says": "The cardholder says they did not authorise the payment.",
        "show": "That the account's own owner made the payment: who they are, the address they paid from, and "
                "their use of the service after paying.",
        "fields": ("customer_name", "customer_email_address", "customer_purchase_ip", "access_activity_log",
                   "product_description", "service_date"),
    },
    "unrecognized": {
        "says": "The cardholder does not recognise the payment on their statement.",
        "show": "That the account's own owner made the payment: who they are, the address they paid from, and "
                "their use of the service after paying.",
        "fields": ("customer_name", "customer_email_address", "customer_purchase_ip", "access_activity_log",
                   "product_description", "service_date"),
    },
    "subscription_canceled": {
        "says": "The cardholder says they were charged after cancelling.",
        "show": "That the subscription was still active when the payment was taken, and that the customer was "
                "shown how to cancel before paying.",
        "fields": ("cancellation_policy_disclosure", "cancellation_rebuttal", "access_activity_log",
                   "uncategorized_text"),
    },
    "credit_not_processed": {
        "says": "The cardholder says they were owed a refund that has not been given.",
        "show": "That a refund was already given, or that none was due under the policy the customer was shown "
                "before paying.",
        "fields": ("refund_policy_disclosure", "refund_refusal_explanation", "uncategorized_text"),
    },
    "product_not_received": {
        "says": "The cardholder says the service was not provided.",
        "show": "That the service was provided: the customer's use of it after paying, with times and addresses.",
        "fields": ("customer_name", "customer_email_address", "customer_purchase_ip", "access_activity_log",
                   "service_date"),
    },
    "product_unacceptable": {
        "says": "The cardholder says the service was not as described.",
        "show": "What was offered before purchase, and the customer's use of it.",
        "fields": ("product_description", "access_activity_log", "service_date"),
    },
    "duplicate": {
        "says": "The cardholder says they were charged twice for the same thing.",
        "show": "That each payment was for a different month.",
        "fields": ("duplicate_charge_id", "duplicate_charge_explanation", "service_date"),
    },
}
OTHER_REASON = {
    "says": "The bank gave a reason with no category of its own.",
    "show": "Ask the customer what they are disputing; send what bears on it.",
    "fields": ("customer_name", "customer_email_address", "customer_purchase_ip", "access_activity_log",
               "product_description", "service_date", "refund_policy_disclosure", "uncategorized_text"),
}
#: What the form asks for as a file, which a person attaches in Stripe.
ATTACH: tuple[tuple[str, str], ...] = (
    ("refund_policy", "The refund policy as shown: print this page to a PDF, or save okwan.ai/refunds as one."),
    ("cancellation_policy", "The same file: the policy covers cancelling."),
    ("customer_communication", "Any email with the customer about this payment, a refund or cancelling."),
    ("receipt", "Stripe's receipt or invoice for the payment, from the payment's page in Stripe."),
)


# ── small things ─────────────────────────────────────────────────────

def _when(value: Any) -> datetime | None:
    """A Stripe timestamp (seconds) as a time; None for anything else."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < 4_102_444_800:
        return None
    return datetime.fromtimestamp(int(value), UTC)


def _id(value: Any) -> str:
    """A Stripe id, or the id of an expanded object; "" for anything else."""
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) and 0 < len(value) <= 255 else ""


def _minor(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and abs(value) < 10**15 else None


def _word(value: Any, limit: int = 64) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def day(at: datetime | None) -> str:
    return f"{at.day} {at:%b %Y}" if at else "no date"


def stamp(at: datetime | None) -> str:
    return f"{at:%Y-%m-%d %H:%M:%S} UTC" if at else "no time"


def days_left(due_by: datetime | None, now: datetime) -> int | None:
    """Whole days until an answer is due; negative once it has passed."""
    return None if due_by is None else (due_by - now) // timedelta(days=1)


def amount(row: dict[str, Any]) -> str:
    if row.get("amount_minor") is None:
        return "an amount Stripe did not give"
    return format_minor(row["amount_minor"], row.get("currency"))


def percent(hundredths: int) -> str:
    return f"{hundredths // 100}.{hundredths % 100:02d}%"


def console_link(dispute_id: str) -> str:
    app = (os.environ.get("OKWAN_DASHBOARD_URL", "").strip() or "http://localhost:3000").rstrip("/")
    return f"{app}/ops/disputes/{dispute_id}"


# ── from Stripe's events ─────────────────────────────────────────────

def handles(kind: str) -> bool:
    return kind == "charge.succeeded" or kind.startswith(("charge.dispute.", "radar.early_fraud_warning."))


async def _account_of(charge_id: str) -> str | None:
    """The account a charge was for: the one its payment was filed under,
    or, if the payment arrived before its customer was linked, the one
    that customer has been linked to since."""
    store = get_store()
    paid = await store.payment(charge_id)
    if paid is None:
        return None
    if paid["tenant_id"]:
        return paid["tenant_id"]
    return await store.tenant_for_billing_customer(paid["customer_id"]) if paid["customer_id"] else None


async def handle(kind: str, obj: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """One signed event. Never raises on what Stripe sent: an event this
    cannot read is acknowledged and logged, not retried for three days."""
    store = get_store()
    event_at = _when(event.get("created")) or datetime.now(UTC)
    if kind == "charge.succeeded":
        charge_id, paid_minor = _id(obj.get("id")), _minor(obj.get("amount"))
        if not charge_id or paid_minor is None or obj.get("paid") is False:
            return {"received": True}
        customer = _id(obj.get("customer"))
        await store.note_payment(
            charge_id, tenant_id=await store.tenant_for_billing_customer(customer) if customer else None,
            customer_id=customer, amount_minor=paid_minor, currency=_word(obj.get("currency"), 8) or "",
            paid_at=_when(obj.get("created")) or event_at)
        return {"received": True, "payment": charge_id}

    warning = kind.startswith("radar.early_fraud_warning.")
    dispute_id, charge_id = _id(obj.get("id")), _id(obj.get("charge"))
    if not dispute_id or not charge_id:
        logger.warning("disputes: %s without an id or a charge, not kept", kind)
        return {"received": True}
    details = obj.get("evidence_details") if isinstance(obj.get("evidence_details"), dict) else {}
    if warning:
        paid = await store.payment(charge_id)
        status = "warning"
        fields = {"amount_minor": paid["amount_minor"] if paid else None, "currency": paid["currency"] if paid else None,
                  "reason": _word(obj.get("fraud_type")), "due_by": None, "has_evidence": False, "closed_at": None}
    else:
        status = _word(obj.get("status")) or "needs_response"
        fields = {"amount_minor": _minor(obj.get("amount")), "currency": _word(obj.get("currency"), 8),
                  "reason": _word(obj.get("reason")), "due_by": _when(details.get("due_by")),
                  "has_evidence": details.get("has_evidence") is True,
                  "closed_at": event_at if status in ENDED else None}
    was = await store.dispute(dispute_id)
    outcome = await store.save_dispute(
        dispute_id, kind=WARNING if warning else DISPUTE, charge_id=charge_id,
        tenant_id=await _account_of(charge_id), status=status,
        opened_at=_when(obj.get("created")) or event_at, event_at=event_at, **fields)
    if outcome == "new":
        await tell(dispute_id, "new")
    elif outcome == "changed" and status in ENDED and was is not None and was["status"] not in ENDED:
        await tell(dispute_id, "ended")
    return {"received": True, "dispute": dispute_id, "outcome": outcome}


# ── the rate ─────────────────────────────────────────────────────────

async def activity(now: datetime | None = None) -> dict[str, Any]:
    """Disputes opened in the last 30 days against payments taken in them."""
    now = now or datetime.now(UTC)
    since = now - WINDOW
    recent = [d for d in await get_store().disputes(1000) if d["opened_at"] >= since]
    disputes = sum(1 for d in recent if d["kind"] == DISPUTE)
    payments = await get_store().payments_between(since, now + timedelta(seconds=1))
    rate = (disputes * 10_000) // payments if payments else None
    return {
        "days": WINDOW.days, "disputes": disputes, "payments": payments,
        "warnings": sum(1 for d in recent if d["kind"] == WARNING),
        "rate_hundredths": rate, "rate": percent(rate) if rate is not None else None,
        "lines": [{"at": percent(at), "whose": whose, "over": rate is not None and rate >= at} for at, whose in LINES],
    }


def activity_line(act: dict[str, Any]) -> str:
    counted = (f"Last {act['days']} days: {act['disputes']} disputed and {act['warnings']} flagged by a bank, "
               f"against {act['payments']} payment{'' if act['payments'] == 1 else 's'}")
    if act["rate"] is None:
        return counted + ". No payment is on record in those days, so there is no rate to give."
    over = [line["whose"] for line in act["lines"] if line["over"]]
    said = f"{counted}: {act['rate']} disputed."
    if over:
        said += (f" That is over {' and '.join(over)}. With this few payments one dispute crosses them; "
                 "what the card networks act on is a month's count as well as its rate.")
    else:
        said += " The lines are " + " and ".join(whose for _, whose in LINES) + "."
    return said


# ── the operator's mail ──────────────────────────────────────────────

async def _who(tenant_id: str | None) -> tuple[str, str]:
    """The account's name and sign-in address, as a mail may show them."""
    if not tenant_id:
        return "an account Okwan could not match to the payment", ""
    store = get_store()
    tenant = await store.get_tenant(tenant_id)
    if tenant is None:
        return "an account that no longer exists", ""
    return f"{shown(tenant.name, own=True)} ({tenant.id})", await store.account_email(tenant.id) or ""


async def message(row: dict[str, Any], why: str, now: datetime) -> tuple[str, str]:
    """Subject and text for the operator. The subject is Okwan's words and
    figures only: an account's name is never in it."""
    name, email = await _who(row["tenant_id"])
    reason = REASONS.get(row["reason"] or "", OTHER_REASON)
    left = days_left(row["due_by"], now)
    paid = await get_store().payment(row["charge_id"])
    payment = f"{amount(row)}" + (f", taken {day(paid['paid_at'])}" if paid else "") + f" ({row['charge_id']})"
    account = f"Account:  {name}" + (f", {email}" if email else "")
    act = activity_line(await activity(now))
    if row["kind"] == WARNING:
        subject = f"[Payment flagged] A bank has flagged a {amount(row)} payment as possible fraud"
        body = [
            "A card issuer has reported one of Okwan's payments as possibly fraudulent (an early fraud warning).",
            "No money has moved and nothing has to be answered.",
            "", account, f"Payment:  {payment}", f"Flagged:  {row['reason'] or 'no type given'}", "",
            "Stripe's documentation says about four in ten of these become a dispute if the payment is not",
            "refunded, and that refunding it first stops the cardholder opening one. Look at the account, and",
            "decide: refund the payment in Stripe, or wait.", "", console_link(row["dispute_id"]), "", act,
        ]
    elif why == "ended":
        subject = f"[Dispute {row['status']}] The {amount(row)} dispute is closed"
        body = [f"The bank has decided: {row['status']}.", "", account, f"Payment:  {payment}", "",
                console_link(row["dispute_id"]), "", act]
    else:
        due = "no date given by Stripe" if left is None else (
            f"{day(row['due_by'])} ({left} days)" if left >= 0 else f"{day(row['due_by'])}, which has passed")
        lead = {"new": "A cardholder's bank has opened a dispute on one of Okwan's payments.",
                "late": "A dispute Okwan was not able to mail about when it arrived is open.",
                "due": "A dispute is still unanswered and its date is near."}[why]
        subject = (f"[Dispute] {amount(row)} disputed, answer due {day(row['due_by'])}" if why != "due"
                   else f"[Dispute due] {amount(row)} dispute unanswered, due {day(row['due_by'])}")
        body = [
            lead, "", account, f"Payment:  {payment}",
            f"Reason:   {row['reason'] or 'none given'}. {reason['says']}",
            f"To show:  {reason['show']}",
            f"Answer by: {due}. After that date the dispute is lost, and an answer can be sent once.", "",
            "What Okwan holds is already put into Stripe's fields, to read, complete and submit in Stripe:",
            console_link(row["dispute_id"]), "", act,
        ]
    return subject, "\n".join(body) + "\n"


async def tell(dispute_id: str, why: str, now: datetime | None = None) -> bool:
    """Mail the operator about one dispute. True when the mail was
    accepted; the dispute is then marked as told."""
    now = now or datetime.now(UTC)
    store = get_store()
    row = await store.dispute(dispute_id)
    to = os.environ.get("OKWAN_SUPPORT_ESCALATION_TO", "").strip()
    mailer = get_mailer()
    if row is None or not to or mailer is None:
        if row is not None:
            logger.error("disputes: %s (%s) not mailed: %s", dispute_id, why,
                         "OKWAN_SUPPORT_ESCALATION_TO is not set" if not to else "no mailer")
        return False
    subject, text = await message(row, why, now)
    try:
        await mailer.send_notice(to, subject, text)
    except Exception as exc:  # noqa: BLE001 — the event is kept either way; the sweep mails it again
        logger.error("disputes: %s (%s) mail not sent: %s", dispute_id, why, type(exc).__name__)
        return False
    await store.dispute_reminded(dispute_id, now)
    return True


async def remind(now: datetime | None = None) -> int:
    """Mail what still needs it: a dispute or warning never mailed about,
    and an open dispute with no answer whose date is within three days,
    once a day. How many mails were accepted."""
    now = now or datetime.now(UTC)
    sent = 0
    for row in await get_store().disputes(1000):
        if row["status"] in ENDED:
            continue
        if row["reminded_at"] is None:
            sent += await tell(row["dispute_id"], "late", now)
        elif (row["status"] in NEEDS_ANSWER and not row["has_evidence"] and row["due_by"] is not None
              and row["due_by"] - now <= REMIND_WITHIN and now - row["reminded_at"] >= REMIND_EVERY):
            sent += await tell(row["dispute_id"], "due", now)
    return sent


async def sweeper(interval: float = SWEEP_SECONDS, first_after: float = 120.0) -> None:
    await asyncio.sleep(first_after)
    while True:
        try:
            await remind()
        except Exception:  # a bad hour must not end the reminders
            logger.exception("disputes: reminder round failed")
        await asyncio.sleep(interval)


# ── the answer, prepared ─────────────────────────────────────────────

async def evidence(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """What Okwan holds for one dispute, in the fields of Stripe's form.

    Each field is `text`, what the record shows, and `add`, what only a
    person can say. Nothing here is guessed: a field with nothing on
    record says so."""
    now = now or datetime.now(UTC)
    store = get_store()
    reason = REASONS.get(row["reason"] or "", OTHER_REASON)
    paid = await store.payment(row["charge_id"])
    tenant = await store.get_tenant(row["tenant_id"]) if row["tenant_id"] else None
    fields: dict[str, dict[str, str | None]] = {}

    def put(field: str, text: str | None, add: str | None = None) -> None:
        fields[field] = {"text": text, "add": add}

    email = await store.account_email(tenant.id) if tenant else None
    put("customer_name", tenant.name if tenant else None,
        None if tenant else "The account could not be matched to this payment: find it in Stripe by the customer.")
    put("customer_email_address", email)
    put("product_description", PRODUCT)
    put("service_date", f"{day(paid['paid_at'])}: the month this payment covers began the day it was taken."
        if paid else None, None if paid else "The payment is not on record here: take its date from Stripe.")

    # The checkout that started the subscription this payment belongs to:
    # the account's latest paid one at or before the payment.
    shown_at = None
    if tenant and paid:
        paid_sessions = [c for c in await store.checkouts_for(tenant.id)
                         if c["paid_at"] is not None and c["paid_at"] <= paid["paid_at"] + timedelta(hours=1)]
        shown_at = paid_sessions[0] if paid_sessions else None
    put("customer_purchase_ip", shown_at["ip"] if shown_at else None,
        None if shown_at else "No checkout is on record for this payment.")
    # The policy in the version that checkout showed: its words, and its
    # own statement of how it was shown. A version not on file is said to
    # be missing, never answered with another version's words.
    version = shown_at["policy_version"] if shown_at else policy.VERSION
    try:
        said = policy.said(version)
    except KeyError:
        said = None
    words = said["text"] if said else None
    disclosure = said["disclosure"] if said else ""
    if shown_at:
        disclosure += (f" This customer opened Okwan's checkout at {stamp(shown_at['opened_at'])} from the address "
                       f"{shown_at['ip']}; the payment page showed version {shown_at['policy_version']} of the "
                       f"policy, and Stripe reported it paid at {stamp(shown_at['paid_at'])}.")
        if shown_at["terms_accepted_at"]:
            disclosure += (" Before paying, the customer ticked a box agreeing to the Terms of Service and the "
                           f"refund policy; Stripe reported it ticked at {stamp(shown_at['terms_accepted_at'])}.")
    no_record = None if shown_at else "No checkout is on record for this payment: say only what Stripe's record shows."
    if said is None:
        no_record = f"Version {version} of the policy is not on file: say how it was shown from the policy as it was."
    put("refund_policy_disclosure", disclosure.strip() or None, no_record)
    put("cancellation_policy_disclosure", disclosure.strip() or None, no_record)
    put("refund_refusal_explanation",
        "Okwan's policy, shown to the customer before they paid, is that a payment for Pro is not refunded once "
        "its month has started, and that cancelling stops the next payment without returning the last one.",
        "Whether the customer asked Okwan for a refund, when, and what they were told. If a refund was given, say "
        "so and give its date instead.")
    bill = await store.billing_for(tenant.id) if tenant else None
    if bill:
        state = f"As of {stamp(now)} the subscription's state at Stripe is \"{bill['status']}\""
        if bill["cancel_at_period_end"]:
            state += ", set to end at the close of the paid month"
        if bill["current_period_end"]:
            state += f"; the paid month ends {day(bill['current_period_end'])}"
        state += ". Cancelling is done by the customer from Manage billing in the dashboard, at any time."
    else:
        state = None
    put("cancellation_rebuttal", state,
        "When the customer cancelled, if they did (Stripe shows it on the subscription), and why this payment was "
        "still due: it was taken before the cancellation, or the cancellation took effect at the end of a month "
        "already paid for.")

    # Use of the service after the payment, oldest first.
    log: list[dict] = []
    if tenant and paid:
        entries = await store.access_entries(tenant.id, paid["paid_at"], 1000)
        # The customer's own requests only: Okwan's scheduled daily check
        # reads the account's rails too, and is not the customer using it.
        log = sorted((e for e in entries if not e["surface"].startswith("schedule:") and e["ip"] != "unknown"),
                     key=lambda e: e["at"])
    if log:
        lines = [f"{stamp(e['at'])}  {e['ip']}  {e['surface']}" for e in log[:LOG_LINES]]
        head = (f"Requests to Okwan made with this account's own sign-in and keys after the payment "
                f"({len(log)} on record{', the first ' + str(LOG_LINES) + ' shown' if len(log) > LOG_LINES else ''}). "
                "Each line: time, the caller's address, and what was read.")
        put("access_activity_log", head + "\n" + "\n".join(lines))
    else:
        put("access_activity_log", None,
            "No use of the service is on record after this payment." if tenant and paid else
            "The account or the payment is not on record here, so its use cannot be listed.")

    earlier = None
    if tenant and paid:
        earlier = next((p for p in await store.payments_for(tenant.id)
                        if p["paid_at"] < paid["paid_at"] and p["charge_id"] != paid["charge_id"]), None)
    put("duplicate_charge_id", earlier["charge_id"] if earlier else None,
        None if earlier else "No earlier payment by this account is on record: there may be nothing it duplicates.")
    put("duplicate_charge_explanation",
        (f"The two payments are for different months of a monthly subscription: {earlier['charge_id']} was taken "
         f"{day(earlier['paid_at'])} and {paid['charge_id']} was taken {day(paid['paid_at'])}, each for the month "
         "starting the day it was taken.") if earlier and paid else None)
    put("uncategorized_text", words,
        None if words else f"Version {version} of the policy is not on file: attach the policy as it was then.")

    return {
        "reason": {"code": row["reason"], "says": reason["says"], "show": reason["show"]},
        "for_this_reason": list(reason["fields"]),
        "fields": fields,
        "attach": [{"field": field, "what": what} for field, what in ATTACH],
        "characters": sum(len(f["text"] or "") for f in fields.values()),
        "policy_version": version,
    }
