"""The daily check, and the mail when it finds something new.

Okwan now reads a merchant's rails without being asked and writes to the
account's inbox, so what must hold is mostly about restraint:

- a merchant is checked once a day, only when every rail is connected,
  whatever else ran a check for it;
- a daily check is a check: stored, metered to the merchant, and logged as
  Okwan's own read; none runs past the plan, with daily checks off, or
  when the operator has paused them;
- the mail says only what the account has not been told, once, in one
  message a day an account; it names merchants, counts and totals, never
  an order, a customer or a stored secret;
- no mail leaves without the sender's postal address and a link that
  turns the mail off; that link needs no sign-in, can do nothing else, and
  one account's cannot touch another's.

The rails are fixture rows (the sample week); the matching and the fold
are real. What an attacker's pass found in the first version, turned
around, is tests/test_daily_check_attacks.py.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import okwan_api.main  # noqa: F401  (registers the connectors and declarations)
import pytest
from okwan_api import schedule
from okwan_api.mail import set_mailer
from okwan_core import UpstreamError
from okwan_core.currency import format_minor
from okwan_recon import across, sample
from okwan_recon.coverage import Coverage
from okwan_vault import (
    ADMIN,
    READ,
    EnvMasterKey,
    MemoryStore,
    PostgresStore,
    accounts,
    new_key,
)
from okwan_vault.accounts import SESSION_PREFIX, new_token
from okwan_vault.models import DailyCheck, unsubscribe_tenant

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
ADDRESS = "Global Tech Startup LLC, 1 Example Street, Springfield, IL 62701"
DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
SECRETS = {"stripe": "sk_live_NEVER_IN_MAIL_0123456789", "shopify": "shpat_NEVER_IN_MAIL_abcdef",
           "paypal": "paypal-secret-NEVER-IN-MAIL"}


class Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.failing = False

    async def send_verification(self, email: str, link: str) -> None:  # pragma: no cover
        raise AssertionError("not a verification")

    async def send_notice(self, to, subject, text, html=None, reply_to=None, headers=None) -> None:
        if self.failing:
            from okwan_api.mail import MailNotSent

            raise MailNotSent("resend answered 500 unknown")
        self.sent.append({"to": to, "subject": subject, "text": text, "headers": headers or {}})


def _week(*, without: tuple[str, ...] = ()) -> dict[str, list[dict]]:
    """The sample week, less some orders (and their payments)."""
    rows = sample.rows(NOW)
    return {
        "shopify": [o for o in rows["shopify"] if o["name"] not in without],
        "paypal": [p for p in rows["paypal"] if p["invoice_id"] not in without],
        "stripe": [c for c in rows["stripe"] if c["order_ref"] not in without],
    }


@pytest.fixture
async def world(monkeypatch):
    """An account (Acme ISV, with a login) and what each merchant's rails
    hold, keyed by its Shopify domain."""
    from okwan_api.auth import set_store

    monkeypatch.setenv("OKWAN_MAIL_POSTAL_ADDRESS", ADDRESS)
    monkeypatch.setenv("OKWAN_DASHBOARD_URL", "https://app.okwan.test")
    monkeypatch.delenv("OKWAN_DAILY_CHECKS", raising=False)
    store = MemoryStore(EnvMasterKey(new_key()))
    set_store(store)
    outbox = Outbox()
    set_mailer(outbox)
    rails: dict[str, object] = {}

    async def fetch(ref, resolver, cap, overrides):
        held = rails[resolver("shopify", ("shop_domain",))["shop_domain"]]
        if isinstance(held, Exception):
            raise held
        rows = held[ref.connector]
        return rows, Coverage(source=ref.qualified, records=len(rows), cap=cap, truncated=False)

    monkeypatch.setattr(across, "fetch_side", fetch)
    root = await store.create_tenant("Acme ISV")
    await store.add_account(root.id, "owner@acme.test", accounts.hash_password("correct horse battery"))

    async def merchant(name: str, rows=None, *, only: tuple[str, ...] = ("shopify", "paypal", "stripe"), parent=None):
        t = await store.create_tenant(name, parent_id=(parent or root).id)
        domain = f"{t.id}.myshopify.com"
        fields = {"shopify": {"access_token": SECRETS["shopify"], "shop_domain": domain},
                  "paypal": {"client_id": "paypal-client", "client_secret": SECRETS["paypal"]},
                  "stripe": {"secret_key": SECRETS["stripe"]}}
        for connector in only:
            for field, value in fields[connector].items():
                await store.put_credential(t.id, connector, field, value)
        rails[domain] = rows if rows is not None else _week()
        return t

    class World:
        pass

    w = World()
    w.store, w.outbox, w.rails, w.root, w.merchant = store, outbox, rails, root, merchant
    w.set_rows = lambda t, rows: rails.__setitem__(f"{t.id}.myshopify.com", rows)
    yield w
    set_mailer(None)


# ── when a check runs ──────────────────────────────────────────────────

def test_the_daily_check_needs_exactly_the_rails_the_fold_reads():
    from okwan_recon.registry import get_across

    need = schedule.needs(get_across(schedule.FOLD))
    assert set(need) == {"shopify", "paypal", "stripe"}
    assert schedule.ready(need, {k: list(v) for k, v in need.items()})
    assert not schedule.ready(need, {"shopify": list(need["shopify"]), "stripe": list(need["stripe"])})
    assert not schedule.ready(need, {**{k: list(v) for k, v in need.items()}, "shopify": ["shop_domain"]})


async def test_a_connected_merchant_is_checked_once_a_day(world):
    kofi = await world.merchant("Kofi's Store")
    first = await schedule.run_due(NOW)
    assert (first.checked, first.failed) == (1, 0)
    [run] = await world.store.list_runs(kofi.id)
    assert (run.surface, run.status, run.kind, run.name) == ("schedule", "ok", "across", "rails")
    assert (await schedule.run_due(NOW + timedelta(hours=1))).checked == 0, "not due again within the day"
    assert (await schedule.run_due(NOW + timedelta(hours=24, minutes=1))).checked == 1
    assert len(await world.store.list_runs(kofi.id)) == 2


async def test_a_check_someone_else_ran_does_not_put_the_daily_check_off(world):
    """Only the scheduler's own record says when a merchant is due: a run
    from the dashboard, a key or an agent leaves the daily check where it was."""
    kofi = await world.merchant("Kofi's Store")
    at = NOW - timedelta(hours=3)
    await world.store.add_run(kofi.id, kind="across", name="rails", surface="dashboard", status="ok",
                              started_at=at, finished_at=at, summary={}, rows=[], error=None)
    assert (await schedule.run_due(NOW)).checked == 1
    assert {r.surface for r in await world.store.list_runs(kofi.id)} == {"dashboard", "schedule"}


async def test_a_merchant_missing_a_rail_is_left_alone(world):
    accra = await world.merchant("Accra Coffee Co.", only=("stripe",))
    r = await schedule.run_due(NOW)
    assert (r.checked, r.failed) == (0, 0)
    assert await world.store.list_runs(accra.id) == []


async def test_every_merchant_in_the_tree_is_reached(world):
    region = await world.merchant("West region", only=())
    deep = await world.merchant("Deep store", parent=region)
    assert (await schedule.run_due(NOW)).checked == 1
    assert len(await world.store.list_runs(deep.id)) == 1


async def test_a_daily_check_is_metered_and_logged_as_okwans_own_read(world):
    kofi = await world.merchant("Kofi's Store")
    await schedule.run_due(NOW)
    since = datetime(2000, 1, 1, tzinfo=UTC)
    assert await world.store.usage_since(world.root.id, since) == 1
    [row] = await world.store.access_entries(world.root.id, since=since)
    assert (row["tenant_id"], row["credential"], row["surface"], row["target"]) == (
        kofi.id, "schedule", "schedule:across", "daily check")
    assert row["ip"] == "unknown", "Okwan's own read comes from no address"


async def test_no_daily_check_runs_past_the_plan(world, monkeypatch):
    from okwan_vault.usage import Quota

    kofi = await world.merchant("Kofi's Store")

    async def spent(tenant):
        return Quota(plan="free", limit=5000, used=5000)

    monkeypatch.setattr(schedule, "quota_for", spent)
    r = await schedule.run_due(NOW)
    assert (r.checked, r.out_of_plan) == (0, 1)
    assert await world.store.list_runs(kofi.id) == [] and world.outbox.sent == []


async def test_an_account_can_turn_daily_checks_off(world):
    kofi = await world.merchant("Kofi's Store")
    await world.store.set_notify(world.root.id, daily_checks=False)
    assert (await schedule.run_due(NOW)).checked == 0
    assert await world.store.list_runs(kofi.id) == []
    await world.store.set_notify(world.root.id, daily_checks=True)
    assert (await schedule.run_due(NOW)).checked == 1


async def test_the_operator_can_pause_every_daily_check(world, monkeypatch):
    await world.merchant("Kofi's Store")
    rounds = 0

    async def counted(now=None):
        nonlocal rounds
        rounds += 1
        return schedule.Round()

    monkeypatch.setattr(schedule, "run_due", counted)
    for value, expected in (("off", 0), ("on", 1)):
        rounds = 0
        monkeypatch.setenv("OKWAN_DAILY_CHECKS", value)
        task = asyncio.create_task(schedule.sweeper(interval=0.01, first_after=0))
        await asyncio.sleep(0.05)
        task.cancel()
        assert (rounds > 0) == bool(expected), value


async def test_a_failed_check_is_stored_and_the_round_goes_on(world):
    broken = await world.merchant("Broken Store", rows=UpstreamError(
        status=401, body=f"Invalid API key provided: {SECRETS['stripe']}"))
    fine = await world.merchant("Fine Store")
    r = await schedule.run_due(NOW)
    assert (r.checked, r.failed) == (1, 1)
    [failed] = await world.store.list_runs(broken.id)
    assert (failed.status, failed.surface) == ("failed", "schedule")
    assert SECRETS["stripe"] not in (failed.error or "") and "[redacted]" in failed.error
    assert [x.status for x in await world.store.list_runs(fine.id)] == ["ok"]
    # A failure is not metered, and is tried again only a day later.
    assert await world.store.usage_since(world.root.id, datetime(2000, 1, 1, tzinfo=UTC)) == 1
    assert (await schedule.run_due(NOW + timedelta(hours=2))).failed == 0


async def test_a_check_that_hangs_is_stopped(world, monkeypatch):
    slow = await world.merchant("Slow Store")

    async def hang(ref, resolver, cap, overrides):
        await asyncio.sleep(5)

    monkeypatch.setattr(across, "fetch_side", hang)
    monkeypatch.setattr(schedule, "RUN_TIMEOUT", 0.05)
    r = await schedule.run_due(NOW)
    assert (r.checked, r.failed) == (0, 1)
    [run] = await world.store.list_runs(slow.id)
    assert run.status == "failed" and "stopped" in run.error


# ── the mail ───────────────────────────────────────────────────────────

async def test_the_first_check_mails_the_account_what_it_found(world):
    kofi = await world.merchant("Kofi's Store")
    r = await schedule.run_due(NOW)
    assert r.mailed == 1
    [mail] = world.outbox.sent
    assert mail["to"] == "owner@acme.test"
    assert mail["subject"] == "Okwan: 3 new findings"
    text = mail["text"]
    assert text.startswith("Okwan's daily check found something new for Acme ISV.\n\nKofi's Store\n")
    assert "2 orders collected twice: $501.99 owed back" in text
    assert "1 order with no payment found: $97.40 unpaid" in text
    assert f"https://app.okwan.test/merchants/{kofi.id}?tab=findings" in text
    assert f"Okwan · {ADDRESS}" in text and "daily checks are on for Acme ISV" in text
    link = re.search(r"Turn these emails off: (\S+)", text).group(1)
    assert link.startswith("https://app.okwan.test/unsubscribe?token=unsub_")
    assert mail["headers"] == {"List-Unsubscribe": f"<{link}>"}


async def test_the_mail_names_no_order_customer_or_secret(world):
    await world.merchant("Kofi's Store")
    await schedule.run_due(NOW)
    text = world.outbox.sent[0]["text"] + world.outbox.sent[0]["subject"]
    for order in ("#2043", "#2046", "#2047", "SAMPLE-PP", "ch_sample"):
        assert order not in text, f"the mail names {order}"
    for secret in SECRETS.values():
        assert secret not in text


async def test_the_same_findings_are_not_mailed_twice(world):
    await world.merchant("Kofi's Store")
    await schedule.run_due(NOW)
    again = await schedule.run_due(NOW + timedelta(hours=25))
    assert (again.checked, again.mailed) == (1, 0)
    assert len(world.outbox.sent) == 1


async def test_only_what_is_new_since_the_last_check_is_mailed(world):
    kofi = await world.merchant("Kofi's Store", rows=_week(without=("#2047",)))
    await schedule.run_due(NOW)
    world.set_rows(kofi, _week())
    await schedule.run_due(NOW + timedelta(hours=25))
    second = world.outbox.sent[1]
    assert second["subject"] == "Okwan: 1 new finding"
    assert "1 order collected twice: $189.99 owed back" in second["text"]
    assert "no payment found" not in second["text"], "the unpaid order was already reported"


async def test_a_finding_that_went_away_and_came_back_is_news_again(world):
    kofi = await world.merchant("Kofi's Store")
    await schedule.run_due(NOW)
    world.set_rows(kofi, _week(without=("#2047",)))
    assert (await schedule.run_due(NOW + timedelta(days=1, minutes=1))).mailed == 0
    world.set_rows(kofi, _week())
    assert (await schedule.run_due(NOW + timedelta(days=2, minutes=2))).mailed == 1
    assert world.outbox.sent[1]["subject"] == "Okwan: 1 new finding"


async def test_an_account_is_mailed_once_a_day_and_a_later_finding_waits_for_tomorrow(world):
    """Two merchants checked at different hours: the second one's findings
    are not a second mail that day, and are not lost. They are in the next
    day's mail, read from the run that found them."""
    await world.merchant("Kofi's Store")
    assert (await schedule.run_due(NOW)).mailed == 1
    accra = await world.merchant("Accra Coffee Co.")           # connected five hours later
    later = await schedule.run_due(NOW + timedelta(hours=5))
    assert (later.checked, later.mailed, len(later.found)) == (1, 0, 1)
    assert len(world.outbox.sent) == 1, "one mail a day at most"
    for hour in range(6, 24):
        assert (await schedule.run_due(NOW + timedelta(hours=hour))).mailed == 0
    tomorrow = await schedule.run_due(NOW + timedelta(hours=24, minutes=1))
    assert tomorrow.mailed == 1
    second = world.outbox.sent[1]
    assert second["subject"] == "Okwan: 3 new findings"
    assert "Accra Coffee Co." in second["text"] and "Kofi's Store" not in second["text"]
    assert len(await world.store.list_runs(accra.id)) == 1, "mailed from the stored run, not from a second check"
    # And once told, never again.
    for hour in range(25, 80):
        await schedule.run_due(NOW + timedelta(hours=hour))
    assert len(world.outbox.sent) == 2


async def test_findings_wait_for_a_mail_that_could_not_go(world, monkeypatch):
    """With no postal address nothing is mailed, and nothing is marked as
    told: the day the address is set, the account hears of what is there."""
    kofi = await world.merchant("Kofi's Store")
    monkeypatch.delenv("OKWAN_MAIL_POSTAL_ADDRESS")
    assert (await schedule.run_due(NOW)).mailed == 0
    monkeypatch.setenv("OKWAN_MAIL_POSTAL_ADDRESS", ADDRESS)
    r = await schedule.run_due(NOW + timedelta(hours=1))
    assert (r.checked, r.mailed) == (0, 1)
    assert world.outbox.sent[0]["subject"] == "Okwan: 3 new findings"
    assert len(await world.store.list_runs(kofi.id)) == 1


async def test_a_mail_that_failed_is_tried_again_next_round(world):
    await world.merchant("Kofi's Store")
    world.outbox.failing = True
    assert (await schedule.run_due(NOW)).mailed == 0
    world.outbox.failing = False
    assert (await schedule.run_due(NOW + timedelta(hours=1))).mailed == 1
    assert (await schedule.run_due(NOW + timedelta(hours=2))).mailed == 0


async def test_the_scheduler_keeps_fingerprints_and_no_order_reference(world):
    kofi = await world.merchant("Kofi's Store")
    await schedule.run_due(NOW)
    state = (await world.store.daily_checks([kofi.id]))[kofi.id]
    assert state.checked_at == NOW and state.news_run is None and len(state.told) == 3
    for mark in state.told:
        assert re.fullmatch(r"[0-9a-f]{20}", mark), "a hash, not a reference"
    assert not any(ref in "".join(state.told) for ref in ("2043", "2046", "2047"))


async def test_an_account_gets_one_mail_for_all_its_merchants(world):
    await world.merchant("Kofi's Store")
    await world.merchant("Accra Coffee Co.", rows=_week(without=("#2043", "#2046")))
    await world.merchant("Quiet Store", rows=_week(without=("#2043", "#2046", "#2047")))
    r = await schedule.run_due(NOW)
    assert (r.checked, r.mailed) == (3, 1)
    [mail] = world.outbox.sent
    assert mail["subject"] == "Okwan: 4 new findings across 2 merchants"
    assert "Kofi's Store" in mail["text"] and "Accra Coffee Co." in mail["text"]
    assert "Quiet Store" not in mail["text"], "a merchant with nothing new is not named"


async def test_another_account_never_appears_in_the_mail(world):
    other = await world.store.create_tenant("Other ISV")
    await world.store.add_account(other.id, "owner@other.test", accounts.hash_password("correct horse battery"))
    await world.merchant("Kofi's Store")
    await world.merchant("Their Store", parent=other)
    r = await schedule.run_due(NOW)
    assert (r.checked, r.mailed) == (2, 2)
    by_to = {m["to"]: m for m in world.outbox.sent}
    assert "Their Store" not in by_to["owner@acme.test"]["text"] and "Acme ISV" not in by_to["owner@other.test"]["text"]
    assert "Kofi's Store" not in by_to["owner@other.test"]["text"]
    tokens = {to: re.search(r"token=(\S+)", m["text"]).group(1) for to, m in by_to.items()}
    assert unsubscribe_tenant(tokens["owner@acme.test"]) == world.root.id
    assert unsubscribe_tenant(tokens["owner@other.test"]) == other.id


@pytest.mark.parametrize("how", ["no address", "no mailer", "emails off", "send fails"])
async def test_no_mail_goes_but_the_check_still_runs(world, monkeypatch, how):
    kofi = await world.merchant("Kofi's Store")
    if how == "no address":
        monkeypatch.delenv("OKWAN_MAIL_POSTAL_ADDRESS")
    elif how == "no mailer":
        set_mailer(None)
    elif how == "emails off":
        await world.store.set_notify(world.root.id, finding_emails=False)
    else:
        world.outbox.failing = True
    r = await schedule.run_due(NOW)
    assert (r.checked, r.mailed) == (1, 0) and world.outbox.sent == []
    assert [x.status for x in await world.store.list_runs(kofi.id)] == ["ok"]
    assert len(r.found) == 1, "what is new is still known to the round"


def test_the_mail_never_states_a_total_it_cannot_stand_behind():
    twice = {"outcome": "collected_twice", "order": {"ref": "#1", "currency": "USD"},
             "order_total_minor": 1000, "collected_minor": 2000}
    euro = {**twice, "order": {"ref": "#2", "currency": "EUR"}, "order_total_minor": 500, "collected_minor": 1000}
    unknown = {**twice, "order": {"ref": "#3", "currency": "USD"}, "collected_minor": None}
    assert schedule.lines([twice]) == ["1 order collected twice: $10.00 owed back"]
    assert schedule.lines([twice, euro]) == ["2 orders collected twice: 5.00 EUR and $10.00 owed back"]
    assert schedule.lines([twice, unknown]) == ["2 orders collected twice"], "an amount is missing, so no total"
    odd = {"outcome": "collected_inconsistent", "order": {"ref": "#4", "currency": "USD"}}
    assert schedule.lines([odd]) == ["1 order where the payments don't add up to the order"]


def test_money_is_written_from_minor_units_without_a_float():
    assert format_minor(144900, "USD") == "$1,449.00"
    assert format_minor(-9740, "usd") == "-$97.40"
    assert format_minor(144900, "EUR") == "1,449.00 EUR"
    assert format_minor(1500, "JPY") == "1,500 JPY"
    assert format_minor(900719925474099301, "USD") == "$9,007,199,254,740,993.01", "past a float's exact range"


# ── the way out of the mail ────────────────────────────────────────────

@pytest.fixture
async def api(world):
    from okwan_api.main import app

    session, token_hash = new_token(SESSION_PREFIX)
    await world.store.create_session(world.root.id, token_hash, datetime.now(UTC) + timedelta(hours=1))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        yield c, {"Authorization": f"Bearer {session}"}


async def _token(world) -> str:
    await world.merchant("Kofi's Store")
    await schedule.run_due(NOW)
    return re.search(r"token=(\S+)", world.outbox.sent[0]["text"]).group(1)


async def test_the_link_turns_the_mail_off_without_signing_in_and_nothing_else(world, api):
    client, _ = api
    token = await _token(world)
    r = await client.post("/v1/unsubscribe", json={"token": token})
    assert r.status_code == 204
    settings = await world.store.get_notify(world.root.id)
    assert (settings["finding_emails"], settings["daily_checks"]) == (False, True)
    # The checks go on; the mail does not.
    world.set_rows((await world.store.children_of(world.root.id))[0], _week(without=("#2043",)))
    later = await schedule.run_due(NOW + timedelta(hours=25))
    assert (later.checked, later.mailed) == (1, 0) and len(world.outbox.sent) == 1
    assert (await client.post("/v1/unsubscribe", json={"token": token})).status_code == 204, "pressing it twice is fine"


async def test_a_link_that_was_never_issued_does_nothing(world, api):
    client, _ = api
    token = await _token(world)
    tenant, _, secret = token.partition(".")
    other = await world.store.create_tenant("Other ISV")
    forged = [
        f"{tenant}.{'A' * 32}",                                   # the right account, a guessed secret
        f"unsub_{other.id}.{secret}",                             # this secret, another account
        f"unsub_ten_{'0' * 16}.{secret}",                         # an account that does not exist
        f"unsub_{world.root.id},{other.id}.{secret}",             # two accounts in one scope
        token + "x", token[:-1], "unsub_", "x" * 60,
    ]
    answers = set()
    for t in forged:
        r = await client.post("/v1/unsubscribe", json={"token": t})
        assert r.status_code == 404, t
        answers.add(r.text)
    assert len(answers) == 1, "every refusal reads the same"
    assert (await world.store.get_notify(world.root.id))["finding_emails"] is True
    assert (await world.store.get_notify(other.id))["finding_emails"] is True


async def test_the_link_keeps_working_for_every_mail_ever_sent(world):
    first = await world.store.notify_token(world.root.id)
    await world.store.set_notify(world.root.id, finding_emails=False)
    await world.store.set_notify(world.root.id, finding_emails=True)
    assert await world.store.notify_token(world.root.id) == first


async def test_guessing_at_links_is_slowed(world, api):
    from okwan_api.ratelimit import UNSUBSCRIBE_IP

    client, _ = api
    codes = [(await client.post("/v1/unsubscribe", json={"token": "x" * 60})).status_code
             for _ in range(UNSUBSCRIBE_IP.limit + 1)]
    assert set(codes[:-1]) == {404} and codes[-1] == 429


# ── the settings ───────────────────────────────────────────────────────

async def test_an_account_reads_and_sets_its_daily_check(world, api):
    client, auth = api
    path = f"/v1/tenants/{world.root.id}/notify"
    got = (await client.get(path, headers=auth)).json()
    assert (got["daily_checks"], got["finding_emails"], got["email"]) == (True, True, "owner@acme.test")
    assert got["emails_blocked"] is None and set(got["needs"]) == {"shopify", "paypal", "stripe"}
    assert "unsubscribe_token" not in got and "token" not in str(got)
    off = (await client.put(path, headers=auth, json={"finding_emails": False})).json()
    assert (off["daily_checks"], off["finding_emails"]) == (True, False)
    both = (await client.put(path, headers=auth, json={"daily_checks": False, "finding_emails": True})).json()
    assert (both["daily_checks"], both["finding_emails"]) == (False, True)
    assert (await client.put(path, headers=auth, json={"daily_checks": "maybe"})).status_code == 422


async def test_the_settings_say_when_this_server_cannot_mail(world, api, monkeypatch):
    client, auth = api
    monkeypatch.delenv("OKWAN_MAIL_POSTAL_ADDRESS")
    got = (await client.get(f"/v1/tenants/{world.root.id}/notify", headers=auth)).json()
    assert got["emails_blocked"] == "OKWAN_MAIL_POSTAL_ADDRESS is not set"
    monkeypatch.setenv("OKWAN_DAILY_CHECKS", "off")
    assert (await client.get(f"/v1/tenants/{world.root.id}/notify", headers=auth)).json()["paused_by_operator"] is True


async def test_the_settings_belong_to_the_account_and_to_no_one_else(world, api):
    client, auth = api
    kofi = await world.merchant("Kofi's Store")
    assert (await client.get(f"/v1/tenants/{kofi.id}/notify", headers=auth)).status_code == 400
    other = await world.store.create_tenant("Other ISV")
    for method, body in (("GET", None), ("PUT", {"daily_checks": False})):
        r = await client.request(method, f"/v1/tenants/{other.id}/notify", headers=auth, json=body)
        assert r.status_code == 404, "another account reads like one that does not exist"
    assert (await world.store.get_notify(other.id))["daily_checks"] is True
    read_key, _ = await world.store.issue_key(world.root.id, scope=READ)
    admin_key, _ = await world.store.issue_key(world.root.id, scope=ADMIN)
    path = f"/v1/tenants/{world.root.id}/notify"
    assert (await client.put(path, headers={"Authorization": f"Bearer {read_key}"}, json={"daily_checks": False})).status_code == 403
    assert (await client.get(path, headers={"Authorization": f"Bearer {admin_key}"})).status_code == 200
    assert (await client.get(path)).status_code == 401


# ── Postgres keeps the same promises ───────────────────────────────────

@pytest.mark.skipif(not DSN, reason="no vault database configured")
async def test_postgres_keeps_the_choices_and_one_token_per_account():
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    try:
        a = await store.create_tenant(f"Notify A {uuid.uuid4().hex[:6]}")
        b = await store.create_tenant(f"Notify B {uuid.uuid4().hex[:6]}")
        try:
            fresh = {"daily_checks": True, "finding_emails": True, "unsubscribe_token": None, "mailed_at": None}
            assert await store.get_notify(a.id) == fresh
            token = await store.notify_token(a.id)
            assert unsubscribe_tenant(token) == a.id and await store.notify_token(a.id) == token
            off = await store.set_notify(a.id, finding_emails=False)
            assert (off["daily_checks"], off["finding_emails"], off["unsubscribe_token"]) == (True, False, token)
            assert (await store.set_notify(a.id, daily_checks=False))["finding_emails"] is False
            await store.notify_mailed(a.id, NOW)
            kept = await store.get_notify(a.id)
            assert (kept["mailed_at"], kept["finding_emails"], kept["unsubscribe_token"]) == (NOW, False, token)
            assert await store.get_notify(b.id) == fresh
            with pytest.raises(KeyError):
                await store.set_notify(f"ten_{'0' * 16}", daily_checks=False)
            await store.delete_tenant(a.id)
            assert await store.pool.fetchval("SELECT count(*) FROM notify_settings WHERE tenant_id = $1", a.id) == 0
        finally:
            for t in (a, b):
                await store.pool.execute("DELETE FROM tenants WHERE id = $1", t.id)
    finally:
        await store.close()


@pytest.mark.skipif(not DSN, reason="no vault database configured")
async def test_postgres_keeps_the_schedulers_record_one_row_a_merchant():
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    try:
        a = await store.create_tenant(f"Daily A {uuid.uuid4().hex[:6]}")
        b = await store.create_tenant(f"Daily B {uuid.uuid4().hex[:6]}")
        try:
            assert await store.daily_checks([a.id, b.id]) == {} and await store.daily_checks([]) == {}
            await store.claim_daily_check(a.id, NOW)
            assert (await store.daily_checks([a.id, b.id]))[a.id] == DailyCheck(tenant_id=a.id, checked_at=NOW)
            await store.settle_daily_check(a.id, told=["aa", "bb"], news_run="run_x")
            later = NOW + timedelta(days=1)
            await store.claim_daily_check(a.id, later)          # the next day's slot keeps what was told
            assert (await store.daily_checks([a.id]))[a.id] == DailyCheck(a.id, later, ("aa", "bb"), "run_x")
            await store.settle_daily_check(a.id, told=[], news_run=None)
            assert (await store.daily_checks([a.id]))[a.id] == DailyCheck(a.id, later)
            await store.settle_daily_check(b.id, told=["zz"], news_run=None)    # no row: nothing is made
            assert list(await store.daily_checks([a.id, b.id])) == [a.id]
            with pytest.raises(KeyError):
                await store.claim_daily_check(f"ten_{'0' * 16}", NOW)
            await store.delete_tenant(a.id)
            assert await store.pool.fetchval("SELECT count(*) FROM daily_checks WHERE tenant_id = $1", a.id) == 0
        finally:
            for t in (a, b):
                await store.pool.execute("DELETE FROM tenants WHERE id = $1", t.id)
    finally:
        await store.close()
