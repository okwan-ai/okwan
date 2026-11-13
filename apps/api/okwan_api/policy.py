"""Okwan's refund and cancellation policy, in one place.

The page at okwan.ai/refunds is what customers read. This module holds the
same sentences, because three things need them and must not drift from it:

* Stripe Checkout shows `at_checkout()` beside the pay button, so the
  policy is in front of the customer before any card is charged;
* the account's billing record keeps `VERSION`, the policy that was shown
  when its checkout was opened;
* a dispute is answered with the policy's own words and with how and when
  it was shown (docs/ops/RUNBOOK.md, "A payment is disputed").

`tests/test_refund_policy.py` reads the published page and fails if a
sentence here is not on it, or if the Terms stop saying what this is drawn
from (Terms of Service, "Plans and fees"). A change to the policy is a
change to both, with a new `VERSION`.
"""
from __future__ import annotations

import os

#: The date of the policy in force: the "Last updated" date on the page.
VERSION = "2026-11-13"
UPDATED = "November 13, 2026"

#: (section, sentences). Plain text, exactly as the page says it.
POLICY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("What you pay for", (
        "Pro is 99 US dollars a month, billed monthly in advance by card through Stripe.",
        "Each payment covers one month of Pro, starting the day it is taken, and it renews each month until you cancel.",
        "The free plan costs nothing and needs no card.",
    )),
    ("Cancelling", (
        "You can cancel at any time from Manage billing in the dashboard, under Settings, Plan & usage.",
        ("Cancelling takes effect at the end of the month you have paid for: Pro stays on until then, no further "
         "payment is taken, and the account then returns to the free plan."),
        "Nothing in your account is deleted when it does.",
    )),
    ("Refunds", (
        "A payment for Pro is not refunded, in whole or in part, once its month has started.",
        "Cancelling stops the next payment; it does not return the last one.",
        ("If we took a payment by mistake, such as charging you twice for the same month or charging you after a "
         "cancellation had taken effect, tell us and we refund that payment in full."),
        "Where the law that applies to you gives you a right to a refund, you have it.",
        "A plan larger than Pro is refunded as its order form or agreement says.",
    )),
    ("How to ask", (
        "Email us from your sign-in address with the date and amount of the payment.",
        "A refund goes back through Stripe to the card that paid; how soon it shows is up to your bank.",
    )),
    ("If a payment looks wrong", (
        "Please write to us before asking your bank to reverse a payment.",
        ("We can see what happened and put a mistake right directly, and a dispute through a bank can take weeks "
         "or months to decide."),
    )),
    ("Changes", (
        "If this policy changes, the new version is posted here with its date.",
        "A payment is covered by the version that was shown when it was made.",
    )),
)


def site_url() -> str:
    return (os.environ.get("OKWAN_SITE_URL", "").strip() or "https://okwan.ai").rstrip("/")


def refunds_url() -> str:
    return f"{site_url()}/refunds"


def terms_url() -> str:
    return f"{site_url()}/terms"


def text() -> str:
    """The whole policy as plain text, for a dispute's evidence."""
    lines = [f"Okwan: Refunds and cancellation (version {VERSION}), {refunds_url()}", ""]
    for title, sentences in POLICY:
        lines += [title, *sentences, ""]
    return "\n".join(lines).rstrip() + "\n"


def at_checkout() -> str:
    """What Stripe Checkout shows beside the pay button (Markdown, at most
    1,200 characters): the price, that it renews, how to stop it, that a
    month is not refunded, and where the policy is."""
    return (
        "Pro is 99 US dollars a month and renews each month until you cancel. Cancel any time from "
        "**Manage billing** in the dashboard: Pro stays on to the end of the month you have paid for. "
        "A payment is not refunded once its month has started. "
        f"[Refunds and cancellation]({refunds_url()}) · [Terms of Service]({terms_url()})"
    )


def acceptance() -> str:
    """The words beside the box a customer ticks, when agreement is required."""
    return (f"I agree to Okwan's [Terms of Service]({terms_url()}) and its "
            f"[Refunds and cancellation]({refunds_url()}) policy")


def disclosure() -> str:
    """How and where the policy is put in front of a customer before they
    pay: said to a card issuer in these words."""
    return (
        "Before paying, the customer was shown the price, that the plan renews monthly until cancelled, how to "
        "cancel, and that a payment is not refunded once its month has started, with a link to the full policy, "
        "in two places: on the plan page of the dashboard next to the Upgrade button, and on Stripe "
        f"Checkout's payment page beside the pay button. The policy is published at {refunds_url()}."
    )
