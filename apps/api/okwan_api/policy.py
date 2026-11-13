"""Okwan's refund and cancellation policy: the one place it is written.

Everything that states the policy is made from this module, so no two of
them can say different things:

* the page at okwan.ai/refunds renders `apps/web/app/refunds/policy.json`
  and has no words of its own; that file is `published()`, written by
  `python -m okwan_api.policy`, and a test fails if the two differ;
* the dashboard's plan page shows `plan_page()`, sent by the API, next to
  the Upgrade button, and Stripe Checkout shows `at_checkout()` beside the
  pay button, so the policy is in front of the customer twice before any
  card is charged;
* each Checkout session's record keeps `VERSION`, the policy it showed;
* a disputed payment is answered with `text()`, the whole policy in the
  version that payment was shown, and `disclosure()`, how it was shown.

Changing the policy: change the words here, give it a new `VERSION` and
`UPDATED`, move what `said()` returned before into `ARCHIVE` under the old
version, write the page's file again, and read the Terms of Service
("Plans and fees") against it. `tests/test_refund_policy.py` holds a
digest of everything each version says (the page, the words at checkout,
the words beside the tick box, the copy for a bank and the statement of
how it was shown) and fails if any of it moves without the version, if an
earlier version is not in the archive, or if an archived version is not
word for word what was pinned while it was in force.
"""
from __future__ import annotations

import json
import os
import re

TITLE = "Refunds and cancellation"
#: The date of the policy in force: the "Last updated" date on the page.
VERSION = "2026-11-13"
UPDATED = "November 13, 2026"
#: What Pro costs a month. The plan page, Checkout and the policy all say
#: this number; the Stripe price behind OKWAN_BILLING_PRO_PRICE_ID has to
#: be this amount (docs/ops/RUNBOOK.md, "Switching on card payments").
PRO_PRICE_USD = 99
COMPANY = "Global Tech Startup LLC"
CONTACT_EMAIL = "info@globaltechstartup.com"

P, UL = "p", "ul"
_MAIL = f"[{CONTACT_EMAIL}](mailto:{CONTACT_EMAIL})"

#: Under the title. A link is written [words](address), as on the page.
INTRO: tuple[str, ...] = (
    "What happens when you cancel Okwan Pro, when a payment is refunded and when it is not.",
    ("This is part of our [Terms of Service](/terms#plans); where an agreement you have signed with us says "
     "something different, the agreement applies."),
)

#: (section, blocks). A block is a paragraph of sentences or a list of items.
POLICY: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    ("What you pay for", (
        (P, (f"Pro is {PRO_PRICE_USD} US dollars a month, billed monthly in advance by card through Stripe.",
             ("Each payment covers one month of Pro, starting the day it is taken, and it renews each month "
              "until you cancel."))),
        (P, ("The free plan costs nothing and needs no card.",)),
        (P, (("Before you pay, the plan page in the dashboard and the payment page at Stripe both show the "
              "price, that it renews, how to cancel, and a link to this page."),)),
    )),
    ("Cancelling", (
        (P, ("You can cancel at any time from Manage billing in the dashboard, under Settings, Plan & usage.",)),
        (P, (("Cancelling takes effect at the end of the month you have paid for: Pro stays on until then, no "
              "further payment is taken, and the account then returns to the free plan."),
             "Nothing in your account is deleted when it does.")),
    )),
    ("Refunds", (
        (P, ("A payment for Pro is not refunded, in whole or in part, once its month has started.",
             "Cancelling stops the next payment; it does not return the last one.")),
        (UL, (("If we took a payment by mistake, such as charging you twice for the same month or charging you "
               "after a cancellation had taken effect, tell us and we refund that payment in full."),
              "Where the law that applies to you gives you a right to a refund, you have it.",
              "A plan larger than Pro is refunded as its order form or agreement says.")),
    )),
    ("How to ask", (
        (P, (f"Email {_MAIL} from your sign-in address, with the date and amount of the payment.",)),
        (P, ("A refund goes back through Stripe to the card that paid; how soon it shows is up to your bank.",)),
    )),
    ("If a payment looks wrong", (
        (P, ("Please write to us before asking your bank to reverse a payment.",
             ("We can see what happened and put a mistake right directly, and a dispute through a bank can "
              "take weeks or months to decide."))),
    )),
    ("Changes", (
        (P, ("If this policy changes, the new version is posted here with its date.",
             "A payment is covered by the version that was shown when it was made.")),
    )),
    ("Contact", (
        (P, (f"{COMPANY}, Wyoming, United States.", f"Email {_MAIL}.")),
    )),
)

#: Earlier versions, each whole and exactly as `said()` gave it while it
#: was in force. A payment is covered by the version it was shown, so a
#: dispute over an older payment is answered with that one.
ARCHIVE: dict[str, dict[str, str]] = {}

_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def sentences() -> tuple[str, ...]:
    """Every sentence of the policy, in order."""
    return INTRO + tuple(line for _, blocks in POLICY for _, lines in blocks for line in lines)


def published() -> dict:
    """The page, whole: what apps/web/app/refunds/policy.json holds."""
    return {
        "title": TITLE, "version": VERSION, "updated": UPDATED,
        "summary": INTRO[0], "intro": " ".join(INTRO),
        "parts": [{
            "id": re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"), "title": title,
            "blocks": [{"kind": kind, "lines": [" ".join(lines)] if kind == P else list(lines)}
                       for kind, lines in blocks],
        } for title, blocks in POLICY],
    }


def page_file() -> str:
    """`published()` as the page's file is written."""
    return json.dumps(published(), indent=2, ensure_ascii=False) + "\n"


def site_url() -> str:
    return (os.environ.get("OKWAN_SITE_URL", "").strip() or "https://okwan.ai").rstrip("/")


def refunds_url() -> str:
    return f"{site_url()}/refunds"


def terms_url() -> str:
    return f"{site_url()}/terms"


def _plain(line: str) -> str:
    """A sentence without the page's link marks: the address in full."""
    def said(m: re.Match) -> str:
        words, address = m.group(1), m.group(2)
        if address.startswith("mailto:"):
            return words
        return f"{words} ({site_url()}{address})" if address.startswith("/") else f"{words} ({address})"
    return _LINK.sub(said, line)


def text(version: str | None = None) -> str:
    """The whole policy as plain text, for a dispute's evidence: the
    version in force, or the earlier one a payment was shown. An unknown
    version is a KeyError, never the wrong words."""
    if version is not None and version != VERSION:
        return ARCHIVE[version]["text"]
    out = [f"Okwan: {TITLE} (version {VERSION}), {refunds_url()}", "", *(_plain(s) for s in INTRO), ""]
    for title, blocks in POLICY:
        out += [title, *(_plain(line) for _, lines in blocks for line in lines), ""]
    return "\n".join(out).rstrip() + "\n"


def at_checkout() -> str:
    """What Stripe Checkout shows beside the pay button (Markdown, at most
    1,200 characters): the price, that it renews, how to stop it, that a
    month is not refunded, and where the policy is."""
    return (
        f"Pro is {PRO_PRICE_USD} US dollars a month and renews each month until you cancel. Cancel any time from "
        "**Manage billing** in the dashboard: Pro stays on to the end of the month you have paid for. "
        "A payment is not refunded once its month has started. "
        f"[Refunds and cancellation]({refunds_url()}) · [Terms of Service]({terms_url()})"
    )


def plan_page() -> str:
    """What the dashboard's plan page shows next to the Upgrade button,
    ahead of it in reading order: the price, that it renews, how to
    cancel, and that a month is not refunded. The page shows these words,
    sent by the API, and adds links to the policy and the Terms."""
    return (
        f"Pro is {PRO_PRICE_USD} US dollars a month and renews each month until you cancel. To cancel, use Manage "
        "billing on this page: Pro stays on to the end of the month you have paid for. A payment is not refunded "
        "once its month has started."
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


def said(version: str | None = None) -> dict[str, str]:
    """Everything a version of the policy says, wherever it says it: the
    page's file, the words on the dashboard's plan page, the words beside
    Stripe's pay button, the words beside
    the box a customer ticks, the copy sent to a bank, and the statement
    of how and where it was shown. The version in force, or an earlier
    one from the archive; an unknown version is a KeyError."""
    if version is not None and version != VERSION:
        return dict(ARCHIVE[version])
    return {"page": page_file(), "plan_page": plan_page(), "at_checkout": at_checkout(),
            "acceptance": acceptance(), "text": text(), "disclosure": disclosure()}


if __name__ == "__main__":
    # python -m okwan_api.policy > apps/web/app/refunds/policy.json
    print(page_file(), end="")
