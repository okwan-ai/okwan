"""The daily check: every connected merchant, once a day, unasked.

Until now a check ran only when someone pressed Run or an agent asked, so
an account nobody opened learned nothing. Once an hour this looks for
merchants whose Shopify, PayPal and Stripe are all connected and whose own
daily check last ran a day or more ago, and runs the same fold for each as
a dashboard run does: stored like any run, metered to the merchant like
any run, and written to the access log as Okwan's own read (credential
`schedule`, no address).

When a check finds something the account has not been told of (an order
collected twice, one that does not add up, one with no payment), the
account gets a mail naming each merchant with what is new and how much is
at stake: one mail a day at most, so a finding from a check later in the
day is in the next day's mail. Order references, customers and payment
records never go into it: the mail provider and the inbox see merchant
names, counts and totals.

What the scheduler decides, it decides from its own record
(`daily_checks`, one row a merchant, written here and by nothing a caller
can reach): when the merchant was last checked, which findings the account
has been told of, and the findings not yet mailed (each as the figures its
line in the mail needs, with no reference). None of it is read back from
stored runs, which any key can add to and so push out of history. A run
made with an API key, the dashboard or an agent changes none of it, so no
key can put a check off, hide a finding from the mail or have one sent
again. The slot is taken before the check runs, so a check that fails, or
whose result cannot be stored, is still not due again until tomorrow.

Accounts take turns: each round gives every account with something due
one check before any gets a second, the account served longest ago first,
and within an account the merchant checked longest ago. No account can
use up a round, and one slow merchant cannot hold the others past the
round's time budget.

What stops it:
- the account turns daily checks off (Settings), or only the mail;
- the plan is spent: a daily check is a check, and none runs past the plan;
- `OKWAN_DAILY_CHECKS=off` on the server, for the operator;
- no mail goes without a mailer and `OKWAN_MAIL_POSTAL_ADDRESS` (the
  sender's postal address, which US law requires on mail sent unasked),
  and every mail carries a link that turns the mail off without signing in.

One API instance runs one sweeper (docs/audit/known-gaps.md G7): a second
instance would need a lock here, or a merchant could be checked twice.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from okwan_core import get as get_connector
from okwan_core.currency import format_minor
from okwan_recon.across import AcrossRails, run_across
from okwan_recon.emitters.rest import mapped, vault_resolver
from okwan_recon.paging import MAX_ROWS
from okwan_recon.registry import get_across
from okwan_vault.authz import MAX_DEPTH
from okwan_vault.models import DailyCheck, Tenant

from . import access
from .auth import get_store, meter, quota_for
from .mail import MailNotSent, dashboard_url, get_mailer, postal_address
from .runs import record_run, run_clock

logger = logging.getLogger(__name__)

#: The check that runs daily: Shopify orders across PayPal and Stripe.
FOLD = "rails"
#: How a scheduled run is stored and metered, and what the access log names.
SURFACE = "schedule"
TARGET = "daily check"
#: A merchant is due when its own daily check last ran this long ago, or never.
DUE_AFTER = timedelta(hours=24)
#: An account is mailed findings once in this long, at most.
MAIL_EVERY = timedelta(hours=24)
SWEEP_SECONDS = 3600
#: After the server starts, before the first round: a deploy is not a reason
#: to read every merchant's rails at once.
FIRST_ROUND_AFTER = 120
#: Checks in one round. The rest are still due next hour.
MAX_PER_ROUND = 200
#: No check starts after a round has run this long (seconds).
ROUND_BUDGET = 20 * 60.0
#: One merchant's check, start to finish.
RUN_TIMEOUT = 300.0
#: The outcomes that are findings (the dashboard's ATTENTION).
FINDINGS = ("collected_twice", "collected_inconsistent", "uncollected")
#: How much of a tenant's name a mail shows.
NAME_SHOWN = 60

#: The round's clock for its time budget; a test moves it.
monotonic = time.monotonic


def enabled() -> bool:
    """Off only when the operator says so (`OKWAN_DAILY_CHECKS=off`)."""
    return os.environ.get("OKWAN_DAILY_CHECKS", "on").strip().lower() not in {"off", "false", "0", "no"}


def needs(spec: AcrossRails) -> dict[str, tuple[str, ...]]:
    """Each connector the fold reads, with the credential fields it requires."""
    names: list[str] = []
    for member in spec.members():
        for ref in (member.left, member.right):
            if ref.connector not in names:
                names.append(ref.connector)
    return {name: tuple(get_connector(name).auth.required_fields) for name in names}


def ready(need: dict[str, tuple[str, ...]], configured: dict[str, list[str]]) -> bool:
    """Every field of every connector is saved. Field names only: nothing
    is decrypted to decide whether a merchant can be checked."""
    return all(set(fields) <= set(configured.get(name, ())) for name, fields in need.items())


def fingerprint(row: dict[str, Any]) -> str:
    """One finding, as the scheduler remembers it: a hash of the order's
    reference and the outcome, so its record holds no order reference. The
    reference is read the way a stored run keeps it (postgres `_storable`),
    so a finding hashes the same from a fresh result and from the stored run."""
    ref = str((row.get("order") or {}).get("ref")).replace("\x00", "\ufffd")
    text = f"{row.get('outcome')}\x1f{ref}"
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:20]


def _findings(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [r for r in rows or [] if r.get("outcome") in FINDINGS]


class RecordUnavailable(RuntimeError):
    """The scheduler could not write down that a merchant's slot is taken.
    The round stops there, before any rail is read: with no record, the
    same merchants would be read again every hour for as long as it lasted."""


@dataclass
class Found:
    """What one merchant's daily check found that the account has not been told of."""

    tenant: Tenant
    rows: list[dict[str, Any]]


@dataclass
class Round:
    checked: int = 0
    failed: int = 0
    out_of_plan: int = 0
    mailed: int = 0
    found: list[Found] = field(default_factory=list)


@dataclass
class _Account:
    """One account in a round: its settings, the merchants the account can
    itself open, and those due, the one checked longest ago first."""

    root: Tenant
    settings: dict
    members: list[Tenant]
    states: dict[str, DailyCheck]
    due: list[Tenant]

    @property
    def last_served(self) -> datetime:
        return max((s.checked_at for s in self.states.values()), default=datetime.min.replace(tzinfo=UTC))


def _subtree(root: Tenant, under: dict[str | None, list[Tenant]]) -> list[Tenant]:
    """The account and what is below it, as far down as the account itself
    can open (authz.MAX_DEPTH) and its plan is found: nothing deeper is
    checked, metered or mailed about."""
    out, level = [root], [root]
    for _ in range(MAX_DEPTH):
        level = [child for t in level for child in sorted(under.get(t.id, []), key=lambda t: t.id)]
        out += level
    return out


async def run_due(now: datetime | None = None) -> Round:
    """One round: the checks that are due, accounts taking turns, then the
    day's mail for each account that has something it has not been told."""
    store = get_store()
    now = now or datetime.now(UTC)
    spec = get_across(FOLD)
    need = needs(spec)
    under: dict[str | None, list[Tenant]] = {}
    for t in await store.list_tenants():
        under.setdefault(t.parent_id, []).append(t)

    accounts: list[_Account] = []
    for root in under.get(None, []):
        settings = await store.get_notify(root.id)
        if not settings["daily_checks"]:
            continue
        members = _subtree(root, under)
        states = await store.daily_checks([t.id for t in members])
        never = datetime.min.replace(tzinfo=UTC)
        due = sorted(
            (t for t in members if t.id not in states or now - states[t.id].checked_at >= DUE_AFTER),
            key=lambda t: (states[t.id].checked_at if t.id in states else never, t.id))
        accounts.append(_Account(root, settings, members, states, due))

    out = Round()
    started = monotonic()
    waiting = sorted((a for a in accounts if a.due), key=lambda a: (a.last_served, a.root.id))

    def room() -> bool:
        return out.checked + out.failed < MAX_PER_ROUND and monotonic() - started < ROUND_BUDGET

    while waiting and room():
        for account in list(waiting):
            if not room():
                break
            tenant = None
            while account.due and tenant is None:
                candidate = account.due.pop(0)
                if ready(need, await store.connectors_configured(candidate.id)):
                    tenant = candidate
            if tenant is None:
                waiting.remove(account)
                continue
            if (await quota_for(account.root)).exceeded:
                # The account shares one plan, so none of its merchants runs.
                out.out_of_plan += 1
                logger.info("daily checks skipped for %s: its plan is spent", account.root.id)
                waiting.remove(account)
                continue
            try:
                new = await _check(tenant, spec, account.states.get(tenant.id), now)
            except RecordUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 — one merchant must not end everyone's round
                logger.warning("daily check failed for %s: %s", tenant.id, type(exc).__name__)
                new = None
            if new is None:
                out.failed += 1
                continue
            out.checked += 1
            if new:
                out.found.append(Found(tenant, new))

    for account in accounts:
        try:
            if await _mail_due(account, now):
                out.mailed += 1
        except Exception as exc:  # noqa: BLE001 — one account must not stop the others' mail
            logger.warning("findings for %s not mailed: %s", account.root.id, type(exc).__name__)
    return out


async def _check(
    tenant: Tenant, spec: AcrossRails, state: DailyCheck | None, now: datetime,
) -> list[dict[str, Any]] | None:
    """Run one merchant's check as a dashboard run does. The findings the
    account has not been told of; None when it failed (stored, as a failed
    run) or its result could not be stored."""
    store = get_store()
    # Before anything is read: whatever happens next, today's slot is used.
    try:
        await store.claim_daily_check(tenant.id, now)
    except KeyError:
        return None  # deleted since the round began
    except Exception as exc:
        raise RecordUnavailable(f"the daily check's record could not be written: {type(exc).__name__}") from exc
    told = set(state.told) if state else set()
    started = run_clock()
    access.remember_schedule(TARGET)
    resolver = None
    try:
        resolver = await vault_resolver(tenant)
        result = await asyncio.wait_for(mapped(run_across(spec, resolver)), RUN_TIMEOUT)
    except HTTPException as exc:
        await record_run(tenant, spec, SURFACE, started, error=exc.detail, resolver=resolver)
        logger.info("daily check failed for %s: HTTP %s", tenant.id, exc.status_code)
        return None
    except TimeoutError:
        await record_run(tenant, spec, SURFACE, started, resolver=resolver,
                         error=f"the check took longer than {int(RUN_TIMEOUT // 60)} minutes and was stopped")
        logger.info("daily check timed out for %s", tenant.id)
        return None
    except Exception as exc:  # noqa: BLE001 — one merchant's failure must not end the round
        await record_run(tenant, spec, SURFACE, started, error=type(exc).__name__, resolver=resolver)
        logger.warning("daily check failed for %s: %s", tenant.id, type(exc).__name__)
        return None
    run_id = await record_run(tenant, spec, SURFACE, started, result=result)
    if run_id is None:
        # Nothing to show the account and nothing to mail from: not a check
        # it is charged for. Tomorrow's tries again.
        logger.warning("daily check for %s ran but its result could not be stored", tenant.id)
        return None
    await meter(tenant, f"{SURFACE}:across", target=TARGET)
    found = _findings(result.trimmed_rows(spec.ledger_currency, limit=MAX_ROWS))
    seen = {fingerprint(r) for r in found}
    new = [_news(r) for r in found if fingerprint(r) not in told]
    # A finding that has gone is forgotten, so it is news if it comes back.
    # What is new is kept here until it is mailed, not looked up in the
    # stored run later: any key can add runs and push that one out of history.
    await store.settle_daily_check(tenant.id, told=sorted(told & seen), news=new)
    return new


def _news(row: dict[str, Any]) -> dict[str, Any]:
    """What the mail's line needs of one finding, and nothing more: its
    fingerprint, the outcome, a currency code and the amounts. No reference
    and no record; and only values of the expected kind, so nothing a rail
    sent as text is kept for the mail."""
    currency = (row.get("order") or {}).get("currency")

    def minor(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return {
        "mark": fingerprint(row),
        "outcome": row.get("outcome"),
        "order": {"currency": currency if isinstance(currency, str) and _CURRENCY.fullmatch(currency) else None},
        "order_total_minor": minor(row.get("order_total_minor")),
        "collected_minor": minor(row.get("collected_minor")),
    }


# ── the mail ───────────────────────────────────────────────────────────

def mail_blocked() -> str | None:
    """Why no finding mail can go out from this server, or None."""
    if get_mailer() is None:
        return "no mailer is configured"
    if not postal_address():
        return "OKWAN_MAIL_POSTAL_ADDRESS is not set"
    return None


async def _mail_due(account: _Account, now: datetime) -> bool:
    """The account's mail for today, if it is owed one: every finding its
    merchants' daily checks hold that it has not been told of. True when a
    mail went."""
    settings, root = account.settings, account.root
    if not settings["finding_emails"]:
        return False
    last = settings.get("mailed_at")
    if last is not None and now - last < MAIL_EVERY:
        return False
    store = get_store()
    states = await store.daily_checks([t.id for t in account.members])
    waiting = [(t, states[t.id]) for t in account.members if t.id in states and states[t.id].news]
    if not waiting:
        return False
    blocked = mail_blocked()
    if blocked:
        logger.info("findings for %s not mailed: %s", root.id, blocked)
        return False
    email = await store.account_email(root.id)
    if not email:
        return False

    found = [Found(tenant, [dict(n) for n in state.news]) for tenant, state in waiting]
    link = unsubscribe_link(await store.notify_token(root.id))
    subject, text = message(root.name, found, link, postal_address())
    # Today's mail is marked as sent before it goes: whatever fails after
    # this, the account is not mailed a second time today.
    await store.notify_mailed(root.id, now)
    try:
        await get_mailer().send_notice(email, subject, text, headers={"List-Unsubscribe": f"<{link}>"})
    except Exception as e:  # noqa: BLE001 — a mail that fails must not end the round
        logger.error("findings for %s not mailed: %s", root.id, e if isinstance(e, MailNotSent) else type(e).__name__)
        # It did not go: put the mark back, so the next round tries again.
        await store.notify_mailed(root.id, last)
        return False
    for tenant, state in waiting:
        marks = {n["mark"] for n in state.news}
        await store.settle_daily_check(tenant.id, told=sorted(set(state.told) | marks), news=[])
    return True


def unsubscribe_link(token: str) -> str:
    """The dashboard page that turns the mail off, with a button: a link
    that did it on a GET would be pressed by every mail scanner."""
    return f"{dashboard_url()}/unsubscribe?token={token}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


_CURRENCY = re.compile(r"[A-Z]{3}")
_LINK = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://|\bwww\.")
#: A full stop, in any of the forms a mail client reads as one, with a
#: letter or digit straight after it: what makes "shop.example.com" a link.
_DOT = re.compile(r"[.\uff0e\u3002\uff61](?=\w)")


def shown(name: str, limit: int = NAME_SHOWN, *, own: bool = False) -> str:
    """A tenant's name as a mail shows it: one line, bounded, with no
    control or invisible character, no web address scheme, and a space
    after any full stop that would join two words into an address a mail
    client makes a link ("shop. example. com"). Whoever can name a merchant
    chooses these words in the account's mail and nothing else: not a
    line, not a header, not a link Okwan appears to give. `own` is the
    account's own name, set at sign-up or by the operator: its full stops
    are left alone."""
    text = "".join(" " if unicodedata.category(c)[0] in "CZ" else c for c in str(name))
    text = _LINK.sub(" ", text)
    if not own:
        text = _DOT.sub(". ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or "(no name)"


def _sum(rows: list[dict[str, Any]], amount) -> str:
    """Amounts by currency, as "$1,449.00" or "$1,449.00 and 300.00 EUR";
    empty when any row lacks a figure, so a total is never an undercount,
    or when a currency is not three letters: it comes from the merchant's
    own store, and only a currency code is let into the mail."""
    totals: dict[str, int] = {}
    for r in rows:
        value, currency = amount(r), (r.get("order") or {}).get("currency")
        if value is None or not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
            return ""
        totals[currency] = totals.get(currency, 0) + value
    return " and ".join(format_minor(v, c) for c, v in sorted(totals.items()))


def _owed(row: dict[str, Any]) -> int | None:
    total, taken = row.get("order_total_minor"), row.get("collected_minor")
    return None if total is None or taken is None else taken - total


def lines(rows: list[dict[str, Any]]) -> list[str]:
    """What is new at one merchant, one line an outcome."""
    by = {o: [r for r in rows if r.get("outcome") == o] for o in FINDINGS}
    out = []
    if twice := by["collected_twice"]:
        owed = _sum(twice, _owed)
        out.append(f"{_plural(len(twice), 'order', 'orders')} collected twice" + (f": {owed} owed back" if owed else ""))
    if odd := by["collected_inconsistent"]:
        out.append(f"{_plural(len(odd), 'order', 'orders')} where the payments don't add up to the order")
    if unpaid := by["uncollected"]:
        due = _sum(unpaid, lambda r: r.get("order_total_minor"))
        out.append(f"{_plural(len(unpaid), 'order', 'orders')} with no payment found" + (f": {due} unpaid" if due else ""))
    return out


def message(account: str, found: list[Found], unsubscribe: str, address: str) -> tuple[str, str]:
    """The subject and plain text of the account's mail. The subject is
    Okwan's words only; a name appears in the body, cleaned (`shown`)."""
    total = sum(len(f.rows) for f in found)
    what = _plural(total, "new finding", "new findings")
    subject = f"Okwan: {what}" + (f" across {len(found)} merchants" if len(found) > 1 else "")
    base = dashboard_url()
    account = shown(account, own=True)
    parts = [f"Okwan's daily check found something new for {account}.", ""]
    for f in found:
        parts.append(shown(f.tenant.name))
        parts += [f"  {line}" for line in lines(f.rows)]
        parts += [f"  {base}/merchants/{f.tenant.id}?tab=findings", ""]
    parts += [
        "Okwan only reads. Any refund is made in PayPal or Stripe, by a person.",
        "",
        "--",
        f"You get this because daily checks are on for {account}.",
        f"Turn these emails off: {unsubscribe}",
        f"Change what runs: {base}/settings#daily-checks",
        f"Okwan · {address}",
    ]
    return subject, "\n".join(parts) + "\n"


# ── the sweeper ────────────────────────────────────────────────────────

async def sweeper(interval: float = SWEEP_SECONDS, first_after: float = FIRST_ROUND_AFTER) -> None:
    """A round an hour, for the life of the process. A failure is logged
    and the next round tries again; it never stops the API."""
    await asyncio.sleep(first_after)
    while True:
        if enabled():
            try:
                r = await run_due()
                if r.checked or r.failed or r.out_of_plan:
                    logger.info("daily checks: %d run, %d failed, %d accounts out of plan, %d mailed",
                                r.checked, r.failed, r.out_of_plan, r.mailed)
            except Exception:
                logger.warning("daily checks round failed", exc_info=True)
        await asyncio.sleep(interval)
