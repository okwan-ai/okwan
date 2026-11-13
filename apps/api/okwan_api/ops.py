"""The operator console's API: the people who run Okwan, across every account.

Never for a customer, and built to be the hardest door in the product:

* **Who.** Only a dashboard session (never an API key) whose login's address
  is in OKWAN_OPERATOR_EMAILS. Anyone else gets the same 404 an unknown
  route gets, so the console's existence is not advertised. Unset, the
  console does not exist for anyone.
* **Second factor.** Even then, the session must be unlocked with a code
  from an authenticator app (enrolled with `python -m okwan_vault operator
  enroll`), for 30 minutes at a time. Codes are rate limited and work once.
* **Everything is logged.** Every action, reads included, is written to
  operator_actions, which the database keeps append-only. Only /me, the
  check each dashboard page makes to draw the sidebar, is not.
* **No secret values.** Tickets, briefs and conversations are already
  redacted; a reply that looks like it carries a secret is refused.
  Accounts show which connections are saved by field name and keys by
  prefix: never a credential value or a key hash.

Customer data here is read across tenants on purpose. Writes still go
through each tenant's own scope: a ticket's status and replies are written
as that tenant, under row-level security.
"""
from __future__ import annotations

import html
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from okwan_vault import accounts, totp
from okwan_vault.usage import month_start, quota
from pydantic import BaseModel, Field

from . import disputes
from .auth import get_store
from .mail import MailNotSent, get_mailer
from .ratelimit import OPS_UNLOCK, OPS_UNLOCK_IP, client_ip, enforce
from .support import CATEGORY_LABELS, redact

UNLOCK_FOR = timedelta(minutes=30)
#: When this process started, so Health can say how long the deploy has run.
STARTED = datetime.now(UTC)
#: How far back Health looks for failed checks.
HEALTH_DAYS = 7
STATUSES = ("open", "answered", "resolved")
_replay = totp.ReplayGuard()


def operator_emails() -> frozenset[str]:
    raw = os.environ.get("OKWAN_OPERATOR_EMAILS", "")
    return frozenset(accounts.normalize_email(e) for e in raw.split(",") if e.strip())


def _not_found() -> HTTPException:
    # Exactly what FastAPI answers for a route that does not exist.
    return HTTPException(404, "Not Found")


@dataclass(frozen=True)
class Operator:
    email: str
    tenant_id: str
    token_hash: str
    until: datetime | None

    @property
    def unlocked(self) -> bool:
        return self.until is not None and self.until > datetime.now(UTC)


async def operator(authorization: str = Header(default="")) -> Operator:
    allowed = operator_emails()
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not allowed or not token.startswith(f"{accounts.SESSION_PREFIX}_"):
        raise _not_found()
    store = get_store()
    token_hash = accounts.hash_token(token)
    tenant = await store.tenant_for_session(token_hash)
    if tenant is None:
        raise _not_found()
    email = await store.account_email(tenant.id)
    if not email or accounts.normalize_email(email) not in allowed:
        raise _not_found()
    until = await store.session_operator_until(token_hash)
    return Operator(accounts.normalize_email(email), tenant.id, token_hash, until)


async def unlocked_operator(op: Operator = Depends(operator)) -> Operator:
    if not op.unlocked:
        raise HTTPException(403, "locked: enter the code from your authenticator app")
    return op


async def _log(op: Operator, action: str, target: str | None = None, **detail: Any) -> None:
    await get_store().log_operator(op.email, action, target, detail or None)


# ── tickets ────────────────────────────────────────────────────────────

async def _ticket_row(t) -> dict[str, Any]:
    store = get_store()
    tenant = await store.get_tenant(t.tenant_id)
    return {
        "id": t.id, "severity": t.severity, "category": t.category,
        "category_label": CATEGORY_LABELS.get(t.category, t.category),
        "issue": t.summary, "status": t.status, "notified": t.notified,
        "created_at": t.created_at.isoformat(),
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "tenant_id": t.tenant_id, "account": tenant.name if tenant else t.tenant_id,
        "contact": await store.account_email(t.tenant_id),
    }


def transcript(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The conversation as a person reads it: what the customer wrote, what
    the assistant answered, and which tools it ran. Tool results stay out:
    the brief already says what they showed."""
    out: list[dict[str, str]] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            if m.get("role") == "system":
                out.append({"from": "note", "text": content})
            continue
        for block in content or []:
            kind = block.get("type")
            if kind == "text" and block.get("text"):
                out.append({"from": "customer" if m["role"] == "user" else "assistant",
                            "text": block["text"]})
            elif kind == "tool_use":
                out.append({"from": "tool", "text": block.get("name", "tool")})
    return out


def reply_message(ticket_id: str, body: str) -> tuple[str, str, str]:
    """Subject, plain text and HTML for a person's reply to a customer.
    The reply is escaped as text; there are no links."""
    subject = f"Re: your Okwan support request ({ticket_id})"
    text = (f"{body}\n\n--\nOkwan Support\nReply to this email to continue. "
            f"Your request: {ticket_id}\n")
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    page = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f5f5f5">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:24px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid #e5e5e5;border-radius:12px;font-family:{font};font-size:15px;line-height:1.6;color:#0a0a0a">
<tr><td style="background:#0b0b0c;border-radius:12px 12px 0 0;padding:16px 24px">
<span style="color:#ffffff;font-size:18px;font-weight:600;letter-spacing:-0.02em">okwan</span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#ffd400;margin-left:6px;vertical-align:middle"></span>
<span style="color:#a3a3a3;font-size:13px;margin-left:10px">Support</span>
</td></tr>
<tr><td style="padding:24px;white-space:pre-wrap">{html.escape(body)}</td></tr>
<tr><td style="padding:14px 24px;border-top:1px solid #e5e5e5;color:#6b6b6b;font-size:12px">
Reply to this email to continue. Your request: <span style="font-family:ui-monospace,Menlo,monospace">{html.escape(ticket_id)}</span>
</td></tr>
</table>
</td></tr></table>
</body></html>
"""
    return subject, text, page


# ── accounts ───────────────────────────────────────────────────────────

def _run(r) -> dict[str, Any]:
    return {"id": r.id, "kind": r.kind, "name": r.name, "surface": r.surface,
            "status": r.status, "finished_at": r.finished_at.isoformat(), "error": r.error}


async def _members(tenant) -> list:
    """The account and its merchants: what one plan and one login cover."""
    return [tenant, *await get_store().children_of(tenant.id)]


async def _latest_run(members: list):
    store = get_store()
    runs = [r for m in members for r in await store.list_runs(m.id, None, 1)]
    return max(runs, key=lambda r: r.finished_at, default=None)


async def _account_row(tenant, open_tickets: dict[str, int]) -> dict[str, Any]:
    store = get_store()
    members = await _members(tenant)
    q = await quota(store, tenant.id)
    connections = sorted({c for m in members for c in await store.connectors_configured(m.id)})
    keys = [k for m in members for k in await store.keys_for(m.id)]
    last = await _latest_run(members)
    return {
        "id": tenant.id, "name": tenant.name, "created_at": tenant.created_at.isoformat(),
        "contact": await store.account_email(tenant.id),
        "plan": {"name": q.plan, "limit": q.limit, "used": q.used, "unmetered": q.unmetered},
        "merchants": len(members) - 1,
        "connections": connections,
        "active_keys": sum(k.is_active for k in keys),
        "last_run": _run(last) if last else None,
        "open_tickets": open_tickets.get(tenant.id, 0),
    }


async def _open_tickets() -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in await get_store().list_tickets(500, "open"):
        counts[t.tenant_id] = counts.get(t.tenant_id, 0) + 1
    return counts


# ── health ─────────────────────────────────────────────────────────────

def _switches() -> list[dict[str, Any]]:
    """Which of the service's switches are on. Whether a variable is set,
    never its value."""
    from .billing import enabled as billing_enabled
    from .mail import DevLogMailer
    from .signup import signup_open
    from .support import enabled as assistant_enabled

    def has(name: str) -> bool:
        return bool(os.environ.get(name, "").strip())

    mailer = get_mailer()
    mail = "off" if mailer is None else "dev log" if isinstance(mailer, DevLogMailer) else "on"
    return [
        {"name": "Mail", "state": mail, "ok": mail == "on",
         "needs": "RESEND_API_KEY and OKWAN_MAIL_FROM"},
        {"name": "Escalation inbox", "state": "on" if has("OKWAN_SUPPORT_ESCALATION_TO") else "off",
         "ok": has("OKWAN_SUPPORT_ESCALATION_TO"), "needs": "OKWAN_SUPPORT_ESCALATION_TO"},
        {"name": "Support assistant", "state": "on" if assistant_enabled() else "off",
         "ok": assistant_enabled(), "needs": "ANTHROPIC_API_KEY"},
        {"name": "Sign-up", "state": "open" if signup_open() else "closed",
         # Closed is the intended state until the privacy policy and terms.
         "ok": None, "needs": "OKWAN_SIGNUP_OPEN=true"},
        {"name": "MCP host allowlist", "state": "on" if has("OKWAN_ALLOWED_HOSTS") else "off",
         "ok": has("OKWAN_ALLOWED_HOSTS"), "needs": "OKWAN_ALLOWED_HOSTS"},
        {"name": "Dashboard client addresses", "state": "on" if has("OKWAN_DASHBOARD_SECRET") else "off",
         "ok": has("OKWAN_DASHBOARD_SECRET"), "needs": "OKWAN_DASHBOARD_SECRET"},
        {"name": "Card payments (Stripe)", "state": "on" if billing_enabled() else "off",
         "ok": billing_enabled(),
         "needs": "OKWAN_BILLING_STRIPE_SECRET_KEY, OKWAN_BILLING_STRIPE_WEBHOOK_SECRET and OKWAN_BILLING_PRO_PRICE_ID"},
    ]


async def _vault() -> dict[str, Any]:
    """The vault's kind and one round trip. A vault that is down never gets
    here: the operator check above already needed it (and /healthz, which
    the monitor polls, answers 503)."""
    pool = getattr(get_store(), "_pool", None)
    if pool is None:
        return {"kind": "memory", "ms": None}
    started = time.perf_counter()
    await pool.fetchval("SELECT 1")
    return {"kind": "postgres", "ms": round((time.perf_counter() - started) * 1000, 1)}


class UnlockIn(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    resolve: bool = False


class StatusIn(BaseModel):
    status: str = Field(pattern="^(open|answered|resolved)$")


def build_router() -> APIRouter:
    # Not in the public API reference: customers have no use for it.
    router = APIRouter(prefix="/v1/ops", include_in_schema=False)

    @router.get("/me")
    async def me(op: Operator = Depends(operator)) -> dict[str, Any]:
        enrolled = await get_store().operator_factor(op.email) is not None
        return {"email": op.email, "enrolled": enrolled,
                "unlocked_until": op.until.isoformat() if op.unlocked else None}

    @router.post("/unlock")
    async def unlock(body: UnlockIn, request: Request,
                     op: Operator = Depends(operator)) -> dict[str, Any]:
        enforce(request, (OPS_UNLOCK, op.email), (OPS_UNLOCK_IP, client_ip(request)))
        store = get_store()
        secret = await store.operator_factor(op.email)
        if secret is None:
            raise HTTPException(409, "no authenticator enrolled: run "
                                     f"python -m okwan_vault operator enroll {op.email}")
        if not _replay.verify(op.email, secret, body.code):
            await _log(op, "unlock_failed", ip=client_ip(request))
            raise HTTPException(401, "that code is not right; codes change every 30 seconds")
        until = datetime.now(UTC) + UNLOCK_FOR
        await store.set_session_operator(op.token_hash, until)
        await _log(op, "unlock", ip=client_ip(request))
        return {"unlocked_until": until.isoformat()}

    @router.post("/lock")
    async def lock(op: Operator = Depends(operator)) -> dict[str, Any]:
        await get_store().set_session_operator(op.token_hash, None)
        await _log(op, "lock")
        return {"unlocked_until": None}

    @router.get("/tickets")
    async def tickets(status: str = Query("open", pattern="^(open|answered|resolved|all)$"),
                      op: Operator = Depends(unlocked_operator)) -> dict[str, Any]:
        found = await get_store().list_tickets(200, None if status == "all" else status)
        rows = [await _ticket_row(t) for t in found]
        await _log(op, "list_tickets", status=status, count=len(rows))
        return {"data": rows}

    @router.get("/tickets/{ticket_id}")
    async def ticket(ticket_id: str, op: Operator = Depends(unlocked_operator)) -> dict[str, Any]:
        store = get_store()
        t = await store.get_ticket(ticket_id)
        if t is None:
            raise HTTPException(404, f"no such ticket: {ticket_id}")
        conversation = (await store.get_conversation(t.tenant_id, t.conversation_id)
                        if t.conversation_id else None)
        await _log(op, "view_ticket", ticket_id, tenant_id=t.tenant_id)
        return {
            **await _ticket_row(t),
            "brief": t.brief or {},
            "conversation": transcript(conversation.messages) if conversation else [],
            "replies": [{**r, "created_at": r["created_at"].isoformat()}
                        for r in await store.replies_for(t.tenant_id, t.id)],
        }

    @router.post("/tickets/{ticket_id}/reply")
    async def reply(ticket_id: str, body: ReplyIn,
                    op: Operator = Depends(unlocked_operator)) -> dict[str, Any]:
        store = get_store()
        t = await store.get_ticket(ticket_id)
        if t is None:
            raise HTTPException(404, f"no such ticket: {ticket_id}")
        contact = await store.account_email(t.tenant_id)
        if not contact:
            raise HTTPException(409, "this account has no sign-in address to reply to")
        _, found = redact(body.body)
        if found:
            raise HTTPException(422, f"the reply looks like it contains {', '.join(found)}; "
                                     "remove it before sending")
        mailer = get_mailer()
        if mailer is None:
            raise HTTPException(503, "no mailer: set RESEND_API_KEY and OKWAN_MAIL_FROM")
        subject, text, page = reply_message(t.id, body.body.strip())
        reply_to = os.environ.get("OKWAN_SUPPORT_ESCALATION_TO", "").strip() or None
        try:
            await mailer.send_notice(contact, subject, text, html=page, reply_to=reply_to)
        except MailNotSent as exc:
            await store.add_reply(t.tenant_id, t.id, op.email, body.body.strip(), False)
            await _log(op, "reply_failed", t.id, tenant_id=t.tenant_id, error=str(exc))
            raise HTTPException(502, f"the reply was not sent: {exc}") from None
        saved = await store.add_reply(t.tenant_id, t.id, op.email, body.body.strip(), True)
        status = "resolved" if body.resolve else "answered"
        await store.set_ticket_status(t.tenant_id, t.id, status)
        await _log(op, "reply", t.id, tenant_id=t.tenant_id, resolve=body.resolve)
        return {"reply": {**saved, "created_at": saved["created_at"].isoformat()},
                "status": status}

    @router.post("/tickets/{ticket_id}/status")
    async def set_status(ticket_id: str, body: StatusIn,
                         op: Operator = Depends(unlocked_operator)) -> dict[str, Any]:
        store = get_store()
        t = await store.get_ticket(ticket_id)
        if t is None:
            raise HTTPException(404, f"no such ticket: {ticket_id}")
        await store.set_ticket_status(t.tenant_id, t.id, body.status)
        await _log(op, "set_status", t.id, tenant_id=t.tenant_id, status=body.status)
        return {"status": body.status}

    @router.get("/accounts")
    async def accounts_list(op: Operator = Depends(unlocked_operator)) -> dict[str, Any]:
        store = get_store()
        roots = sorted((t for t in await store.list_tenants() if t.parent_id is None),
                       key=lambda t: t.created_at, reverse=True)[:500]
        open_tickets = await _open_tickets()
        rows = [await _account_row(t, open_tickets) for t in roots]
        await _log(op, "list_accounts", count=len(rows))
        return {"data": rows}

    @router.get("/accounts/{tenant_id}")
    async def account(tenant_id: str, op: Operator = Depends(unlocked_operator)) -> dict[str, Any]:
        store = get_store()
        tenant = await store.get_tenant(tenant_id)
        # Accounts are root tenants; a merchant is shown on its account's page.
        if tenant is None or tenant.parent_id is not None:
            raise HTTPException(404, f"no such account: {tenant_id}")
        members = await _members(tenant)
        by_surface: dict[str, int] = {}
        for _, _, surface, count in await store.usage_buckets(tenant.id, month_start()):
            by_surface[surface] = by_surface.get(surface, 0) + count
        merchants = []
        for m in members:
            last = await _latest_run([m])
            merchants.append({
                "id": m.id, "name": m.name, "is_account": m.id == tenant.id,
                "created_at": m.created_at.isoformat(),
                # Field names only: which parts of a credential are saved.
                "connections": await store.connectors_configured(m.id),
                "last_run": _run(last) if last else None,
            })
        keys = [{"id": k.id, "tenant_id": k.tenant_id, "prefix": k.prefix,
                 "issued": k.created_at.isoformat(),
                 "revoked": k.revoked_at.isoformat() if k.revoked_at else None}
                for m in members for k in await store.keys_for(m.id)]
        runs = sorted([r for m in members for r in await store.list_runs(m.id, None, 10)],
                      key=lambda r: r.finished_at, reverse=True)[:15]
        ids = {m.id for m in members}
        tickets = [await _ticket_row(t) for t in await store.list_tickets(500, None)
                   if t.tenant_id in ids][:50]
        await _log(op, "view_account", tenant.id)
        return {
            **await _account_row(tenant, await _open_tickets()),
            "usage_by_surface": dict(sorted(by_surface.items())),
            "month_start": month_start().isoformat(),
            "members": merchants,
            "keys": sorted(keys, key=lambda k: k["issued"], reverse=True),
            "runs": [{**_run(r), "tenant_id": r.tenant_id} for r in runs],
            "tickets": tickets,
        }

    async def _dispute_row(d: dict[str, Any], now: datetime) -> dict[str, Any]:
        tenant = await get_store().get_tenant(d["tenant_id"]) if d["tenant_id"] else None
        return {
            "id": d["dispute_id"], "kind": d["kind"], "status": d["status"], "reason": d["reason"],
            "stage": disputes.stage(d), "amount": disputes.amount(d), "charge_id": d["charge_id"],
            "opened_at": d["opened_at"].isoformat(), "due_by": d["due_by"].isoformat() if d["due_by"] else None,
            "days_left": disputes.days_left(d["due_by"], now),
            # Waiting for an answer is Stripe's state. `submitted`: an
            # answer is with the bank. `answered_before`: one was sent at
            # an earlier stage and the bank is waiting again. `saved`:
            # evidence is in Stripe, not sent, and the bank still waits.
            "needs_answer": disputes.waits(d), "submitted": d["submitted"] and not disputes.waits(d),
            "answered_before": d["submitted"] and disputes.waits(d),
            "saved": d["has_evidence"] and disputes.waits(d),
            # False: its charge is not a payment Okwan has on record.
            "matched": d["okwan"],
            "account": {"id": tenant.id, "name": tenant.name} if tenant else None,
        }

    @router.get("/disputes")
    async def disputes_list(op: Operator = Depends(unlocked_operator)) -> dict[str, Any]:
        """Disputed payments with Okwan's rate: what is waiting for an
        answer first, the nearest date at the top, then the rest newest first."""
        now = datetime.now(UTC)
        rows = [await _dispute_row(d, now) for d in await get_store().disputes(200)]
        await _log(op, "list_disputes", count=len(rows))
        act = await disputes.activity(now)
        return {"activity": {**act, "said": disputes.activity_line(act)}, "data": rows}

    @router.get("/disputes/{dispute_id}")
    async def dispute(dispute_id: str, op: Operator = Depends(unlocked_operator)) -> dict[str, Any]:
        """One dispute with what Okwan holds, in the fields of Stripe's form."""
        now = datetime.now(UTC)
        d = await get_store().dispute(dispute_id)
        if d is None:
            raise HTTPException(404, "no such dispute")
        await _log(op, "view_dispute", dispute_id, tenant_id=d["tenant_id"])
        return {**await _dispute_row(d, now), "evidence": await disputes.evidence(d, now),
                "stripe_url": f"https://dashboard.stripe.com/payments/{d['charge_id']}"}

    @router.get("/health")
    async def health(op: Operator = Depends(unlocked_operator)) -> dict[str, Any]:
        store = get_store()
        since = datetime.now(UTC) - timedelta(days=HEALTH_DAYS)
        tenants = await store.list_tenants()
        by_id = {t.id: t for t in tenants}
        ok = failed = 0
        failures = []
        # Through each tenant's own reads: one query per tenant, which is
        # fine at today's size and keeps row-level security in the path.
        for t in tenants:
            for r in await store.list_runs(t.id, None, 20):
                if r.finished_at < since:
                    continue
                if r.status == "ok":
                    ok += 1
                    continue
                failed += 1
                root = by_id.get(t.parent_id) if t.parent_id else t
                failures.append({**_run(r), "tenant_id": t.id, "tenant": t.name,
                                 "account_id": root.id if root else t.id,
                                 "account": root.name if root else t.name})
        failures.sort(key=lambda f: f["finished_at"], reverse=True)
        tickets = await store.list_tickets(500, None)
        not_mailed = [await _ticket_row(t) for t in tickets
                      if not t.notified and t.status != "resolved"][:20]
        near = []
        for t in tenants:
            if t.parent_id is not None:
                continue
            q = await quota(store, t.id)
            if not q.unmetered and q.limit and q.used >= 0.8 * q.limit:
                near.append({"id": t.id, "name": t.name, "plan": q.plan, "used": q.used, "limit": q.limit})
        commit = os.environ.get("RENDER_GIT_COMMIT", "").strip()
        await _log(op, "view_health")
        return {
            "service": {"commit": commit[:7] or None, "started_at": STARTED.isoformat(),
                        "vault": await _vault()},
            "switches": _switches(),
            "runs": {"days": HEALTH_DAYS, "ok": ok, "failed": failed, "failures": failures[:50]},
            "not_mailed": not_mailed,
            "near_limit": sorted(near, key=lambda a: a["used"] / a["limit"], reverse=True),
            "accounts": sum(t.parent_id is None for t in tenants),
            "merchants": sum(t.parent_id is not None for t in tenants),
            "open_tickets": sum(t.status == "open" for t in tickets),
            "activity": [{**e, "at": e["at"].isoformat()} for e in await store.operator_log(15)],
        }

    @router.get("/log")
    async def log(limit: int = Query(100, ge=1, le=500),
                  op: Operator = Depends(unlocked_operator)) -> dict[str, Any]:
        entries = await get_store().operator_log(limit)
        await _log(op, "view_log")
        return {"data": [{**e, "at": e["at"].isoformat()} for e in entries]}

    return router
