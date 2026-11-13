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
  mailed the account's id, the amount, the bank's reason and the date an
  answer is due; again when an inquiry becomes a dispute, when it goes to
  appeal, when a decided one is reopened or decided differently, when one
  first mailed as unmatched turns out to be Okwan's, as an unanswered
  one's date nears, and when the bank decides. Whether the bank is waiting
  is Stripe's state for the dispute and nothing Okwan remembers. Each payment Stripe reports is kept too: it is how a disputed
  charge finds its account, and what the rate is measured against.
* **The rate.** What became a dispute in the last 30 days against payments
  in the same 30 days, beside the lines Stripe's documentation gives: 0.75%, the
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
Stripe customer is one that has paid for Pro for an Okwan account: every
such customer is remembered, a second one the account came to have and
one whose account has since been deleted included, because Stripe can
still charge them. A payment by any other customer is kept unmatched for
a week (its account may be linked a moment later) and then forgotten. A dispute on a charge that is not a known Okwan payment is
kept and mailed, said plainly to be unmatched, counted apart, and given
no evidence: Okwan has none for it. The rate is Okwan's own; the card
networks measure the whole account, which Stripe's dashboard shows.

Nothing here is a connector and nothing reads or writes a customer's
rails. Amounts are minor units, as Stripe sends them.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from okwan_core.currency import format_minor
from okwan_vault.access import RETENTION as LOG_KEPT
from okwan_vault.usage import DISPUTE_ENDED as ENDED
from okwan_vault.usage import DISPUTE_NEEDS as NEEDS_ANSWER
from okwan_vault.usage import dispute_heard, dispute_stage

from . import policy
from .auth import get_store
from .mail import get_mailer
from .ratelimit import as_address

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
#: In the log of use, where a request's recorded address does not read as one.
NO_ADDRESS = "(no address on record)"
#: A payment is Okwan's by its Stripe customer. Nothing Stripe sends with a
#: charge says which product it was for, so one that is not the price of
#: Pro is said to be so, for a person to check.
NOT_PRO = ("{paid} is not the price of Pro. This Stripe customer has paid for Okwan Pro, and the Stripe account "
           "takes other products' payments too: check in Stripe that this charge is for Okwan Pro before answering "
           "for it as one.")

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
    """An address only if it is one, by the rule that recorded it: what is
    on record is what a header said."""
    return as_address(value) or None


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
    a dispute (a fraud warning is told the same way), an appeal, or
    decided."""
    return dispute_stage(row["status"])


def heard(row: dict[str, Any]) -> str:
    """What the operator has heard once mailed about a dispute as it now
    stands: its stage, or how it ended, and whether it is matched."""
    return dispute_heard(row["status"], bool(row["okwan"]))


def waits(row: dict[str, Any]) -> bool:
    """The bank is waiting for an answer: Stripe's own state says so, and
    nothing Okwan remembers overrides it. Evidence saved in Stripe and not
    submitted leaves the state as it was, so it is not an answer; and an
    answer sent to an inquiry is not an answer to the dispute it became,
    nor one sent before a decided dispute was opened again (Stripe's
    documentation: "you must submit another response")."""
    return row["status"] in NEEDS_ANSWER


def _is_pro(paid: dict[str, Any]) -> bool:
    """The payment is what Pro costs, in the currency it is sold in."""
    return (paid["amount_minor"], paid["currency"]) == (policy.PRO_PRICE_USD * 100, "usd")


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
            and (await store.okwan_customer(paid["customer_id"]))[0]):
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
        await store.note_payment(
            charge_id, customer_id=_id(obj.get("customer")), amount_minor=paid_minor,
            currency=_word(obj.get("currency")) or "", paid_at=_when(obj.get("created")) or event_at)
        return {"received": True, "payment": charge_id}

    warning = kind.startswith("radar.early_fraud_warning.")
    dispute_id, charge_id = _id(obj.get("id")), _id(obj.get("charge"))
    if not dispute_id or not charge_id:
        # Okwan's own words for what it was: an event's type is text from
        # the request, and nothing from the request is written to the log.
        logger.warning("disputes: %s without a readable id or charge, not kept",
                       "an early fraud warning" if warning else "a dispute event")
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
    """The account, as a mail may name it: by Okwan's own id for it and
    nothing the customer wrote. An account made by signing up is named by
    its sign-in address, so its name is the customer's words and their
    address at once; both are on the console page, behind the operator's
    sign-in, and neither is in the mail."""
    if not row["okwan"]:
        return "not matched to an Okwan payment (it may be another product's on this Stripe account)"
    tenant = await get_store().get_tenant(row["tenant_id"]) if row["tenant_id"] else None
    if tenant is None:
        return "an Okwan account that no longer exists"
    return f"{tenant.id} (its name and sign-in address are on the page below)"


async def message(row: dict[str, Any], why: str, now: datetime) -> tuple[str, str]:
    """Subject and text for the operator: Okwan's words, Stripe's ids and
    names for things, and figures. Nothing in either is text a customer
    chose."""
    reason = REASONS.get(row["reason"] or "", OTHER_REASON)
    left = days_left(row["due_by"], now)
    paid = await get_store().payment(row["charge_id"])
    payment = amount(row) + (f", taken {day(paid['paid_at'])}" if paid else "") + f" ({row['charge_id']})"
    head = [f"Account:  {await _who(row)}", f"Payment:  {payment}"]
    if row["okwan"] and paid and not _is_pro(paid):
        head.append(f"Note:     {NOT_PRO.format(paid=amount(paid))}")
    foot = ["", console_link(row["dispute_id"]), "", activity_line(await activity(now))]
    due = "no date given by Stripe" if left is None else (
        f"{day(row['due_by'])} ({left} days)" if left >= 0 else f"{day(row['due_by'])}, which has passed")
    asked = [f"Reason:   {row['reason'] or 'none given'}. {reason['says']}", f"To show:  {reason['show']}",
             f"Answer by: {due}. After that date it is lost, and an answer can be sent once."]
    # First heard of, or heard of again, when an answer is already with the bank.
    with_bank = row["status"].endswith("under_review")
    if with_bank:
        asked[-1] = "Answered: Stripe reports that an answer has been sent and the bank is reviewing it."
    held = ["", "What Okwan holds is already put into Stripe's fields, to read, complete and submit in Stripe:"
            if row["okwan"] else "Okwan holds no evidence for a payment it cannot match:"]
    told = row["told"] or ""
    # First mailed as not matched, and since matched to an Okwan payment.
    matched = ("Okwan mailed about this before as not matched to an Okwan payment. The payment's record has since "
               "arrived: it is a payment for Okwan Pro.")
    found = [matched, ""] if told.endswith(":unmatched") and row["okwan"] else []
    at = stage(row)
    if row["kind"] == WARNING:
        subject = f"[Payment flagged] A bank has flagged a {amount(row)} payment as possible fraud"
        body = [*found, "A card issuer has reported a payment as possibly fraudulent (an early fraud warning).",
                "No money has moved and nothing has to be answered.", "", *head,
                f"Flagged:  {row['reason'] or 'no type given'}", "",
                "Stripe's documentation says about four in ten of these become a dispute if the payment is not",
                "refunded, and that refunding it first stops the cardholder opening one. Look at the account, and",
                "decide: refund the payment in Stripe, or wait.", *foot]
    elif at == "ended":
        subject = f"[Dispute {row['status']}] The {amount(row)} dispute is closed"
        first = [] if told else ["Okwan first heard of this dispute when it was already decided.", ""]
        if told in ENDED and told != row["status"]:
            changed = (f"The decision on this dispute has changed. Okwan mailed it as {told}; Stripe now reports "
                       f"it {row['status']}.")
            first = [changed, ""]
        body = [*first, f"The bank has decided: {row['status']}.", "", *head, *foot]
    elif at == "inquiry":
        subject = (f"[Inquiry] A bank is asking about a {amount(row)} payment, answered and with the bank"
                   if with_bank else
                   f"[Inquiry] A bank is asking about a {amount(row)} payment, answer due {day(row['due_by'])}")
        lead = (["An inquiry is still unanswered and its date is near."] if why == "due" else
                ["A cardholder's bank is asking about a payment before opening a dispute (an inquiry)."])
        body = [*found, *lead,
                "Stripe's documentation says an answer, or a full refund, can settle it with no dispute and no fee,",
                "and that leaving it unanswered can turn it into a dispute that is unlikely to be won.", "",
                *head, *asked, *held, *foot]
    else:
        lead = {"open": "A cardholder's bank has opened a dispute on a payment.",
                "escalated": "The bank's inquiry has become a dispute. An answer sent to the inquiry does not answer "
                             "the dispute: Stripe's documentation says another has to be sent.",
                "reopened": "A dispute that was decided has been opened again.",
                "appeal": "A dispute has gone to appeal: Stripe reports it as needing an answer to the appeal."
                          if waits(row) else "A dispute has gone to appeal, and the appeal is with the bank.",
                "matched": "A cardholder's bank has opened a dispute on a payment.",
                "due": "A dispute is still unanswered and its date is near."}[why]
        subject = (f"[Dispute due] {amount(row)} dispute unanswered, due {day(row['due_by'])}" if why == "due"
                   else f"[Dispute] {amount(row)} disputed, answered and with the bank" if with_bank
                   else f"[Dispute] {amount(row)} disputed, answer due {day(row['due_by'])}")
        body = [*found, lead, "", *head, *asked, *held, *foot]
    return subject, "\n".join(body) + "\n"


def _why(row: dict[str, Any], now: datetime) -> str | None:
    """Which mail one dispute is owed, if any. It follows from the row's
    state and what was last told, never from which event arrived."""
    at, told = stage(row), row["told"]
    if heard(row) != told:
        was = (told or "").removesuffix(":unmatched")
        if at in ("ended", "inquiry", "appeal"):
            return at
        if was == at:
            return "matched"            # the same stage; what changed is that its payment is now known
        return "reopened" if was in ENDED or was == "appeal" else "escalated" if was == "inquiry" else "open"
    if (waits(row) and row["due_by"] is not None and timedelta(0) <= row["due_by"] - now <= REMIND_WITHIN
            and (row["reminded_at"] is None or now - row["reminded_at"] >= REMIND_EVERY)):
        return "due"
    return None


async def _tell(row: dict[str, Any], now: datetime) -> bool:
    """One mail about one dispute, if one is owed. The right to send is
    taken first, so of two events or two rounds at once one mails; what
    the operator has heard is recorded only when the mail was accepted;
    and the right is a time that runs out. So a mail the provider refuses
    is sent on the next round, and so is one whose send never reported
    back at all: the process stopped inside it, or the database went away
    before the outcome could be written. True when a mail was accepted."""
    store = get_store()
    if _why(row, now) is None or not await store.claim_dispute_notice(
            row["dispute_id"], told_was=row["told"], reminded_was=row["reminded_at"], at=now):
        return False
    # The dispute as it stands now that the right is held: an event may
    # have landed since the caller read it, and the mail says what is so.
    row = await store.dispute(row["dispute_id"]) or row
    why = _why(row, now)
    to, mailer = os.environ.get("OKWAN_SUPPORT_ESCALATION_TO", "").strip(), get_mailer()
    sent = False
    if why is None:
        pass                            # answered or decided in the meantime, and already told
    elif not to or mailer is None:
        logger.error("disputes: a notice (%s) was not mailed: %s", why,
                     "OKWAN_SUPPORT_ESCALATION_TO is not set" if not to else "no mailer")
    else:
        try:
            subject, text = await message(row, why, now)
            await mailer.send_notice(to, subject, text)
            sent = True
        except Exception as exc:  # noqa: BLE001 — the event is kept either way; the next round mails it
            logger.error("disputes: a notice (%s) was not sent: %s", why, type(exc).__name__)
    try:
        await store.end_dispute_notice(row["dispute_id"], claimed_at=now, sent=sent, told=heard(row), reminded_at=now)
    except Exception as exc:  # noqa: BLE001 — the right runs out by itself, and the next round mails again
        logger.error("disputes: the outcome of a notice (%s) was not recorded: %s", why, type(exc).__name__)
    return sent


async def settle(row: dict[str, Any], now: datetime) -> int:
    """Mail the operator what they have not yet heard about one dispute:
    that it exists, that an inquiry became a dispute, that it went to
    appeal, was decided, decided differently or reopened, that it is
    matched to an Okwan payment after all, or that its date is near. An
    event that landed while a mail was going out is told straight after
    it. How many mails were accepted."""
    sent = 0
    for _ in range(3):
        if not await _tell(row, now):
            break
        sent += 1
        row = await get_store().dispute(row["dispute_id"])
        if row is None:
            break
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
    put("product_description", PRODUCT, NOT_PRO.format(paid=amount(paid)) if paid and not _is_pro(paid) else None)
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
    # and read in the database, without Okwan's scheduled reads, and every
    # one of them, with or without an address that reads as one. The log
    # is kept 90 days and a cardholder has longer than that to dispute, so
    # for an older payment it says from when it speaks, never "after the
    # payment", and never that there was no use.
    kept_from = now - LOG_KEPT
    cut = bool(paid) and paid["paid_at"] < kept_from
    since = max(paid["paid_at"], kept_from) if paid else kept_from
    total, rows = await store.customer_access(tenant.id, since, LOG_LINES) if tenant and paid else (0, [])
    lines = [f"{stamp(r['at'])}  {_address(r['ip']) or NO_ADDRESS}  {' '.join(str(r['surface']).split())[:80]}"
             for r in rows]
    lost = (f"Okwan keeps its record of requests for {LOG_KEPT.days} days and this payment was taken "
            f"{day(paid['paid_at'])}, longer ago than that: " if cut else "")
    head = ((f"Okwan keeps its record of requests for {LOG_KEPT.days} days. Requests made with this account's own "
             f"sign-in and keys since {stamp(kept_from)}, as far back as that record now reaches: " if cut else
             "Requests to Okwan made with this account's own sign-in and keys after the payment: ")
            + f"{total} on record, the first {{n}} here. Each line: time, the caller's address, and what was "
            "read.")
    notes = ["The addresses are what the network in front of Okwan reported for each request."]
    if cut:
        notes.append(f"{lost}requests from before {day(kept_from)} are no longer on record, so the use made "
                     "straight after paying cannot be shown from here.")
    if lines:
        put("access_activity_log", head.format(n=len(lines)) + "\n" + "\n".join(lines), " ".join(notes))
    elif cut:
        put("access_activity_log", None,
            f"{lost}nothing is on record since {day(kept_from)}, and whether the service was used before that can "
            "no longer be shown from here. Do not tell the bank there was no use.")
    else:
        put("access_activity_log", None,
            "No use of the service is on record after this payment." if tenant and paid else
            "The account or the payment is not on record here, so its use cannot be listed.")

    earlier = None
    if tenant and paid:
        # A charge of another amount is not the same thing charged twice,
        # whatever it was for.
        earlier = next((p for p in await store.payments_for(tenant.id)
                        if p["paid_at"] <= paid["paid_at"] and p["charge_id"] != paid["charge_id"]
                        and (p["amount_minor"], p["currency"]) == (paid["amount_minor"], paid["currency"])), None)
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
        put("duplicate_charge_id", None, "No earlier payment of this amount by this account is on record.")
        put("duplicate_charge_explanation", None)

    # Stripe takes 150,000 characters across every field: the log gives way
    # first, then the policy's text, which can go as a file. Whatever is
    # still too long after that goes too, longest first: the limit holds
    # for any record, not only for the ones foreseen.
    while size() > EVIDENCE_CHARACTERS and lines:
        lines.pop()
        put("access_activity_log", (head.format(n=len(lines)) + "\n" + "\n".join(lines)) if lines else None,
            " ".join(["Shortened to fit Stripe's limit on an answer's length.", *notes[1:]]))
    if size() > EVIDENCE_CHARACTERS:
        put("uncategorized_text", None, "Left out to fit Stripe's limit: attach the policy as a file instead.")
    while size() > EVIDENCE_CHARACTERS:
        longest = max(fields, key=lambda name: len(fields[name]["text"] or ""))
        put(longest, None, "Left out to fit Stripe's limit on an answer's length: say it in your own words.")
    return pack(version if disclosure else None)
