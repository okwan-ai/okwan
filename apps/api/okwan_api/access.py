"""Who read a tenant's data, from where and when, and what was unusual.

`remember` runs where a caller is authenticated (an API key, or a
dashboard session) and keeps who and from where for the rest of the
request. `record` runs inside `meter`, after a read succeeded, and writes
one access row: when, the key's public prefix or "session", the caller's
address and network, the surface, and what was read. Never the key, query
text, parameters or results.

Two things raise an alert, kept on the tenant and mailed to the account,
with a copy to the operator's escalation inbox:

- an API key reading from a network it has not used in 30 days, once the
  key has a day of history (checked as the row is written), and
- an hour in which a key read at least 100 times and more than three times
  its busiest hour of the week before (checked by the hourly sweep).

Like the meter, nothing here can fail the read it records.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Coroutine, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from okwan_vault import access as rules
from okwan_vault.apikey import public_prefix
from okwan_vault.models import Tenant

from .mail import MailNotSent, dashboard_url, get_mailer
from .ratelimit import address_from

logger = logging.getLogger(__name__)

SWEEP_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class Caller:
    credential: str
    ip: str
    target: str


_caller: ContextVar[Caller | None] = ContextVar("okwan_access_caller", default=None)

#: Alerts being raised in the background, held so they are not collected
#: mid-flight, and the (tenant, key, network) pairs alerted lately, so two
#: reads racing in from a new network raise one alert, not two.
_pending: set[asyncio.Task] = set()
_alerted: dict[tuple[str, str, str], datetime] = {}


def credential_for(token: str) -> str:
    """How a presented credential is logged: an API key by its public
    prefix, which the dashboard also shows, and anything else as a
    dashboard session. Never enough of a key to use it."""
    return public_prefix(token) if token.startswith(rules.KEY_MARK) else rules.SESSION


def remember(headers: Mapping[str, str], peer: str | None, token: str, target: str = "") -> None:
    _caller.set(Caller(credential_for(token), address_from(headers, peer), target))


def remember_request(request: Request, token: str) -> None:
    """For a FastAPI route: the route's path is what was read."""
    remember(request.headers, request.client.host if request.client else None, token,
             request.url.path)


async def record(tenant: Tenant, surface: str, target: str | None = None) -> None:
    """Write the access row for one metered read, and alert on a new network."""
    from .auth import get_store

    caller = _caller.get() or Caller("unknown", "unknown", "")
    network = rules.network_of(caller.ip)
    now = datetime.now(UTC)
    try:
        seen = await get_store().record_access(
            tenant.id, credential=caller.credential, ip=caller.ip, network=network,
            surface=surface, target=target or caller.target, at=now)
    except Exception:
        logger.warning("access not recorded for %s", tenant.id, exc_info=True)
        return
    if (rules.is_key(caller.credential) and caller.ip != "unknown" and not seen.known_network
            and seen.first_seen is not None and now - seen.first_seen >= rules.ESTABLISHED
            and _first_alert((tenant.id, caller.credential, network), now)):
        _spawn(_alert(tenant.id, "new_network", caller.credential, {
            "ip": caller.ip, "network": network, "surface": surface,
            "target": target or caller.target, "at": now.isoformat(),
        }))


def _first_alert(pair: tuple[str, str, str], now: datetime) -> bool:
    for k in [k for k, at in _alerted.items() if now - at > timedelta(hours=1)]:
        del _alerted[k]
    if pair in _alerted:
        return False
    _alerted[pair] = now
    return True


def _spawn(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def settle() -> None:
    """Wait for alerts raised in the background. For tests and shutdown."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def check_volume(now: datetime | None = None) -> int:
    """Alert on every key that read unusually much in the hour to `now`."""
    from .auth import get_store

    now = now or datetime.now(UTC)
    spikes = await get_store().volume_spikes(now)
    for s in spikes:
        await _alert(s["tenant_id"], "volume", s["credential"], {
            "requests": s["requests"], "busiest": s["busiest"], "hour_to": now.isoformat(),
        })
    return len(spikes)


async def sweeper(interval: float = SWEEP_SECONDS) -> None:
    """Check the last hour's volume, then delete aged rows; once an interval.

    A failure is logged and retried on the next round; it never stops the
    API. A missed round delays an alert or a deletion, nothing more.
    """
    from .auth import get_store

    while True:
        try:
            await check_volume()
            gone = await get_store().sweep_access()
            if gone:
                logger.info("access sweep deleted %d rows past retention", gone)
        except Exception:
            logger.warning("access sweep failed", exc_info=True)
        await asyncio.sleep(interval)


async def _alert(tenant_id: str, kind: str, credential: str, detail: dict) -> None:
    from .auth import get_store

    store = get_store()
    try:
        alert = await store.add_access_alert(tenant_id, kind=kind, credential=credential,
                                             detail=detail)
        # The tenant and the alert's id, never the address: the log is
        # where an operator looks first, and it is kept longer than this.
        logger.warning("access alert %s on %s (%s)", kind, tenant_id, alert["id"])
        if await _mail(tenant_id, alert):
            await store.set_alert_mailed(tenant_id, alert["id"])
    except Exception:
        logger.warning("access alert %s on %s not raised", kind, tenant_id, exc_info=True)


async def _mail(tenant_id: str, alert: dict) -> bool:
    """Mail the account, and copy the operator. True when the account's
    mail went out; an account with no mailer or no address is not mailed."""
    from .auth import get_store

    mailer = get_mailer()
    if mailer is None:
        return False
    store = get_store()
    tenant = await store.get_tenant(tenant_id)
    subject, text = message(alert, tenant.name if tenant else tenant_id)
    sent = False
    email = await store.account_email(tenant_id)
    if email:
        try:
            await mailer.send_notice(email, subject, text)
            sent = True
        except MailNotSent as e:
            logger.error("access alert %s not mailed: %s", alert["id"], e)
    operator = os.environ.get("OKWAN_SUPPORT_ESCALATION_TO", "").strip()
    if operator:
        try:
            await mailer.send_notice(operator, f"[Access alert] {subject}", text)
        except MailNotSent as e:
            logger.error("access alert %s not copied to the operator: %s", alert["id"], e)
    return sent


def message(alert: dict, tenant_name: str) -> tuple[str, str]:
    """The subject and plain-text body of an alert mail."""
    d = alert["detail"]
    key = alert["credential"]
    keys = f"{dashboard_url()}/settings?tab=keys"
    log = f"GET /v1/tenants/{alert['tenant_id']}/access"
    if alert["kind"] == "new_network":
        at = datetime.fromisoformat(d["at"]).strftime("%Y-%m-%d %H:%M UTC")
        subject = f"Okwan: API key {key} was used from a new network"
        lead = (f"Your Okwan API key {key} read data from a network it has not used "
                "in the last 30 days.")
        facts = (f"Account: {tenant_name} ({alert['tenant_id']})\n"
                 f"When: {at}\n"
                 f"From: {d['ip']} (network {d['network']})\n"
                 f"What: {d['surface']} {d['target']}".rstrip())
    else:
        to = datetime.fromisoformat(d["hour_to"]).strftime("%Y-%m-%d %H:%M UTC")
        subject = f"Okwan: unusual volume on API key {key}"
        lead = (f"Your Okwan API key {key} made {d['requests']} reads in the hour to {to}. "
                f"Its busiest hour in the week before was {d['busiest']}.")
        facts = f"Account: {tenant_name} ({alert['tenant_id']})"
    text = (f"{lead}\n\n{facts}\n\n"
            "If this was you or your own systems, there is nothing to do.\n\n"
            f"If it was not, revoke the key now under Settings, API keys ({keys}) and issue a "
            "new one. A revoked key stops working at once.\n\n"
            f"Every read of this account's data in the last 90 days, with its address, is at {log}.\n")
    return subject, text
