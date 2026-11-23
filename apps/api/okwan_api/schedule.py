"""The daily check: every connected merchant, once a day, unasked.

Until now a check ran only when someone pressed Run or an agent asked, so
an account nobody opened learned nothing. Once an hour this looks for
merchants whose Shopify, PayPal and Stripe are all connected and whose own
daily check last ran a day or more ago, and runs the same fold for each as
a dashboard run does: stored like any run, metered to the merchant like
any run, and written to the access log as Okwan's own read (credential
`schedule`, no address).

Once a day at most, the account is mailed what is new on its open items
(okwan_api.exceptions): every merchant under it, every check, the checks
that read imported files included, and whatever ran them. An item is in
the mail when it is new to the account, and once more when it has been
open the account's number of days (seven unless it says); the items at or
above the account's amount (1,000 of their own currency unless it says)
are named again in each mail while they stay open. A mail goes only when
something is new or has newly aged, and says nothing of an item a person
explained or resolved. References, customers and payment records never go
into it: the mail provider and the inbox see merchant names, the checks'
titles, counts and totals.

When a merchant is due, the scheduler decides from its own record
(`daily_checks`, one row a merchant, written here and by nothing a caller
can reach), not from stored runs, which any key can add to: a run made
with an API key, the dashboard or an agent cannot put a check off. The
slot is taken before the check runs, so a check that fails, or whose
result cannot be stored, is still not due again until tomorrow.

What the mail has told an account of a merchant is the scheduler's own
record too (`mail_told`): when it last named the merchant, and, for each
check, when the result it read then was read from the rails. An item is
new when it was first seen after that (a stored row carries the time its
finding was first seen, from run to run), and newly old when it has
passed the account's days since that mail. Times only: no item, key or
reference is kept for the mail. What is open is the open-items list, as
the dashboard shows it, read a few merchants at a time and within a
budget a look; the next look starts where one stopped.

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
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException
from okwan_core import get as get_connector
from okwan_core.currency import format_minor, minor_unit_factor
from okwan_recon.across import AcrossRails, run_across
from okwan_recon.emitters.rest import mapped, vault_resolver
from okwan_recon.exceptions import holds, open_items, run_found
from okwan_recon.registry import get_across
from okwan_vault.authz import MAX_DEPTH
from okwan_vault.models import AGING_DAYS, LARGE_AMOUNT, DailyCheck, Tenant

from . import access
from .auth import get_store, meter, quota_for
from .exceptions import _titles, _unseen
from .mail import MailNotSent, dashboard_url, get_mailer, postal_address
from .runs import file_check_names, record_run, run_clock

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
#: How much of a tenant's name a mail shows.
NAME_SHOWN = 60
#: Merchants one mail names, those it told longest ago first; the others
#: are counted, and their news waits for the next mail.
MAIL_MERCHANTS = 25
#: Merchants whose stored results are read at once for the mail: few, so
#: what one read decodes is small.
READ_CHUNK = 5
#: What one look at an account reads at most: open items, and merchants.
#: The next look starts where this one stopped.
LOOK_ITEMS = 100_000
LOOK_MERCHANTS = 2_000
#: A look that read every merchant and found nothing to mail is not made
#: again for this long.
LOOK_EVERY = timedelta(hours=6)
#: Merchants whose scheduler records are read at once.
STATE_CHUNK = 200
#: Where each account's next look starts, and until when it is quiet. Kept
#: in this process (one sweeper, G7), and emptied rather than let grow
#: past a bound.
_looks: dict[str, Look] = {}
LOOKS_KEPT = 50_000

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


class RecordUnavailable(RuntimeError):
    """The scheduler could not write down that a merchant's slot is taken.
    The round stops there, before any rail is read: with no record, the
    same merchants would be read again every hour for as long as it lasted."""


class Tally:
    """How many items, and what they put at stake by currency, in minor
    units. A total is stated only when every item has a figure and a
    three-letter currency code: never an undercount, and nothing a
    merchant's systems or files sent as text but a code."""

    __slots__ = ("n", "sums", "whole")

    def __init__(self) -> None:
        self.n, self.sums, self.whole = 0, {}, True

    def add(self, item: dict[str, Any]) -> None:
        self.n += 1
        minor, currency = item.get("amount_minor"), item.get("currency")
        if (not isinstance(minor, int) or isinstance(minor, bool) or not isinstance(currency, str)
                or not _CURRENCY.fullmatch(currency)):
            self.whole = False
            return
        self.sums[currency] = self.sums.get(currency, 0) + minor

    def merge(self, other: Tally) -> None:
        self.n += other.n
        self.whole = self.whole and other.whole
        for c, v in other.sums.items():
            self.sums[c] = self.sums.get(c, 0) + v

    def total(self) -> str:
        """"$1,449.00", or "$1,449.00 and 300.00 EUR"; empty when it cannot
        be stood behind."""
        if not self.n or not self.whole:
            return ""
        return " and ".join(format_minor(v, c) for c, v in sorted(self.sums.items()))

    def counted(self) -> str:
        """"3 items, $1,449.00 at stake", or the count alone."""
        at_stake = self.total()
        return _plural(self.n, "item", "items") + (f", {at_stake} at stake" if at_stake else "")


@dataclass
class News:
    """What a look found of one merchant, in figures only: what is new to
    the account (by check, and what each finding is), what has newly been
    open the account's days, what is at or above its amount, and how many
    more its checks found than their stored results keep (paging.MAX_ROWS),
    which no mail can tell apart. `through` is, for each check, when the
    result read was read from the rails: what a mail naming the merchant
    tells the account through."""

    tenant: Tenant
    told_at: datetime | None
    new: Tally = field(default_factory=Tally)
    new_by: dict[str, dict[str, int]] = field(default_factory=dict)
    aged: Tally = field(default_factory=Tally)
    large: Tally = field(default_factory=Tally)
    beyond: int = 0
    through: dict[str, str] = field(default_factory=dict)

    @property
    def any(self) -> bool:
        return bool(self.new.n or self.aged.n)


@dataclass
class Look:
    """Where an account's next look starts; whether the looks since the
    last one that started from the first merchant found nothing; and until
    when the account is quiet."""

    cursor: int = 0
    clean: bool = True
    quiet_until: datetime | None = None


@dataclass
class Round:
    checked: int = 0
    failed: int = 0
    out_of_plan: int = 0
    mailed: int = 0


@dataclass
class _Account:
    """One account in a round: its settings, the merchants the account can
    itself open, and those due, the one checked longest ago first."""

    root: Tenant
    settings: dict
    members: list[Tenant]
    states: dict[str, DailyCheck] = field(default_factory=dict)
    due: list[Tenant] = field(default_factory=list)

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
    mailing: list[_Account] = []
    for root in under.get(None, []):
        settings = await store.get_notify(root.id)
        members = _subtree(root, under)
        # The mail is about the open items, whatever ran the checks behind
        # them, so an account with daily checks off still hears of them; of
        # every merchant the account can open, as the daily check is.
        mailing.append(_Account(root, settings, members))
        if not settings["daily_checks"]:
            continue
        states: dict[str, DailyCheck] = {}
        for start in range(0, len(members), STATE_CHUNK):
            states |= await store.daily_checks([t.id for t in members[start:start + STATE_CHUNK]])
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
                ran = await _check(tenant, spec, now)
            except RecordUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 — one merchant must not end everyone's round
                logger.warning("daily check failed for %s: %s", tenant.id, type(exc).__name__)
                ran = False
            if not ran:
                out.failed += 1
                continue
            out.checked += 1

    for account in mailing:
        try:
            if await _mail_due(account, now):
                out.mailed += 1
        except Exception as exc:  # noqa: BLE001 — one account must not stop the others' mail
            logger.warning("open items for %s not mailed: %s", account.root.id, type(exc).__name__)
    return out


async def _check(tenant: Tenant, spec: AcrossRails, now: datetime) -> bool:
    """Run one merchant's check as a dashboard run does. False when it
    failed (stored, as a failed run) or its result could not be stored. What
    it found reaches the mail through the open items, as any run's does."""
    store = get_store()
    # Before anything is read: whatever happens next, today's slot is used.
    try:
        await store.claim_daily_check(tenant.id, now)
    except KeyError:
        return False  # deleted since the round began
    except Exception as exc:
        raise RecordUnavailable(f"the daily check's record could not be written: {type(exc).__name__}") from exc
    started = run_clock()
    access.remember_schedule(TARGET)
    resolver = None
    try:
        resolver = await vault_resolver(tenant)
        result = await asyncio.wait_for(mapped(run_across(spec, resolver)), RUN_TIMEOUT)
    except HTTPException as exc:
        await record_run(tenant, spec, SURFACE, started, error=exc.detail, resolver=resolver)
        logger.info("daily check failed for %s: HTTP %s", tenant.id, exc.status_code)
        return False
    except TimeoutError:
        await record_run(tenant, spec, SURFACE, started, resolver=resolver,
                         error=f"the check took longer than {int(RUN_TIMEOUT // 60)} minutes and was stopped")
        logger.info("daily check timed out for %s", tenant.id)
        return False
    except Exception as exc:  # noqa: BLE001 — one merchant's failure must not end the round
        await record_run(tenant, spec, SURFACE, started, error=type(exc).__name__, resolver=resolver)
        logger.warning("daily check failed for %s: %s", tenant.id, type(exc).__name__)
        return False
    run_id = await record_run(tenant, spec, SURFACE, started, result=result)
    if run_id is None:
        # Nothing to show the account and nothing to mail from: not a check
        # it is charged for. Tomorrow's tries again.
        logger.warning("daily check for %s ran but its result could not be stored", tenant.id)
        return False
    await meter(tenant, f"{SURFACE}:across", target=TARGET)
    return True


# ── the mail ───────────────────────────────────────────────────────────

def mail_blocked() -> str | None:
    """Why no finding mail can go out from this server, or None."""
    if get_mailer() is None:
        return "no mailer is configured"
    if not postal_address():
        return "OKWAN_MAIL_POSTAL_ADDRESS is not set"
    return None


def large(item: dict[str, Any], amount: int) -> bool:
    """An item at or above the account's amount, in whole units of the
    item's own currency (0: no item is large). Integer arithmetic only."""
    minor, currency = item.get("amount_minor"), item.get("currency")
    return (bool(amount) and isinstance(minor, int) and not isinstance(minor, bool)
            and isinstance(currency, str) and _CURRENCY.fullmatch(currency) is not None
            and minor >= amount * minor_unit_factor(currency))


def _told(record: Any) -> tuple[datetime | None, dict[str, datetime]]:
    """When a mail last named the merchant, and through when it told each
    check's result. The scheduler's own record; anything else reads as
    never told."""
    if not isinstance(record, dict):
        return None, {}
    at = _instant(record.get("at"))
    through = record.get("through") if isinstance(record.get("through"), dict) else {}
    return at, {k: t for k, v in through.items() if isinstance(k, str) and (t := _instant(v)) is not None}


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def weigh(news: News, check: str, title: str, run_at: datetime, items: list[dict[str, Any]],
          shut: set[str], told_at: datetime | None, through: datetime | None,
          days: int, amount: int, now: datetime) -> None:
    """Add one check's open items to the merchant's news, by figures only.
    An item is new when the account was never told of this check, or the
    item was first seen after the result last told; newly old when it was
    told and has passed the account's days since that mail; large while it
    is open at or above the account's amount. `shut`: the keys a person
    explained or resolved on the terms they stand on now."""
    news.through[check] = run_at.isoformat()
    since_told = (now.date() - told_at.date()).days if told_at is not None else None
    for i in items:
        if i["key"] in shut:
            continue
        if amount and large(i, amount):
            news.large.add(i)
        first = _instant(i.get("first_seen"))
        if told_at is None or through is None or (first is not None and first > through):
            news.new.add(i)
            said = news.new_by.setdefault(title, {})
            what = WHAT.get(i.get("finding"), "needs a look")
            said[what] = said.get(what, 0) + 1
            continue
        open_days = i.get("open_days")
        if isinstance(open_days, int) and open_days >= days and open_days - since_told < days:
            news.aged.add(i)


async def _merchant(store, tenant: Tenant, runs: list, titles: dict, record: Any,
                    days: int, amount: int, now: datetime) -> tuple[News, int]:
    """One merchant's news, and how many items were read for it. Its items
    are worked out off the event loop and held only while this runs."""
    told_at, through = _told(record)
    news, read = News(tenant, told_at), 0
    for run in runs:
        title = titles.get((run.kind, run.name))
        if title is None:  # no longer declared
            continue
        items = await asyncio.to_thread(open_items, tenant.id, run.name, run.kind, run.rows, now)
        read += len(items)
        news.beyond += max(run_found(run.kind, run.summary) - len(items), 0)
        current = await store.current_decisions([(tenant.id, i["key"]) for i in items])
        shut = {i["key"] for i in items if holds(current.get((tenant.id, run.kind, run.name, i["key"])), i)}
        check = f"{run.kind}/{run.name}"
        await asyncio.to_thread(weigh, news, check, title, run.started_at, items, shut, told_at,
                                through.get(check), days, amount, now)
    return news, read


async def look(store, account: _Account, now: datetime, days: int, amount: int) -> tuple[list[News], bool]:
    """What the account's merchants have that a mail could tell, in figures,
    from where the last look stopped and within LOOK_ITEMS and
    LOOK_MERCHANTS; and whether this look reached the last merchant. A
    merchant's expired files go first, with the results that read them, as
    before a suggestion: the mail sends what it reads to the mail provider."""
    titles, files = _titles(), set(file_check_names())
    members = account.members
    state = _looks.setdefault(account.root.id, Look())
    start = state.cursor if state.cursor < len(members) else 0
    if start == 0:
        state.clean = True
    order = members[start:]
    found: list[News] = []
    items = merchants = 0
    for at in range(0, len(order), READ_CHUNK):
        if items >= LOOK_ITEMS or merchants >= LOOK_MERCHANTS:
            break
        chunk = order[at:at + READ_CHUNK]
        ids = [t.id for t in chunk]
        runs = await store.newest_runs_all(ids, ok_only=True, with_rows=True)
        stale = sorted({r.tenant_id for r in runs if r.name in files})
        if any([await store.drop_expired_imports(tid, now) for tid in stale]):
            runs = await store.newest_runs_all(ids, ok_only=True, with_rows=True)
        told = await store.mail_told(ids)
        for t in chunk:
            mine = [r for r in runs if r.tenant_id == t.id]
            news, read = await _merchant(store, t, mine, titles, told.get(t.id), days, amount, now)
            items += read
            if news.any or news.large.n:
                found.append(news)
        merchants += len(chunk)
    reached = merchants >= len(order)
    state.cursor = 0 if reached else start + merchants
    return found, reached


async def _mail_due(account: _Account, now: datetime) -> bool:
    """The account's mail for today, if it is owed one and has news. True
    when a mail went."""
    settings, root = account.settings, account.root
    if not settings["finding_emails"]:
        return False
    last = settings.get("mailed_at")
    if last is not None and now - last < MAIL_EVERY:
        return False
    blocked = mail_blocked()
    if blocked:
        logger.info("open items for %s not mailed: %s", root.id, blocked)
        return False
    state = _looks.get(root.id)
    if state is not None and state.quiet_until is not None and now < state.quiet_until:
        return False
    store = get_store()
    email = await store.account_email(root.id)
    if not email:
        return False
    days = settings.get("aging_days") or AGING_DAYS
    amount = settings.get("large_amount", LARGE_AMOUNT) or 0
    if len(_looks) >= LOOKS_KEPT and root.id not in _looks:
        _looks.clear()
    found, reached = await look(store, account, now, days, amount)
    news = [n for n in found if n.any]
    never = datetime.min.replace(tzinfo=UTC)
    listed = sorted(news, key=lambda n: (n.told_at or never, n.tenant.id))[:MAIL_MERCHANTS]
    state = _looks.setdefault(root.id, Look())
    state.clean = state.clean and not news
    # Quiet only once looks from the first merchant to the last found nothing.
    state.quiet_until = now + LOOK_EVERY if reached and state.clean else None
    if not listed:
        return False
    big = sorted((n for n in found if n.large.n), key=lambda n: (-n.large.n, n.tenant.id))
    link = unsubscribe_link(await store.notify_token(root.id))
    subject, text = message(root.name, news, listed, big, link, postal_address(), days, amount)
    # Today's mail is marked as sent before it goes: whatever fails after
    # this, the account is not mailed a second time today.
    await store.notify_mailed(root.id, now)
    try:
        await get_mailer().send_notice(email, subject, text, headers={"List-Unsubscribe": f"<{link}>"})
    except Exception as e:  # noqa: BLE001 — a mail that fails must not end the round
        logger.error("open items for %s not mailed: %s", root.id, e if isinstance(e, MailNotSent) else type(e).__name__)
        # It did not go: put the mark back, so the next round tries again.
        await store.notify_mailed(root.id, last)
        return False
    # The merchants the mail named are told, through the results it read;
    # the others' news waits for the next mail.
    for n in listed:
        try:
            await store.set_mail_told(n.tenant.id, {"at": now.isoformat(), "through": n.through})
        except KeyError:
            continue  # deleted since the look
    return True


def unsubscribe_link(token: str) -> str:
    """The dashboard page that turns the mail off, with a button: a link
    that did it on a GET would be pressed by every mail scanner."""
    return f"{dashboard_url()}/unsubscribe?token={token}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


_CURRENCY = re.compile(r"[A-Z]{3}")
_LINK = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://|\bwww\.")
#: A word straight before a colon with something after it: how "tel:",
#: "mailto:" or "sms:" make the rest of a name an address to press.
_SCHEME = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{0,31}):(?=\S)")
#: A full stop, in any of the forms a mail client reads as one, with a
#: letter or digit straight after it: what makes "shop.example.com" a link.
_DOT = re.compile(r"[.．。｡․﹒︒](?=\w)")


def shown(name: str, limit: int = NAME_SHOWN, *, own: bool = False) -> str:
    """A tenant's name as a mail shows it: one line, bounded, with no
    control or invisible character (the rule a note is held to,
    okwan_api.exceptions), no web address scheme or "word:" one, and a
    space after any full stop that would join two words into an address a
    mail client makes a link ("shop. example. com"). Whoever can name a
    merchant chooses these words in the account's mail and nothing else:
    not a line, not a header, not a link Okwan appears to give. `own` is
    the account's own name, set at sign-up or by the operator: its full
    stops and colons are left alone."""
    text = "".join(" " if unicodedata.category(c)[0] in "CZ" else "" if _unseen(c) else c for c in str(name))
    text = _LINK.sub(" ", text)
    if not own:
        text = _SCHEME.sub(r"\1: ", text)
        text = _DOT.sub(". ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text if any(unicodedata.category(c)[0] in "LNPS" for c in text) else "(no name)"


def total(items: list[dict[str, Any]]) -> str:
    """What items put at stake, by currency (Tally.total)."""
    t = Tally()
    for i in items:
        t.add(i)
    return t.total()


def _counted(items: list[dict[str, Any]]) -> str:
    t = Tally()
    for i in items:
        t.add(i)
    return t.counted()


#: What each kind of finding is, in words that name no reference.
WHAT = {
    "collected_twice": "collected twice",
    "collected_inconsistent": "payments don't add up",
    "uncollected": "no payment found",
    "matched_discrepant": "amounts differ",
    "unmatched_left": "no match found",
    "unmatched_right": "no match found",
    "ambiguous": "more than one match fits",
}


def _kinds(items: list[dict[str, Any]]) -> str:
    """"2 collected twice, 1 no payment found", in a fixed order."""
    count: dict[str, int] = {}
    for i in items:
        said = WHAT.get(i.get("finding"), "needs a look")
        count[said] = count.get(said, 0) + 1
    return _said(count)


def _said(count: dict[str, int]) -> str:
    return ", ".join(f"{n} {said}" for said, n in sorted(count.items(), key=lambda c: (-c[1], c[0])))


def lines(n: News, days: int) -> list[str]:
    """One merchant's part of the mail. A check is named by its title,
    which is Okwan's own words, as is what each finding is."""
    out: list[str] = []
    if n.new.n:
        out.append(f"New: {n.new.counted()}")
        out += [f"  {title}: {_said(count)}" for title, count in sorted(n.new_by.items())]
    if n.aged.n:
        out.append(f"Now open {days} days or more: {n.aged.counted()}")
    if n.beyond:
        out.append(f"And {n.beyond:,} more found than a stored result keeps: Open items counts them")
    return out


def message(account: str, news: list[News], listed: list[News], big: list[News], unsubscribe: str,
            address: str, days: int = AGING_DAYS, amount: int = LARGE_AMOUNT) -> tuple[str, str]:
    """The subject and plain text of the account's mail: every merchant's
    news counted in the subject, those `listed` named in the body, and the
    merchants with open items at or above the account's amount after them.
    The subject is Okwan's words and figures only; a name appears in the
    body, cleaned (`shown`)."""
    new = sum(n.new.n for n in news)
    aged = sum(n.aged.n for n in news)
    said = [x for x in (_plural(new, "new open item", "new open items") if new else "",
                        f"{aged} now open {days} days or more" if aged else "") if x]
    subject = "Okwan: " + " and ".join(said) + (f" across {len(news)} merchants" if len(news) > 1 else "")
    base = dashboard_url()
    account = shown(account, own=True)
    parts = [f"What is new on the open items for {account}.", ""]
    for n in listed:
        parts.append(shown(n.tenant.name))
        parts += [f"  {line}" for line in lines(n, days)]
        parts += [f"  {base}/exceptions?merchant={quote(n.tenant.id, safe='')}", ""]
    more = len(news) - len(listed)
    if more:
        said_more = _plural(more, "more merchant has", "more merchants have")
        parts += [f"{said_more} something new, in the next email and on Open items: {base}/exceptions", ""]
    if big:
        parts.append(f"Still open at {amount:,} or more of their currency:")
        parts += [f"  {shown(n.tenant.name)}: {n.large.counted()}" for n in big[:MAIL_MERCHANTS]]
        if len(big) > MAIL_MERCHANTS:
            parts.append(f"  and {_plural(len(big) - MAIL_MERCHANTS, 'more merchant', 'more merchants')}")
        parts.append("")
    parts += [
        "Okwan only reads. What is done about an item is done by a person, where it started.",
        "",
        "--",
        f"You get this because emails about open items are on for {account}.",
        f"Turn these emails off: {unsubscribe}",
        f"Change what is sent: {base}/settings#daily-checks",
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
