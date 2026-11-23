"""Webhooks: Okwan tells a platform when one of its open items changes.

A platform that embeds Okwan registers an address (`POST /v1/webhooks`),
and Okwan POSTs an event to it each time an item of the account or one of
its merchants changes:

* `open_item.opened`: a run of its check found it, and the run before did not;
* `open_item.aged`: a run found it still open on the day it had been open
  the account's number of days (Settings › Daily checks, as the mail);
* `open_item.cleared`: the check's newest run no longer finds it (a deposit
  arrived, a refund landed): said only when neither result was cut at its
  row limit, since an item left out of a cut result is not gone;
* `open_item.closed`: a person explained or resolved it;
* `open_item.reopened`: a person opened it again, or a run found it on
  other terms than the decision that closed it.

An event's `data` is the open item as `OpenItem` defines it, the same
definition `GET /v1/exceptions`, the SQL table `exceptions.open_items` and
the MCP tool `okwan_open_items` answer with: one declaration, every surface.

Each event is signed as Standard Webhooks signs (`webhook-id`,
`webhook-timestamp`, `webhook-signature: v1,<base64 HMAC-SHA256>` over
`id.timestamp.body`), with the address's own secret, shown once when the
address is added. A delivery that does not get a 2xx is tried again, at
growing intervals, for three days; an address whose every delivery has
failed for three days is turned off and the account is mailed. The events
are kept 30 days (`GET /v1/events`), so a platform can read what it missed.

Okwan connects only to a public address on port 443, over HTTPS, checked
as it connects: the name is resolved, every address it resolves to must be
public, and the connection goes to the address that was checked, with the
name kept for TLS (okwan_core.egress). No redirect is followed and no
answer's body is read: what is kept of an answer is its status code.

An address is told what changes after it is added; what is already open
is read with `GET /v1/exceptions`. An event can come twice (at least once):
a platform tells them apart by the item (`merchant_id`, `kind`,
`check_name`, `item_key`) and the event's `type`.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import random
import secrets
import ssl
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from okwan_core.egress import EgressRefused, resolve_public
from okwan_recon.exceptions import (
    OpenItem,
    aged,
    holds,
    items_by_key,
    open_items,
    run_found,
)
from okwan_vault import WEBHOOK_ENDPOINTS_MAX, EndpointsFull, WebhookEndpoint
from okwan_vault.authz import ancestors
from okwan_vault.models import AGING_DAYS, RunRecord, Tenant
from pydantic import BaseModel, ConfigDict, Field

from . import access
from .auth import admin_actor, current_tenant, get_store
from .mail import get_mailer
from .ratelimit import EVENTS_TENANT, WEBHOOK_CHANGE, WEBHOOK_TEST, enforce

logger = logging.getLogger(__name__)

EVENT_TYPES = ("open_item.opened", "open_item.aged", "open_item.cleared", "open_item.closed",
               "open_item.reopened")
EventType = Literal["open_item.opened", "open_item.aged", "open_item.cleared", "open_item.closed",
                    "open_item.reopened"]
#: How long the events are kept for `GET /v1/events`.
EVENTS_KEPT = timedelta(days=30)
#: How long a delivery is tried, and how long an address may fail every
#: delivery before it is turned off.
GIVE_UP_AFTER = timedelta(days=3)
#: Seconds before each next attempt: 1 min, 5 min, 30 min, 2 h, 5 h, then
#: every 10 h until three days have passed. Each is moved by up to a tenth
#: either way, so addresses that failed together are not tried together.
RETRY_AFTER = (60, 300, 1800, 7200, 18000, 36000)
#: The only port Okwan sends to.
PORT = 443
URL_MAX = 2048
#: An attempt's limits: connecting, then the whole request.
CONNECT_SECONDS = 5.0
SEND_SECONDS = 10.0
#: The delivery round: how often; how many accounts, and deliveries an
#: account and an address, a round takes; how long what it took is held.
#: Each address's queue is sent on its own task, one delivery at a time:
#: SENDING_AT_ONCE addresses at once in all, ACCOUNT_AT_ONCE of one account,
#: and an address's turn ends after QUEUE_SECONDS, the rest of its queue
#: going back for a later round. When places are short, an account with
#: nothing being sent goes first: slow addresses keep their own accounts'
#: events waiting, and another account's for a turn at most.
ROUND_SECONDS = 5.0
ROUND_ACCOUNTS = 100
ROUND_DELIVERIES = 50
ADDRESS_DELIVERIES = 10
CLAIM_FOR = timedelta(minutes=5)
SENDING_AT_ONCE = 64
ACCOUNT_AT_ONCE = 2
QUEUE_SECONDS = 30.0
#: Listed a request at most.
EVENTS_LISTED = 100
USER_AGENT = "Okwan-Webhooks/1"


def now() -> datetime:
    return datetime.now(UTC)


# ── signing ────────────────────────────────────────────────────────────

def new_secret() -> str:
    """`whsec_` and 24 random bytes in base64, as Standard Webhooks writes a
    secret, so its libraries verify Okwan's signatures as they are."""
    return "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode()


def sign(secret: str, msg_id: str, timestamp: int, body: str) -> str:
    """`v1,` and the base64 HMAC-SHA256 of `id.timestamp.body` under the
    secret's bytes."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{timestamp}.{body}".encode(), hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def signed_headers(secret: str, msg_id: str, body: str, at: float | None = None) -> dict[str, str]:
    ts = int(at if at is not None else time.time())
    return {
        "content-type": "application/json",
        "user-agent": USER_AGENT,
        "webhook-id": msg_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": sign(secret, msg_id, ts, body),
    }


# ── addresses ──────────────────────────────────────────────────────────

class BadAddress(ValueError):
    """An address Okwan will not send to, and why, in words for the caller."""


def check_url(url: str) -> tuple[str, str]:
    """The address as Okwan keeps it, and its host; or BadAddress. HTTPS on
    port 443, a host name or a public IP, no user name or password, no
    fragment, nothing that cannot be seen."""
    if len(url) > URL_MAX:
        raise BadAddress(f"an address is at most {URL_MAX} characters")
    if any(c.isspace() or not c.isprintable() for c in url):
        raise BadAddress("an address has no spaces or characters that cannot be seen")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise BadAddress("this is not an address Okwan can read") from None
    if parts.scheme.lower() != "https":
        raise BadAddress("an address starts with https://: events are sent over HTTPS only")
    if parts.username is not None or parts.password is not None:
        raise BadAddress("an address carries no user name or password; the signature says the event is Okwan's")
    if port not in (None, PORT):
        raise BadAddress(f"Okwan sends events to port {PORT} only")
    if parts.fragment:
        raise BadAddress("an address has no #fragment")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise BadAddress("an address names a host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is None:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise BadAddress("the host name is not one Okwan can look up") from None
        netloc = host
    else:
        host = str(ip)
        netloc = f"[{host}]" if ip.version == 6 else host
    if PORT != 443:
        netloc = f"{netloc}:{PORT}"
    return urlunsplit(("https", netloc, parts.path or "/", parts.query, "")), host


_trust: ssl.SSLContext | None = None


def _tls() -> ssl.SSLContext:
    """How the address's certificate is checked: the trust httpx uses
    (certifi's authorities), made once, since loading it takes long enough
    to hold every request if it were made for each delivery. Tests put
    their own authority here."""
    global _trust
    if _trust is None:
        _trust = httpx.create_ssl_context()
    return _trust


async def send(url: str, body: str, headers: dict[str, str]) -> tuple[int | None, str | None]:
    """POST one event; its status code, and why it failed when it did.

    The host is resolved and every address it resolves to must be public;
    the connection goes to the address that was checked, with the host kept
    for TLS (SNI and the certificate), so a second DNS answer cannot point
    the request somewhere else. No proxy from the environment, no redirect,
    and the answer's body is never read."""
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".")
    try:
        ip = await resolve_public(host, PORT, exceptions=False)
    except EgressRefused:
        return None, "refused: the host does not resolve to a public address"
    dial = f"[{ip}]" if ":" in ip else ip
    target = urlunsplit(("https", f"{dial}:{PORT}", parts.path or "/", parts.query, ""))
    host_header = host if PORT == 443 else f"{host}:{PORT}"
    try:
        async with httpx.AsyncClient(
            verify=_tls(), trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(SEND_SECONDS, connect=CONNECT_SECONDS),
        ) as client:
            request = client.build_request(
                "POST", target, content=body.encode(), headers={**headers, "host": host_header},
                extensions={"sni_hostname": host})
            response = await asyncio.wait_for(client.send(request, stream=True), SEND_SECONDS + 2)
            status = response.status_code
            await response.aclose()
    except (TimeoutError, httpx.TimeoutException):
        return None, "timed out"
    except httpx.ConnectError as exc:
        cause = exc.__cause__ or exc.__context__
        return None, "TLS failed" if isinstance(cause, ssl.SSLError) or "SSL" in str(exc) else "could not connect"
    except httpx.HTTPError as exc:
        return None, type(exc).__name__
    return status, None if 200 <= status < 300 else f"answered {status}"


# ── events ─────────────────────────────────────────────────────────────

def _event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:24]}"


def envelope(event_id: str, type_: str, account: str, at: datetime, data: dict[str, Any]) -> str:
    """The body as signed and sent: Standard Webhooks' `type`, `timestamp`
    and `data`, with the event's `id` and the `account` it is sent to."""
    return json.dumps({"id": event_id, "type": type_, "timestamp": at.isoformat(), "account": account,
                       "data": data}, ensure_ascii=False, separators=(",", ":"))


async def receivers(store, merchant_id: str) -> tuple[list[str], list[str]]:
    """The accounts with an address that is on and may hear of this
    merchant: itself and those above it, as far as an account reaches
    (authz.MAX_DEPTH); and the chain itself, the merchant first."""
    chain = [merchant_id, *await ancestors(store, merchant_id)]
    on = {e.tenant_id for e in await store.webhook_endpoints(chain, enabled_only=True)}
    return [t for t in chain if t in on], chain


def changes(tenant_id: str, kind: str, name: str, previous: RunRecord | None, run: RunRecord,
            decisions: dict, days: int) -> tuple[list[tuple[str, dict, bool]], bool]:
    """What changed between a check's previous good result and this one:
    (event type, the item, whether it is the previous result's), and
    whether this result was cut at its row limit. An item a person's
    decision closes on the terms it has is not announced."""
    new = {i["key"]: i for i in open_items(tenant_id, name, kind, run.rows, run.started_at)}
    old = ({i["key"]: i for i in open_items(tenant_id, name, kind, previous.rows, previous.started_at)}
           if previous is not None else {})

    def closed(item: dict) -> bool:
        return holds(decisions.get((tenant_id, kind, name, item["key"])), item)

    out: list[tuple[str, dict, bool]] = []
    for key, item in new.items():
        if closed(item):
            continue
        if key not in old:
            out.append(("open_item.opened", item, False))
        elif closed(old[key]):
            out.append(("open_item.reopened", item, False))
        elif (old[key].get("open_days") or 0) < days <= (item.get("open_days") or 0):
            out.append(("open_item.aged", item, False))
    cut_new = run_found(kind, run.summary) > len(new)
    cut_old = previous is not None and run_found(kind, previous.summary) > len(old)
    if previous is not None and not cut_new and not cut_old:
        for key, item in old.items():
            if key not in new and not closed(item):
                out.append(("open_item.cleared", item, True))
    return out, cut_new


def _data(tenant: Tenant, run: RunRecord, title: str, item: dict, decisions: dict, suggestions: dict,
          cut: bool, at: datetime) -> dict[str, Any]:
    """The item as `OpenItem` defines it, as JSON: what every other surface
    answers with. Its age is as of the event."""
    from .open_items import _rows

    rows = _rows(tenant, run, title, [aged(item, at)], decisions, suggestions, "all", cut)
    return OpenItem.model_validate(rows[0]).model_dump(mode="json")


async def after_run(tenant: Tenant, run: RunRecord, previous: RunRecord | None) -> int:
    """The events a stored run makes, for every account with an address
    that may hear of this merchant; none when there is no such address.
    Never raises: a run is not failed by its events."""
    try:
        if run.status != "ok" or run.rows is None:
            return 0
        store = get_store()
        heard, chain = await receivers(store, tenant.id)
        if not heard:
            return 0
        from .exceptions import _titles
        from .runs import file_check_names

        title = _titles().get((run.kind, run.name))
        if title is None:
            return 0
        days = (await store.get_notify(chain[-1])).get("aging_days") or AGING_DAYS
        keys = await asyncio.to_thread(_keys_of, tenant.id, run, previous)
        decisions = await store.current_decisions([(tenant.id, k) for k in keys]) if keys else {}
        found, cut = await asyncio.to_thread(changes, tenant.id, run.kind, run.name, previous, run, decisions, days)
        if not found:
            return 0
        suggestions = await store.newest_suggestions([(tenant.id, i["key"]) for _, i, _ in found])
        at = now()
        reads_files = bool(run.reads_files) or run.name in file_check_names()
        events = await asyncio.to_thread(
            _events, tenant, run, previous, title, found, cut, decisions, suggestions, heard, reads_files, at)
        return await store.add_webhook_events(tenant.id, run.id, events, at)
    except Exception:
        logger.warning("webhook events not made for %s's %s", tenant.id, run.name, exc_info=True)
        return 0


def _keys_of(tenant_id: str, run: RunRecord, previous: RunRecord | None) -> list[str]:
    keys = set(items_by_key(tenant_id, run.name, run.kind, run.rows))
    if previous is not None:
        keys |= set(items_by_key(tenant_id, previous.name, previous.kind, previous.rows))
    return sorted(keys)


def _events(tenant: Tenant, run: RunRecord, previous: RunRecord | None, title: str,
            found: list[tuple[str, dict, bool]], cut: bool, decisions: dict, suggestions: dict,
            heard: list[str], reads_files: bool, at: datetime) -> list[dict]:
    out = []
    for type_, item, was in found:
        source = previous if was and previous is not None else run
        # A cleared item is as the previous result had it, which was not cut.
        data = _data(tenant, source, title, item, decisions, suggestions, False if was else cut, at)
        for account in heard:
            event_id = _event_id()
            out.append({"id": event_id, "tenant_id": account, "type": type_, "kind": run.kind,
                        "check_name": run.name, "item_key": item["key"], "reads_files": reads_files,
                        "body": envelope(event_id, type_, account, at, data)})
    return out


async def after_decision(tenant: Tenant, run: RunRecord, key: str, decision, closed_before: bool,
                         reads_files: bool) -> int:
    """`open_item.closed` or `open_item.reopened` when a person's decision
    changed whether the item is open. Never raises."""
    try:
        store = get_store()
        heard, _ = await receivers(store, tenant.id)
        if not heard:
            return 0
        from .exceptions import _titles

        title = _titles().get((run.kind, run.name))
        at = now()
        items = await asyncio.to_thread(open_items, tenant.id, run.name, run.kind, run.rows, at)
        item = next((i for i in items if i["key"] == key), None)
        if title is None or item is None:
            return 0
        closed_after = holds(decision, item)
        if closed_after == closed_before:
            return 0
        type_ = "open_item.closed" if closed_after else "open_item.reopened"
        decisions = {(tenant.id, run.kind, run.name, key): decision}
        suggestions = await store.newest_suggestions([(tenant.id, key)])
        cut = run_found(run.kind, run.summary) > len(items)
        data = await asyncio.to_thread(_data, tenant, run, title, item, decisions, suggestions, cut, at)
        events = []
        for account in heard:
            event_id = _event_id()
            events.append({"id": event_id, "tenant_id": account, "type": type_, "kind": run.kind,
                           "check_name": run.name, "item_key": key, "reads_files": reads_files,
                           "body": envelope(event_id, type_, account, at, data)})
        return await store.add_webhook_events(tenant.id, run.id, events, at)
    except Exception:
        logger.warning("webhook event not made for a decision on %s", tenant.id, exc_info=True)
        return 0


# ── delivery ───────────────────────────────────────────────────────────

def _retry_at(attempts: int, at: datetime, event_at: datetime) -> datetime | None:
    """When to try again after the attempt numbered `attempts` (from 1), or
    None once the event has been tried for GIVE_UP_AFTER. The last attempt
    is at GIVE_UP_AFTER exactly, so an address failing since an event was
    made is failing that long when it is last tried, and is turned off."""
    if at - event_at >= GIVE_UP_AFTER:
        return None
    # A tenth either way, so addresses that failed together are not tried together.
    wait = RETRY_AFTER[min(attempts, len(RETRY_AFTER)) - 1] * random.uniform(0.9, 1.1)
    return min(at + timedelta(seconds=wait), event_at + GIVE_UP_AFTER)


def _the_address(status: int | None) -> bool:
    """Whether a failure is the address's rather than this event's: it could
    not be reached, or it said it is down or busy. Its other deliveries then
    wait as this one does; a 4xx about one event holds back no other."""
    return status is None or status >= 500 or status in (408, 429)


#: The addresses whose queues this process is sending: each address's
#: account and task. A round takes nothing more for them until it is done.
_sending: dict[str, tuple[str, asyncio.Task]] = {}


async def deliver_due(at: datetime | None = None, *, wait: bool = True) -> int:
    """One round. Places are given out in turns: first one to each account
    with deliveries due (those with nothing being sent before the rest),
    then a second to each, up to ACCOUNT_AT_ONCE an account and
    SENDING_AT_ONCE in all. An account given a place takes, in its own
    scope, up to ADDRESS_DELIVERIES for one of its addresses not being sent
    to already, and that address's queue is sent on its own task; what it
    took for an address with no place goes back. With `wait`, the round
    waits for the tasks and says how many were delivered; the sweeper does
    not wait."""
    store = get_store()
    at = at or now()
    started: list[asyncio.Task] = []
    free = SENDING_AT_ONCE - len(_sending)
    busy: dict[str, int] = {}
    for account, _ in _sending.values():
        busy[account] = busy.get(account, 0) + 1
    due = await store.webhook_due(at, ROUND_ACCOUNTS) if free > 0 else []
    due = sorted(due, key=lambda a: busy.get(a, 0) > 0)
    for turn in range(1, ACCOUNT_AT_ONCE + 1):
        for account in due:
            if free <= 0:
                break
            if busy.get(account, 0) >= turn:
                continue
            claims = await store.claim_webhooks(account, at, at + CLAIM_FOR, ROUND_DELIVERIES,
                                                per_address=ADDRESS_DELIVERIES, skip=set(_sending))
            by_address: dict[str, list] = {}
            for c in claims:
                by_address.setdefault(c.endpoint_id, []).append(c)
            if not by_address:
                continue
            address, *rest = list(by_address)
            back = [(c.endpoint_id, c.event_id) for a in rest for c in by_address[a]]
            if back:
                await store.release_webhooks(account, back)
            task = asyncio.create_task(_guarded(store, by_address[address]))
            _sending[address] = (account, task)
            task.add_done_callback(lambda _t, a=address: _sending.pop(a, None))
            started.append(task)
            busy[account] = busy.get(account, 0) + 1
            free -= 1
    if not wait:
        return 0
    return sum(await asyncio.gather(*started)) if started else 0


async def _guarded(store, queue: list) -> int:
    try:
        return await _send_queue(store, queue)
    except Exception:
        logger.warning("webhook deliveries to %s failed to send", queue[0].endpoint_id, exc_info=True)
        return 0


async def _send_queue(store, queue: list) -> int:
    done = 0
    ends = time.monotonic() + QUEUE_SECONDS
    for n, c in enumerate(queue):
        if time.monotonic() >= ends:
            # The address's turn is over: what is left goes back for a later round.
            await store.release_webhooks(c.tenant_id, [(x.endpoint_id, x.event_id) for x in queue[n:]])
            break
        sent_at = now()
        status, error = await send(c.url, c.body, signed_headers(c.secret, c.event_id, c.body, sent_at.timestamp()))
        if error is None and status is not None:
            await store.webhook_delivered(c.tenant_id, c.endpoint_id, c.event_id, at=sent_at, status=status)
            done += 1
            continue
        retry = _retry_at(c.attempts + 1, sent_at, c.event_at)
        wide = _the_address(status)
        since = await store.webhook_failed(
            c.tenant_id, c.endpoint_id, c.event_id, at=sent_at, status=status, error=error, next_at=retry,
            hold_until=retry if wide else None)
        if since is not None and sent_at - since >= GIVE_UP_AFTER:
            why = f"every delivery failed for three days (last: {error or status})"
            if await store.disable_webhook_endpoint(c.tenant_id, c.endpoint_id, sent_at, why):
                await _tell_turned_off(store, c.tenant_id, c.endpoint_id, c.url, since, error or str(status))
            break
        if wide:
            # The address is down: the rest of this round's queue for it
            # waits with it (`hold_until` moved them).
            break
    return done


async def _tell_turned_off(store, tenant_id: str, endpoint_id: str, url: str, since: datetime,
                           last: str) -> None:
    """Mail the account that one of its addresses was turned off. Nothing of
    an event is in it: the address's host, since when it failed, and how
    to turn it back on."""
    mailer = get_mailer()
    if mailer is None:
        return
    try:
        email = None
        for t in [tenant_id, *await ancestors(store, tenant_id)]:
            email = await store.account_email(t)
            if email:
                break
        if not email:
            return
        host = urlsplit(url).hostname or "your address"
        text = "\n".join([
            f"Okwan has stopped sending events to {host} (webhook {endpoint_id}).",
            "",
            f"Every delivery to it has failed since {since:%Y-%m-%d %H:%M} UTC; the last answer was: {last}.",
            "No more events are sent to it until it is turned back on:",
            f"  POST /v1/webhooks/{endpoint_id}/enable",
            "",
            "The events of the last 30 days can be read at GET /v1/events.",
        ])
        await mailer.send_notice(email, f"Okwan stopped sending events to {host}", text + "\n")
    except Exception as e:  # noqa: BLE001 — a mail that fails must not end the round
        logger.error("webhook %s turned off; the account was not mailed: %s", endpoint_id, type(e).__name__)


async def sweeper(interval: float = ROUND_SECONDS) -> None:
    """Delivery rounds for the life of the process, and once an hour the
    events past EVENTS_KEPT go. A failure is logged; the next round tries
    again."""
    rounds = 0
    while True:
        try:
            await deliver_due(wait=False)
        except Exception:
            logger.warning("webhook round failed", exc_info=True)
        if rounds % max(int(3600 / interval), 1) == 0:
            try:
                await get_store().sweep_webhooks(now() - EVENTS_KEPT)
            except Exception:
                logger.warning("webhook events not swept", exc_info=True)
        rounds += 1
        await asyncio.sleep(interval)


# ── routes ─────────────────────────────────────────────────────────────

class EndpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=9, max_length=URL_MAX,
                     description="Where to send the events: https://, port 443, a public host")


class OpenItemEvent(BaseModel):
    """What each event's body is: Standard Webhooks' `type`, `timestamp` and
    `data`, with the event's `id` and the `account` it was sent to. `data`
    is the open item, as on every other surface."""

    id: str = Field(description="The event, as in the webhook-id header: the same on every attempt")
    type: EventType
    timestamp: datetime
    account: str = Field(description="The account whose address this was sent to")
    data: OpenItem


def _endpoint_out(e: WebhookEndpoint) -> dict[str, Any]:
    return {
        "id": e.id, "url": e.url, "secret_hint": f"{e.hint}…", "enabled": e.disabled_at is None,
        "created_at": e.created_at.isoformat(), "created_by": e.created_by,
        "disabled_at": e.disabled_at.isoformat() if e.disabled_at else None, "disabled_why": e.disabled_why,
        "failing_since": e.failing_since.isoformat() if e.failing_since else None,
        "last_ok_at": e.last_ok_at.isoformat() if e.last_ok_at else None, "pending": e.pending,
    }


_ENDPOINT_ID = r"^whe_[0-9a-f]{16}$"
_EVENT_ID = r"^evt_[0-9a-f]{24}$"


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.post("/webhooks", status_code=201, tags=["webhooks"])
    async def add(body: EndpointIn, request: Request, actor=Depends(admin_actor)) -> dict[str, Any]:
        """Add an address for the account's events (at most five). The answer
        holds its signing secret (`secret`), shown this once: keep it where
        the receiving code reads it. A read key is refused."""
        enforce(request, (WEBHOOK_CHANGE, actor.id))
        try:
            url, host = check_url(body.url)
            await resolve_public(host, PORT, exceptions=False)
        except BadAddress as exc:
            raise HTTPException(422, str(exc)) from None
        except EgressRefused:
            raise HTTPException(422, "the host does not resolve to a public address Okwan may send to") from None
        secret = new_secret()
        try:
            made = await get_store().add_webhook_endpoint(
                actor.id, url=url, secret=secret, created_by=access.credential_now(), at=now())
        except EndpointsFull:
            raise HTTPException(409, f"this account has {WEBHOOK_ENDPOINTS_MAX} addresses; delete one first") from None
        return {**_endpoint_out(made), "secret": secret}

    @router.get("/webhooks", tags=["webhooks"])
    async def listed(actor=Depends(admin_actor)) -> dict[str, Any]:
        """The account's addresses: whether each is on, since when it has
        failed, when it last took an event, and how many wait. Never a secret."""
        return {"data": [_endpoint_out(e) for e in await get_store().webhook_endpoints([actor.id])]}

    @router.delete("/webhooks/{endpoint_id}", status_code=204, tags=["webhooks"])
    async def delete(endpoint_id: str, request: Request, actor=Depends(admin_actor)) -> Response:
        """Delete an address, its secret and what waits to be sent to it."""
        enforce(request, (WEBHOOK_CHANGE, actor.id))
        if not await get_store().delete_webhook_endpoint(actor.id, endpoint_id):
            raise HTTPException(404, f"no such webhook: {endpoint_id}")
        return Response(status_code=204)

    @router.post("/webhooks/{endpoint_id}/enable", tags=["webhooks"])
    async def enable(endpoint_id: str, request: Request, actor=Depends(admin_actor)) -> dict[str, Any]:
        """Turn an address back on after Okwan turned it off. Events made
        while it was off are not sent to it; read them at GET /v1/events."""
        enforce(request, (WEBHOOK_CHANGE, actor.id))
        found = await get_store().enable_webhook_endpoint(actor.id, endpoint_id)
        if found is None:
            raise HTTPException(404, f"no such webhook: {endpoint_id}")
        return _endpoint_out(found)

    @router.post("/webhooks/{endpoint_id}/test", tags=["webhooks"])
    async def test(endpoint_id: str, request: Request, actor=Depends(admin_actor)) -> dict[str, Any]:
        """Send the address a signed `ping` now, and say what it answered.
        Nothing is kept of it."""
        enforce(request, (WEBHOOK_TEST, actor.id))
        found = await get_store().webhook_secret(actor.id, endpoint_id)
        if found is None:
            raise HTTPException(404, f"no such webhook: {endpoint_id}")
        url, secret = found
        event_id, at = _event_id(), now()
        body = json.dumps({"id": event_id, "type": "ping", "timestamp": at.isoformat(), "account": actor.id,
                           "data": {"webhook": endpoint_id}}, separators=(",", ":"))
        status, error = await send(url, body, signed_headers(secret, event_id, body, at.timestamp()))
        return {"delivered": error is None and status is not None, "status": status, "error": error,
                "event_id": event_id}

    @router.get("/events", tags=["events"])
    async def events(
        request: Request,
        after: str | None = Query(None, pattern=_EVENT_ID,
                                  description="The last event already read: those after it, oldest first"),
        type: EventType | None = Query(None, description="One type of event"),
        limit: int = Query(50, ge=1, le=EVENTS_LISTED),
        tenant=Depends(current_tenant),
    ) -> dict[str, Any]:
        """The events Okwan made for this account's addresses in the last 30
        days, oldest first, each with its body as sent and what became of
        each delivery. A read key may read them: they are the account's open
        items, which it reads anyway. Logged as a read."""
        enforce(request, (EVENTS_TENANT, tenant.id))
        await access.record(tenant, "rest:events", "/v1/events")
        found = await get_store().webhook_events(
            tenant.id, after=after, since=now() - EVENTS_KEPT, limit=limit, types=None if type is None else [type])
        data = [{
            "id": e.id, "type": e.type, "created_at": e.created_at.isoformat(), "merchant_id": e.merchant_id,
            "body": json.loads(e.body),
            "deliveries": [{**d, "last_at": d["last_at"].isoformat() if d.get("last_at") else None,
                            "next_at": d["next_at"].isoformat() if d.get("next_at") else None}
                           for d in e.deliveries],
        } for e in found]
        return {"data": data, "next": data[-1]["id"] if len(data) == limit else None}

    return router


def document(app) -> None:
    """Each event in the OpenAPI document's `webhooks`, from the one
    definition: what an address receives is an OpenItemEvent."""
    for type_ in EVENT_TYPES:
        def receive(body: OpenItemEvent) -> None:  # pragma: no cover — documentation only
            """Documentation of what Okwan sends; nothing serves it."""

        receive.__doc__ = _WHEN[type_]
        app.webhooks.post(type_, summary=type_, tags=["webhooks"])(receive)


_WHEN = {
    "open_item.opened": "A run of its check found the item, and the run before it did not.",
    "open_item.aged": "A run found the item still open on the day it had been open the account's number of days.",
    "open_item.cleared": "The check's newest run no longer finds the item (neither result was cut at its limit).",
    "open_item.closed": "A person explained or resolved the item.",
    "open_item.reopened": "A person opened the item again, or a run found it on other terms than the decision "
                          "that closed it.",
}
