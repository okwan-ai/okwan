"""Disputes on payments for Okwan Pro: known the hour they arrive, measured
before a card network measures them, and answered from what is already
put together.

A cardholder can ask their bank to take back a payment. The bank decides
on what the seller shows it, by a date (7 to 21 days, by card network); a
missed date is a lost dispute, an answer can be sent once, and every
dispute counts against the account whether it is won or lost. So, from
Stripe's signed events on the billing webhook:

* **Known when it arrives.** A dispute, the inquiry that can come before
  one, and a bank's early warning of fraud are kept, and the operator is
  mailed the account, the amount, the bank's reason and the date an
  answer is due; again when an inquiry becomes a dispute, when a decided
  one is reopened, as an unanswered one's date nears, and when the bank
  decides. Each payment Stripe reports is kept too: it is how a disputed
  charge finds its account, and what the rate is measured against.
* **The rate.** Disputes in the last 30 days against payments in the same
  30 days, beside the lines Stripe's documentation gives: 0.75%, the
  level it calls excessive, and 0.5%, Visa's monitoring line, which counts
  fraud warnings with disputes. An inquiry that has not become a dispute
  is not counted, as Stripe says the card networks do not count it.
* **The answer, prepared.** `evidence()` puts what Okwan holds into the
  fields of Stripe's dispute form. A person reads it, adds what only a
  person knows, and submits it in Stripe. Okwan does not submit it and its
  Stripe key cannot.

**One Stripe account, more than one product.** The company's Stripe
account takes payments for other products as well, and Stripe sends every
destination all of the account's events. A payment is Okwan's when its
Stripe customer is linked to an Okwan account; one that is not is kept
unmatched for a week (its account may be linked a moment later) and then
forgotten. A dispute on a charge that is not a known Okwan payment is
kept and mailed, said plainly to be unmatched, counted apart, and given
no evidence: Okwan has none for it. The rate is Okwan's own; the card
networks measure the whole account, which Stripe's dashboard shows.

Nothing here is a connector and nothing reads or writes a customer's
rails. Amounts are minor units, as Stripe sends them.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from okwan_core.currency import format_minor
from okwan_vault.usage import DISPUTE_ENDED as ENDED
from okwan_vault.usage import DISPUTE_NEEDS as NEEDS_ANSWER

from . import policy
from .auth import get_store
from .mail import get_mailer
from .schedule import shown

logger = logging.getLogger("okwan.disputes")

#: What the rate is measured over, as the card networks measure a month.
WINDOW = timedelta(days=30)
#: Hundredths of a percent (Stripe's documentation, read 2026-11-13).
EXCESSIVE = 75      # dispute activity above 0.75% is what it calls excessive
VISA = 50           # Visa's monitoring line, for disputes and fraud warnings together
#: An unanswered dispute is mailed again from this long before its date, once in this long.
REMIND_WITHIN = timedelta(days=3)
REMIND_EVERY = timedelta(hours=24)
SWEEP_SECONDS = 3600.0
#: The most lines of the access log one answer carries, and Stripe's limit
#: on the characters of every field together.
LOG_LINES = 60
EVIDENCE_CHARACTERS = 150_000
#: Two payments closer than this are not two months of a monthly plan.
A_MONTH_APART = timedelta(days=20)

DISPUTE, WARNING = "dispute", "warning"
#: The state kept for a bank's early fraud warning, which has none of a dispute's.
FLAGGED = "flagged"

PRODUCT = (
    "Okwan Pro: a monthly subscription to Okwan's hosted service, which reads the customer's own payment and "
    "commerce systems and serves that data to the customer over an API, SQL and a dashboard, with 100,000 "
    f"requests a month. {policy.PRO_PRICE_USD} US dollars a month, billed monthly in advance by card. A digital "
    "service: nothing is shipped."
)
UNMATCHED = ("This charge is not a payment Okwan has on record for an Okwan account. The Stripe account takes "
             "payments for other products too, so it may be one of theirs; or it is an Okwan payment from before "
             "this record began. Okwan holds no evidence for it: look the payment up in Stripe.")

#: The bank's reason, as Stripe names it: what the cardholder is saying,
#: what overturns it, and which of the form's fields carry that (Stripe's
#: documentation, "Dispute reason codes", for a digital service).
_OWNER = ("customer_name", "customer_email_address", "customer_purchase_ip", "access_activity_log",
          "product_description", "service_date")
REASONS: dict[str, dict[str, Any]] = {
    "fraudulent": {
        "says": "The cardholder says they did not authorise the payment.",
        "show": "That the account's own owner made the payment: who they are, the address they paid from, and "
                "their use of the service after paying.",
        "fields": _OWNER,
    },
    "unrecognized": {
        "says": "The cardholder does not recognise the payment on their statement.",
        "show": "That the account's own owner made the payment: who they are, the address they paid from, and "
                "their use of the service after paying.",
        "fields": _OWNER,
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
        "show": "That each payment was for a different month. If it was not, the cardholder is right.",
        "fields": ("duplicate_charge_id", "duplicate_charge_explanation", "service_date"),
    },
}
OTHER_REASON = {
    "says": "The bank gave a reason with no category of its own.",
    "show": "Ask the customer what they are disputing; send what bears on it.",
    "fields": (*_OWNER, "refund_policy_disclosure", "uncategorized_text"),
}
#: What the form asks for as a file, which a person attaches in Stripe.
ATTACH: tuple[tuple[str, str], ...] = (
    ("refund_policy", "The refund policy as shown: print this page to a PDF, or save okwan.ai/refunds as one."),
    ("cancellation_policy", "The same file: the policy covers cancelling."),
    ("customer_communication", "Any email with the customer about this payment, a refund or cancelling."),
    ("receipt", "Stripe's receipt or invoice for the payment, from the payment's page in Stripe."),
)
FIELDS = ("customer_name", "customer_email_address", "product_description", "service_date", "customer_purchase_ip",
          "refund_policy_disclosure", "cancellation_policy_disclosure", "refund_refusal_explanation",
          "cancellation_rebuttal", "access_activity_log", "duplicate_charge_id", "duplicate_charge_explanation",
          "uncategorized_text")


# ── small things ─────────────────────────────────────────────────────

_ID = re.compile(r"[A-Za-z0-9_]{1,255}")
_PLAIN = re.compile(r"[a-z0-9_]{1,64}")


def _when(value: Any) -> datetime | None:
    """A Stripe timestamp (seconds) as a time; None for anything else."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < 4_102_444_800:
        return None
    return datetime.fromtimestamp(int(value), UTC)


def _id(value: Any) -> str:
    """A Stripe id, or the id of an expanded object; "" for anything else."""
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) and _ID.fullmatch(value) else ""


def _minor(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and abs(value) < 10**15 else None


def _word(value: Any) -> str | None:
    """One of Stripe's own lower-case names (a status, a reason, a currency); None for anything else."""
    return value if isinstance(value, str) and _PLAIN.fullmatch(value) else None


def _address(value: Any) -> str | None:
    """An address only if it is one: what is recorded is what a header said."""
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return None


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


def stage(row: dict[str, Any]) -> str:
    """Where a dispute stands, as the operator is told of it: an inquiry,
    a dispute (a fraud warning is told the same way), or decided."""
    if row["status"] in ENDED:
        return "ended"
    return "inquiry" if row["status"].startswith("warning_") else "open"


def waits(row: dict[str, Any]) -> bool:
    """The bank is waiting for an answer and none has been sent. Evidence
    saved in Stripe and not submitted is not an answer."""
    return row["status"] in NEEDS_ANSWER and not row["submitted"]


def console_link(dispute_id: str) -> str:
    app = (os.environ.get("OKWAN_DASHBOARD_URL", "").strip() or "http://localhost:3000").rstrip("/")
    return f"{app}/ops/disputes/{dispute_id}"


# ── from Stripe's events ─────────────────────────────────────────────

def handles(kind: str) -> bool:
    return kind == "charge.succeeded" or kind.startswith(("charge.dispute.", "radar.early_fraud_warning."))


async def _match(charge_id: str) -> tuple[str | None, bool]:
    """The Okwan account a charge was for, and whether it is an Okwan
    payment at all. A payment that arrived before its customer was linked
    to an account is matched now if it can be."""
    store = get_store()
    paid = await store.payment(charge_id)
    if (paid is not None and not paid["okwan"] and paid["customer_id"]
            and await store.tenant_for_billing_customer(paid["customer_id"])):
        await store.settle_payments()
        paid = await store.payment(charge_id)
    return (paid["tenant_id"], True) if paid is not None and paid["okwan"] else (None, False)


async def handle(kind: str, obj: Any, event: dict[str, Any]) -> dict[str, Any]:
    """One signed event. Never raises on what Stripe sent: an event this
    cannot read is acknowledged and logged, not retried for three days."""
    store = get_store()
    if not isinstance(obj, dict):
        return {"received": True}
    now = datetime.now(UTC)
    event_at = _when(event.get("created")) or now
    if kind == "charge.succeeded":
        charge_id, paid_minor = _id(obj.get("id")), _minor(obj.get("amount"))
        if not charge_id or paid_minor is None or obj.get("paid") is False:
            return {"received": True}
        customer = _id(obj.get("customer"))
        await store.note_payment(
            charge_id, tenant_id=await store.tenant_for_billing_customer(customer) if customer else None,
            customer_id=customer, amount_minor=paid_minor, currency=_word(obj.get("currency")) or "",
            paid_at=_when(obj.get("created")) or event_at)
        return {"received": True, "payment": charge_id}

    warning = kind.startswith("radar.early_fraud_warning.")
    dispute_id, charge_id = _id(obj.get("id")), _id(obj.get("charge"))
    if not dispute_id or not charge_id:
        logger.warning("disputes: %s without a readable id or charge, not kept", kind)
        return {"received": True}
    tenant_id, ours = await _match(charge_id)
    details = obj.get("evidence_details") if isinstance(obj.get("evidence_details"), dict) else {}
    if warning:
        paid = await store.payment(charge_id)
        status = FLAGGED
        fields = {"amount_minor": paid["amount_minor"] if paid else None, "currency": paid["currency"] if paid else None,
                  "reason": _word(obj.get("fraud_type")), "due_by": None, "has_evidence": False, "submitted": False}
    else:
        status = _word(obj.get("status")) or "needs_response"
        sent = _minor(details.get("submission_count")) or 0
        fields = {"amount_minor": _minor(obj.get("amount")), "currency": _word(obj.get("currency")),
                  "reason": _word(obj.get("reason")), "due_by": _when(details.get("due_by")),
                  "has_evidence": details.get("has_evidence") is True,
                  # An answer was sent: Stripe counts one, or the bank is reviewing it.
                  "submitted": sent > 0 or status.endswith("under_review")}
    applied = await store.save_dispute(
        dispute_id, kind=WARNING if warning else DISPUTE, charge_id=charge_id, tenant_id=tenant_id, okwan=ours,
        status=status, opened_at=_when(obj.get("created")) or event_at,
        closed_at=event_at if status in ENDED else None, event_at=event_at, **fields)
    if applied:
        row = await store.dispute(dispute_id)
        if row is not None:
            await settle(row, now)
    return {"received": True, "dispute": dispute_id, "applied": applied}


# ── the rate ─────────────────────────────────────────────────────────

async def activity(now: datetime | None = None) -> dict[str, Any]:
    """Okwan's own: what was opened in the last 30 days against payments
    taken in them. Counted in the database, whatever the number of rows."""
    now = now or datetime.now(UTC)
    c = await get_store().dispute_counts(now - WINDOW, now + timedelta(seconds=1))
    paid = c["payments"]
    rate = (c["disputes"] * 10_000) // paid if paid else None
    with_warnings = ((c["disputes"] + c["warnings"]) * 10_000) // paid if paid else None
    return {
        "days": WINDOW.days, **c,
        "rate_hundredths": rate, "rate": percent(rate) if rate is not None else None,
        "with_warnings": percent(with_warnings) if with_warnings is not None else None,
        "lines": [
            {"at": percent(EXCESSIVE), "whose": "the level Stripe's documentation calls excessive", "of": "disputes",
             "over": rate is not None and rate >= EXCESSIVE},
            {"at": percent(VISA), "whose": "Visa's monitoring line", "of": "disputes and fraud warnings together",
             "over": with_warnings is not None and with_warnings >= VISA},
        ],
    }


def _many(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def activity_line(act: dict[str, Any]) -> str:
    said = (f"Okwan's payments, last {act['days']} days: {act['disputes']} disputed, "
            f"{_many(act['inquiries'], 'inquiry', 'inquiries')} not yet a dispute and "
            f"{act['warnings']} flagged for fraud, against {_many(act['payments'], 'payment', 'payments')}")
    if act["rate"] is None:
        said += ". No Okwan payment is on record in those days, so there is no rate to give."
    else:
        said += (f": {act['rate']} disputed ({act['lines'][0]['at']} is {act['lines'][0]['whose']}), and "
                 f"{act['with_warnings']} with fraud warnings ({act['lines'][1]['at']} is {act['lines'][1]['whose']}).")
        if any(line["over"] for line in act["lines"]):
            said += (" With this few payments one dispute crosses a line; what the card networks act on is a "
                     "month's count as well as its rate.")
    if act["unmatched"]:
        said += (f" {_many(act['unmatched'], 'more is', 'more are')} on this Stripe account and not matched to an "
                 "Okwan payment: not counted here.")
    return said + (" The card networks measure the whole Stripe account, other products included; Stripe's "
                   "dashboard has that figure.")


# ── the operator's mail ──────────────────────────────────────────────

async def _who(row: dict[str, Any]) -> str:
    """The account, as a mail may name it: its cleaned name and its id.
    The sign-in address is on the console page, not in the mail."""
    if not row["okwan"]:
        return "not matched to an Okwan payment (it may be another product's on this Stripe account)"
    tenant = await get_store().get_tenant(row["tenant_id"]) if row["tenant_id"] else None
    if tenant is None:
        return "an Okwan account that no longer exists"
    return f"{shown(tenant.name, own=True)} ({tenant.id})"


async def message(row: dict[str, Any], why: str, now: datetime) -> tuple[str, str]:
    """Subject and text for the operator. The subject is Okwan's words and
    figures only: an account's name is never in it."""
    reason = REASONS.get(row["reason"] or "", OTHER_REASON)
    left = days_left(row["due_by"], now)
    paid = await get_store().payment(row["charge_id"])
    payment = amount(row) + (f", taken {day(paid['paid_at'])}" if paid else "") + f" ({row['charge_id']})"
    head = [f"Account:  {await _who(row)}", f"Payment:  {payment}"]
    foot = ["", console_link(row["dispute_id"]), "", activity_line(await activity(now))]
    due = "no date given by Stripe" if left is None else (
        f"{day(row['due_by'])} ({left} days)" if left >= 0 else f"{day(row['due_by'])}, which has passed")
    asked = [f"Reason:   {row['reason'] or 'none given'}. {reason['says']}", f"To show:  {reason['show']}",
             f"Answer by: {due}. After that date it is lost, and an answer can be sent once."]
    held = ["", "What Okwan holds is already put into Stripe's fields, to read, complete and submit in Stripe:"
            if row["okwan"] else "Okwan holds no evidence for a payment it cannot match:"]
    if row["kind"] == WARNING:
        subject = f"[Payment flagged] A bank has flagged a {amount(row)} payment as possible fraud"
        body = ["A card issuer has reported a payment as possibly fraudulent (an early fraud warning).",
                "No money has moved and nothing has to be answered.", "", *head,
                f"Flagged:  {row['reason'] or 'no type given'}", "",
                "Stripe's documentation says about four in ten of these become a dispute if the payment is not",
                "refunded, and that refunding it first stops the cardholder opening one. Look at the account, and",
                "decide: refund the payment in Stripe, or wait.", *foot]
    elif why == "ended":
        subject = f"[Dispute {row['status']}] The {amount(row)} dispute is closed"
        first = [] if row["told"] else ["Okwan first heard of this dispute when it was already decided.", ""]
        body = [*first, f"The bank has decided: {row['status']}.", "", *head, *foot]
    elif why == "inquiry":
        subject = f"[Inquiry] A bank is asking about a {amount(row)} payment, answer due {day(row['due_by'])}"
        body = ["A cardholder's bank is asking about a payment before opening a dispute (an inquiry).",
                "Stripe's documentation says an answer, or a full refund, can settle it with no dispute and no fee,",
                "and that leaving it unanswered can turn it into a dispute that is unlikely to be won.", "",
                *head, *asked, *held, *foot]
    else:
        lead = {"open": "A cardholder's bank has opened a dispute on a payment.",
                "escalated": "The bank's inquiry has become a dispute.",
                "reopened": "A dispute that was decided has been opened again.",
                "due": "A dispute is still unanswered and its date is near."}[why]
        subject = (f"[Dispute due] {amount(row)} dispute unanswered, due {day(row['due_by'])}" if why == "due"
                   else f"[Dispute] {amount(row)} disputed, answer due {day(row['due_by'])}")
        body = [lead, "", *head, *asked, *held, *foot]
    return subject, "\n".join(body) + "\n"


async def settle(row: dict[str, Any], now: datetime) -> bool:
    """Mail the operator what they have not yet heard about one dispute,
    if anything: that it exists, that an inquiry became a dispute, that it
    was decided or reopened, or that its date is near. Which mail follows
    from the row's state and what was last told, never from which event
    arrived; the right to send is claimed first, so of two events or two
    rounds at once one mails; and a mail that is not accepted gives the
    claim back, for the next round. True when a mail was accepted."""
    store = get_store()
    at, told = stage(row), row["told"]
    if at != told:
        why = {"ended": "ended", "inquiry": "inquiry"}.get(
            at, "reopened" if told == "ended" else "escalated" if told == "inquiry" else "open")
    elif (waits(row) and row["due_by"] is not None and timedelta(0) <= row["due_by"] - now <= REMIND_WITHIN
          and (row["reminded_at"] is None or now - row["reminded_at"] >= REMIND_EVERY)):
        why = "due"
    else:
        return False
    if not await store.claim_dispute_notice(row["dispute_id"], told_was=told, told=at,
                                            reminded_was=row["reminded_at"], reminded_at=now):
        return False
    to, mailer = os.environ.get("OKWAN_SUPPORT_ESCALATION_TO", "").strip(), get_mailer()
    sent = False
    if not to or mailer is None:
        logger.error("disputes: a notice (%s) was not mailed: %s", why,
                     "OKWAN_SUPPORT_ESCALATION_TO is not set" if not to else "no mailer")
    else:
        try:
            subject, text = await message(row, why, now)
            await mailer.send_notice(to, subject, text)
            sent = True
        except Exception as exc:  # noqa: BLE001 — the event is kept either way; the next round mails it
            logger.error("disputes: a notice (%s) was not sent: %s", why, type(exc).__name__)
    if not sent:
        await store.claim_dispute_notice(row["dispute_id"], told_was=at, told=told, reminded_was=now,
                                         reminded_at=row["reminded_at"])
    return sent


async def remind(now: datetime | None = None) -> int:
    """One round: match payments that can now be matched and forget those
    that never were, then mail whatever the operator has not heard. How
    many mails were accepted."""
    now = now or datetime.now(UTC)
    store = get_store()
    await store.settle_payments(now)
    sent = 0
    for row in await store.disputes_to_settle(now, REMIND_WITHIN, REMIND_EVERY):
        sent += await settle(row, now)
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
    person can say or should check. Nothing here is guessed: a field with
    nothing on record says so, a payment with no checkout on record is not
    given today's policy, and a charge that is not a known Okwan payment
    gets no evidence at all."""
    now = now or datetime.now(UTC)
    store = get_store()
    reason = REASONS.get(row["reason"] or "", OTHER_REASON)
    fields: dict[str, dict[str, str | None]] = {}

    def put(field: str, text: str | None, add: str | None = None) -> None:
        fields[field] = {"text": text, "add": add}

    def size() -> int:
        return sum(len(f["text"] or "") for f in fields.values())

    def pack(version: str | None) -> dict[str, Any]:
        return {"matched": bool(row["okwan"]),
                "reason": {"code": row["reason"], "says": reason["says"], "show": reason["show"]},
                "for_this_reason": list(reason["fields"]), "fields": fields,
                "attach": [{"field": field, "what": what} for field, what in ATTACH],
                "characters": size(), "policy_version": version}

    if not row["okwan"]:
        for name in FIELDS:
            put(name, None, UNMATCHED if name == "customer_name" else None)
        return pack(None)

    await store.settle_payments(now)       # an earlier payment still waiting for its account is matched first
    paid = await store.payment(row["charge_id"])
    tenant = await store.get_tenant(row["tenant_id"]) if row["tenant_id"] else None
    gone = None if tenant else "The account has been deleted: its name, address and use are no longer on record."
    put("customer_name", tenant.name if tenant else None, gone)
    put("customer_email_address", await store.account_email(tenant.id) if tenant else None)
    put("product_description", PRODUCT)
    put("service_date", f"{day(paid['paid_at'])}: the day this payment was taken." if paid else None,
        "Pro is billed monthly in advance. Take the month this payment covers from its invoice in Stripe.")

    # The checkout that started the subscription this payment belongs to:
    # the account's latest paid one at or before the payment, matched by time.
    shown_at = None
    if tenant and paid:
        shown_at = next((c for c in await store.checkouts_for(tenant.id)
                         if c["paid_at"] is not None and c["paid_at"] <= paid["paid_at"] + timedelta(hours=1)), None)
    where = _address(shown_at["ip"]) if shown_at else None
    by_time = ("Matched by time: the account's most recent paid checkout before this payment. Check in Stripe that "
               "the payment belongs to the subscription that checkout started.")
    put("customer_purchase_ip", where,
        "No checkout is on record for this payment." if not shown_at else
        (f"{by_time} The address is what the network in front of Okwan reported for the request that opened the "
         "checkout." if where else "The checkout's record holds no address that reads as one."))
    version, said = None, None
    if shown_at:
        version = shown_at["policy_version"]
        try:
            said = policy.said(version)
        except KeyError:
            said = None
    if shown_at and said:
        disclosure = said["disclosure"] + (
            f" This customer opened Okwan's checkout at {stamp(shown_at['opened_at'])}"
            + (f" from the address {where}" if where else "")
            + f"; the payment page showed version {version} of the policy, and Stripe reported it paid at "
            f"{stamp(shown_at['paid_at'])}.")
        if shown_at["terms_accepted_at"]:
            disclosure += (" Before paying, the customer ticked a box agreeing to the Terms of Service and the "
                           f"refund policy; Stripe reported it ticked at {stamp(shown_at['terms_accepted_at'])}.")
        missing = by_time
    else:
        disclosure = None
        missing = ("No checkout is on record for this payment, so what it showed is not known here: say only what "
                   "Stripe's record of the session shows." if not shown_at else
                   f"Version {version} of the policy is not on file: say how it was shown from the policy as it was.")
    put("refund_policy_disclosure", disclosure, missing)
    put("cancellation_policy_disclosure", disclosure, missing)
    put("uncategorized_text", said["text"] if disclosure and said else None, None if disclosure else missing)
    put("refund_refusal_explanation",
        ("Okwan's policy, shown to the customer before they paid, is that a payment for Pro is not refunded once "
         "its month has started, and that cancelling stops the next payment without returning the last one.")
        if disclosure else None,
        "Whether the customer asked Okwan for a refund, when, and what they were told. If a refund was given, say "
        "so and give its date instead.")
    bill = await store.billing_for(tenant.id) if tenant else None
    state = None
    if bill:
        state = f"As of {stamp(now)} the subscription's state at Stripe is \"{bill['status']}\""
        if bill["cancel_at_period_end"]:
            state += ", set to end at the close of the paid month"
        if bill["current_period_end"]:
            state += f"; the paid month ends {day(bill['current_period_end'])}"
        state += ". Cancelling is done by the customer from Manage billing in the dashboard, at any time."
    put("cancellation_rebuttal", state,
        "When the customer cancelled, if they did (Stripe shows it on the subscription), and why this payment was "
        "still due: it was taken before the cancellation, or the cancellation took effect at the end of a month "
        "already paid for.")

    # The account's own requests after the payment, oldest first: counted
    # and read in the database, without Okwan's scheduled reads.
    total, rows = await store.customer_access(tenant.id, paid["paid_at"], LOG_LINES) if tenant and paid else (0, [])
    lines = [f"{stamp(r['at'])}  {_address(r['ip'])}  {' '.join(str(r['surface']).split())[:80]}"
             for r in rows if _address(r["ip"])]
    head = (f"Requests to Okwan made with this account's own sign-in and keys after the payment: {total} on "
            "record, the first {n} here. Each line: time, the caller's address, and what was read.")
    if lines:
        put("access_activity_log", head.format(n=len(lines)) + "\n" + "\n".join(lines),
            "The addresses are what the network in front of Okwan reported for each request.")
    else:
        put("access_activity_log", None,
            "No use of the service is on record after this payment." if tenant and paid else
            "The account or the payment is not on record here, so its use cannot be listed.")

    earlier = None
    if tenant and paid:
        earlier = next((p for p in await store.payments_for(tenant.id)
                        if p["paid_at"] <= paid["paid_at"] and p["charge_id"] != paid["charge_id"]), None)
    if earlier:
        apart = paid["paid_at"] - earlier["paid_at"]
        put("duplicate_charge_id", earlier["charge_id"])
        if apart < A_MONTH_APART:
            put("duplicate_charge_explanation",
                f"Two payments by this account {apart.days} days apart: {earlier['charge_id']} taken "
                f"{day(earlier['paid_at'])} and {paid['charge_id']} taken {day(paid['paid_at'])}.",
                "Less than a month apart, so these are not two months of a monthly plan. Check in Stripe whether "
                "the account was charged twice for one month. If it was, the cardholder is right: accept the "
                "dispute, as the refund policy promises a month charged twice is given back.")
        else:
            put("duplicate_charge_explanation",
                f"The payment before this one, {earlier['charge_id']}, was taken {day(earlier['paid_at'])}; this "
                f"one, {paid['charge_id']}, was taken {day(paid['paid_at'])}, {apart.days} days later. Pro is "
                "billed once a month.", "Confirm from the two invoices in Stripe that each is for its own month.")
    else:
        put("duplicate_charge_id", None, "No earlier payment by this account is on record.")
        put("duplicate_charge_explanation", None)

    # Stripe takes 150,000 characters across every field: the log gives way first.
    while size() > EVIDENCE_CHARACTERS and lines:
        lines.pop()
        put("access_activity_log", (head.format(n=len(lines)) + "\n" + "\n".join(lines)) if lines else None,
            "Shortened to fit Stripe's limit on an answer's length.")
    if size() > EVIDENCE_CHARACTERS:
        put("uncategorized_text", None, "Left out to fit Stripe's limit: attach the policy as a file instead.")
    return pack(version if disclosure else None)
