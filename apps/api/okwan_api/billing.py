"""Paying for Okwan: the Pro plan by card, through Stripe.

This is Okwan's own billing, in Okwan's own Stripe account. It is not a
connector: nothing here reads or writes a customer's rails, and it never
goes through the connector registry, so the read-only rule for rails is
untouched.

* **Checkout.** A signed-in account owner asks for Pro; we create a Stripe
  Checkout session for the Pro price and send the browser there. Card
  details go to Stripe and never reach Okwan.
* **The webhook decides the plan.** Stripe tells us, in a signed event,
  when a subscription starts, changes or ends, and the plan follows: Pro
  while the subscription is active (or retrying a failed payment), Free
  once it is cancelled or unpaid. A browser coming back from Checkout
  changes nothing by itself.
* **Manage billing.** Stripe's customer portal: change card, see invoices,
  cancel. Cancelling keeps Pro to the end of the paid month.
* **The policy is shown before the card is charged.** Checkout carries the
  price, that it renews, how to cancel and that a month is not refunded,
  with links to the refund policy and the Terms (`policy.at_checkout`),
  beside the pay button. What each Checkout session showed is recorded
  under that session's own id: the policy's version, when, and from which
  address. When Stripe reports a session paid, that session's record, and
  no other, is marked with when and with the subscription it started; it
  is written once and never changed by a later checkout. A cardholder who
  disputes a payment is answered with the record of the session that took
  it. With OKWAN_BILLING_REQUIRE_TERMS on, Checkout also makes the
  customer tick a box agreeing to both, and the record keeps when Stripe
  reported it ticked; Stripe refuses such a session until the Terms'
  address is set in the Stripe account's public details, which is why it
  is a switch and not the default.

Off until OKWAN_BILLING_STRIPE_SECRET_KEY, OKWAN_BILLING_STRIPE_WEBHOOK_SECRET
and OKWAN_BILLING_PRO_PRICE_ID are set; the dashboard then offers a call
instead of a button.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from okwan_vault import accounts

from . import access, policy
from .auth import get_store, quota_for
from .mail import get_mailer
from .ratelimit import BILLING_TENANT, client_ip, enforce

logger = logging.getLogger("okwan.billing")

STRIPE_API = "https://api.stripe.com/v1"
PRO = "pro"
#: One number, said by the plan page, Checkout and the refund policy.
PRO_PRICE_USD = policy.PRO_PRICE_USD
#: How old a webhook's signature may be: Stripe's own default tolerance.
SIGNATURE_TOLERANCE = 300
#: The most unpaid Checkout sessions one account may have on record from
#: the last day. The hourly limit on the route (ratelimit.BILLING_TENANT)
#: is what stops a burst; this keeps the rows of many hours bounded.
MAX_OPEN_CHECKOUTS = 20
#: Subscription states that keep Pro. past_due is Stripe retrying a failed
#: payment; the plan holds while it does, and ends if Stripe gives up.
PAID = frozenset({"active", "trialing", "past_due"})
#: The events that say a Checkout session took its payment: at once for a
#: card, later for a payment method that settles in its own time.
PAID_SESSION = frozenset({"checkout.session.completed", "checkout.session.async_payment_succeeded"})
#: How Stripe reports a new subscription until its first payment is confirmed.
NOT_YET = "incomplete"
#: How long an unpaid Checkout session can still take a payment: Stripe ends
#: one a day after it opens.
SESSION_LIFE = timedelta(hours=25)
#: The most other sessions ended at Stripe for one request.
END_AT_MOST = 10
#: Second subscriptions the operator has been mailed about, by this process.
_told_twice: set[tuple[str, str]] = set()

_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Tests replace Stripe with a fake transport."""
    global _transport
    _transport = transport


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def enabled() -> bool:
    return all(_env(n) for n in ("OKWAN_BILLING_STRIPE_SECRET_KEY",
                                 "OKWAN_BILLING_STRIPE_WEBHOOK_SECRET",
                                 "OKWAN_BILLING_PRO_PRICE_ID"))


def require_terms() -> bool:
    """Whether Checkout makes the customer tick agreement to the Terms and
    the refund policy. Off unless the operator says so: Stripe refuses the
    session if the Terms' address is not in the account's public details."""
    return _env("OKWAN_BILLING_REQUIRE_TERMS").lower() in {"on", "true", "1", "yes"}


def dashboard_url() -> str:
    return (_env("OKWAN_DASHBOARD_URL") or "http://localhost:3000").rstrip("/")


async def _stripe(path: str, form: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(transport=_transport, timeout=20) as client:
        r = await client.post(f"{STRIPE_API}{path}", data=form,
                              headers={"Authorization": f"Bearer {_env('OKWAN_BILLING_STRIPE_SECRET_KEY')}"})
    try:
        answer = r.json() if r.content else {}
    except ValueError:
        answer = None          # an error page, not Stripe's JSON
    if r.status_code >= 400 or not isinstance(answer, dict):
        # Stripe's error type only: a message can echo request values.
        error = answer.get("error") if isinstance(answer, dict) else None
        kind = error.get("type", "error") if isinstance(error, dict) else "error"
        logger.error("billing: Stripe refused %s (%s %s)", path, r.status_code, kind)
        raise HTTPException(502, "the payment provider did not answer; try again in a moment")
    return answer


async def _stripe_ends(session_id: str) -> bool:
    """Ask Stripe to end an unpaid Checkout session. True only when Stripe
    answers with the session and says it is expired; False for anything
    else (it is already paid, already ended, or Stripe did not answer as
    expected). Never raises."""
    try:
        async with httpx.AsyncClient(transport=_transport, timeout=5) as client:
            r = await client.post(f"{STRIPE_API}/checkout/sessions/{quote(session_id, safe='')}/expire",
                                  headers={"Authorization": f"Bearer {_env('OKWAN_BILLING_STRIPE_SECRET_KEY')}"})
        answer = r.json() if r.status_code < 300 else None
    except (httpx.HTTPError, ValueError):
        return False
    return isinstance(answer, dict) and answer.get("status") == "expired"


async def end_other_sessions(tenant_id: str, *, keep: str, before: datetime | None = None) -> int:
    """One account, one payable checkout. Every other unpaid session the
    account still has open is ended at Stripe, and one Stripe confirms
    ended is dropped: it can never take a payment. Two tabs, or a second
    click, cannot then become two subscriptions and a month charged twice.
    `before`: only sessions opened earlier than this, so of several opened
    at once the newest stands."""
    store = get_store()
    oldest = datetime.now(UTC) - SESSION_LIFE
    others = [c["session_id"] for c in await store.checkouts_for(tenant_id)
              if c["paid_at"] is None and c["session_id"] != keep and c["opened_at"] >= oldest
              and (before is None or c["opened_at"] < before)][:END_AT_MOST]
    # Side by side, each with its own five seconds: the request or Stripe's
    # event that asked for this is not held for the sum of them.
    answers = await asyncio.gather(*(_stripe_ends(session) for session in others))
    ended = [session for session, gone in zip(others, answers, strict=True) if gone]
    for session in ended:
        await store.drop_checkout(tenant_id, session)
    return len(ended)


def _event_time(event: dict[str, Any]) -> datetime:
    """When Stripe made the event; now, if it does not say in a way that can be read."""
    stamp = event.get("created")
    if isinstance(stamp, int | float) and not isinstance(stamp, bool) and 0 < stamp < 4_102_444_800:
        return datetime.fromtimestamp(int(stamp), UTC)
    return datetime.now(UTC)


def _ref(value: Any) -> str:
    """A Stripe id, given as itself or as the object it names; "" otherwise."""
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) else ""


async def _paying_for_another(tenant_id: str, subscription_id: str) -> dict | None:
    """The account's billing record, when it already names a different
    subscription that is paying for Pro."""
    row = await get_store().billing_for(tenant_id)
    if (row and subscription_id and row["subscription_id"] and row["subscription_id"] != subscription_id
            and row["status"] in PAID):
        return row
    return None


async def _tell_paid_twice(tenant_id: str, kept: str, second: str, customer: str) -> None:
    """An account has two paying subscriptions. The record keeps the first;
    the operator is told once, to cancel and refund the second."""
    # The account only: Stripe's ids go in the operator's mail, not the log.
    logger.error("billing: %s is paying for a second subscription while its first is active", tenant_id)
    if (tenant_id, second) in _told_twice:
        return
    _told_twice.add((tenant_id, second))
    to, mailer = os.environ.get("OKWAN_SUPPORT_ESCALATION_TO", "").strip(), get_mailer()
    if not to or mailer is None:
        return
    email = await get_store().account_email(tenant_id) or "no sign-in address"
    text = (
        "An account has paid for Pro twice: two subscriptions are active for it at Stripe.\n\n"
        f"Account:  {tenant_id} ({email})\n"
        f"Kept:     {kept}, the one the dashboard's Manage billing opens\n"
        f"Second:   {second}" + (f", Stripe customer {customer}" if customer else "") + "\n\n"
        "In Stripe, cancel the second subscription now and refund its payment in full: the refund policy says a "
        "month charged twice is refunded. The account stays on Pro through the first.\n"
    )
    try:
        await mailer.send_notice(to, "[Billing] An account has paid for Pro twice", text)
    except Exception as exc:  # noqa: BLE001 — the event is acknowledged either way; the error line above stands
        logger.error("billing: second-subscription mail not sent: %s", type(exc).__name__)


def verify_signature(payload: bytes, header: str, secret: str, now: float | None = None) -> bool:
    """Stripe's scheme: HMAC-SHA256 of "<t>.<body>" with the endpoint's
    secret, in a `v1=` entry of the Stripe-Signature header, timestamped
    within the tolerance so an old event cannot be replayed."""
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        stamp = int(parts.get("t", [""])[0])
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - stamp) > SIGNATURE_TOLERANCE:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest().encode()
    # Bytes: compare_digest refuses a str with a character outside ASCII,
    # and a header can carry one. Such a signature is wrong, not an error.
    return any(hmac.compare_digest(expected, sig.encode("utf-8", "replace")) for sig in parts.get("v1", []))


async def session_owner(request: Request, authorization: str = Header(default=""),
                        x_okwan_key: str = Header(default="")) -> Any:
    """Billing is a person's decision: a dashboard session on the paying
    account, never an API key, and never a merchant under it.

    The account is the one the session itself belongs to. A request that
    carries an API key is refused whatever else it carries: the key would
    otherwise be the identity, with a session-shaped header beside it as
    decoration."""
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if x_okwan_key.strip() or not token.startswith(f"{accounts.SESSION_PREFIX}_"):
        raise HTTPException(403, "billing is managed from the dashboard")
    tenant = await get_store().tenant_for_session(accounts.hash_token(token))
    if tenant is None:
        raise HTTPException(401, "session expired — sign in again")
    access.remember_request(request, token)
    if tenant.parent_id is not None:
        raise HTTPException(403, "billing belongs to the account, not a merchant")
    return tenant


async def apply_subscription(tenant_id: str, sub: dict[str, Any]) -> str:
    """Record a subscription and set the plan it pays for. The plan."""
    store = get_store()
    status = str(sub.get("status") or "")
    plan = PRO if status in PAID else "free"
    end = sub.get("current_period_end") or (sub.get("items", {}).get("data") or [{}])[0].get("current_period_end")
    await store.save_billing(
        tenant_id, customer_id=str(sub.get("customer") or ""), subscription_id=str(sub.get("id") or ""),
        status=status, cancel_at_period_end=bool(sub.get("cancel_at_period_end")),
        current_period_end=datetime.fromtimestamp(int(end), UTC) if end else None)
    await store.set_plan(tenant_id, plan)
    return plan


async def _tenant_for(obj: dict[str, Any]) -> str | None:
    """Whose subscription this is: our own tenant id, put in the metadata at
    Checkout, else the customer we already linked. Only a root account.

    Never `client_reference_id`: on a Stripe payment link or pricing table
    that is a value the buyer writes into the address, so it would let a
    buyer name the account their payment is for. Metadata is set only with
    Okwan's Stripe key."""
    store = get_store()
    tenant_id = (obj.get("metadata") or {}).get("tenant_id")
    if not tenant_id and obj.get("customer"):
        tenant_id = await store.tenant_for_billing_customer(str(obj["customer"]))
    if not tenant_id:
        return None
    tenant = await store.get_tenant(str(tenant_id))
    return tenant.id if tenant is not None and tenant.parent_id is None else None


async def sweeper(interval: float = 3600.0, first_after: float = 90.0) -> None:
    """Hourly: forget unpaid checkout sessions more than a week old, whoever
    opened them. The privacy policy says a payment page opened and not paid
    on is kept about a week; an account that never comes back would
    otherwise keep its row."""
    await asyncio.sleep(first_after)
    while True:
        try:
            await get_store().sweep_checkouts()
        except Exception:  # a bad hour must not end the sweep
            logger.exception("billing: sweep of unpaid checkouts failed")
        await asyncio.sleep(interval)


def build_router() -> APIRouter:
    # Dashboard-only (an API key is refused), so not in the API reference.
    router = APIRouter(prefix="/v1/billing", include_in_schema=False)

    @router.get("")
    async def billing(tenant=Depends(session_owner)) -> dict[str, Any]:
        q = await quota_for(tenant)
        record = await get_store().billing_for(tenant.id)
        return {
            "enabled": enabled(), "plan": q.plan, "pro_price_usd": PRO_PRICE_USD,
            "subscription": None if record is None else {
                "status": record["status"], "cancel_at_period_end": record["cancel_at_period_end"],
                "current_period_end": record["current_period_end"].isoformat() if record["current_period_end"] else None,
            },
            "can_manage": record is not None and bool(record["customer_id"]),
            # The policy's own words for this page, shown ahead of the Upgrade button.
            "notice": policy.plan_page(), "refunds_url": policy.refunds_url(), "terms_url": policy.terms_url(),
        }

    @router.post("/checkout")
    async def checkout(request: Request, tenant=Depends(session_owner)) -> dict[str, str]:
        if not enabled():
            raise HTTPException(503, "card payments are not switched on yet")
        enforce(request, (BILLING_TENANT, tenant.id))
        store = get_store()
        if (await quota_for(tenant)).plan != "free":
            raise HTTPException(409, "this account is already on a paid plan")
        now = datetime.now(UTC)
        opened = [c for c in await store.checkouts_for(tenant.id)
                  if c["paid_at"] is None and c["opened_at"] > now - timedelta(days=1)]
        if len(opened) >= MAX_OPEN_CHECKOUTS:
            raise HTTPException(429, "too many checkouts opened today; finish one already open, or try tomorrow")
        record = await store.billing_for(tenant.id)
        app = dashboard_url()
        form = {
            "mode": "subscription",
            "line_items[0][price]": _env("OKWAN_BILLING_PRO_PRICE_ID"),
            "line_items[0][quantity]": "1",
            "client_reference_id": tenant.id,
            "metadata[tenant_id]": tenant.id,
            "subscription_data[metadata][tenant_id]": tenant.id,
            "success_url": f"{app}/settings?tab=plan&upgraded=1",
            "cancel_url": f"{app}/settings?tab=plan",
            "allow_promotion_codes": "true",
            # Beside the pay button, before the card is charged.
            "custom_text[submit][message]": policy.at_checkout(),
            # Which policy that was, kept by Stripe with the payment.
            "metadata[refund_policy]": policy.VERSION,
            "subscription_data[metadata][refund_policy]": policy.VERSION,
        }
        if require_terms():
            form["consent_collection[terms_of_service]"] = "required"
            form["custom_text[terms_of_service_acceptance][message]"] = policy.acceptance()
        if record and record["customer_id"]:
            form["customer"] = record["customer_id"]
        else:
            email = await store.account_email(tenant.id)
            if email:
                form["customer_email"] = email
        session = await _stripe("/checkout/sessions", form)
        # What this session shows, filed under the session's own id: the
        # webhook marks this row, and only this one, when it is paid.
        await store.open_checkout(tenant.id, str(session["id"]), policy_version=form["metadata[refund_policy]"],
                                  at=now, ip=client_ip(request))
        # And it is the only one that can be paid: the account's earlier
        # sessions (another tab, an earlier click) are ended at Stripe.
        await end_other_sessions(tenant.id, keep=str(session["id"]), before=now)
        return {"url": session["url"]}

    @router.post("/portal")
    async def portal(request: Request, tenant=Depends(session_owner)) -> dict[str, str]:
        if not enabled():
            raise HTTPException(503, "card payments are not switched on yet")
        enforce(request, (BILLING_TENANT, tenant.id))
        record = await get_store().billing_for(tenant.id)
        if record is None or not record["customer_id"]:
            raise HTTPException(409, "there is no billing to manage yet")
        session = await _stripe("/billing_portal/sessions", {
            "customer": record["customer_id"], "return_url": f"{dashboard_url()}/settings?tab=plan"})
        return {"url": session["url"]}

    @router.post("/webhook")
    async def webhook(request: Request, stripe_signature: str = Header(default="")) -> dict[str, Any]:
        secret = _env("OKWAN_BILLING_STRIPE_WEBHOOK_SECRET")
        payload = await request.body()
        if not secret or not verify_signature(payload, stripe_signature, secret):
            raise HTTPException(400, "signature check failed")
        event = json.loads(payload)
        kind = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}
        if (kind in PAID_SESSION and obj.get("mode") == "subscription"
                and obj.get("payment_status") in ("paid", "no_payment_required")):
            tenant_id = await _tenant_for(obj)
            store = get_store()
            session_id, subscription, customer = _ref(obj.get("id")), _ref(obj.get("subscription")), _ref(obj.get("customer"))
            if tenant_id and subscription:
                # The record first. This session was paid, whatever state the
                # subscription is in by the time this event is handled: Stripe
                # does not order its events, and one that is sent late, or
                # sent again by hand, still says which session took the money.
                when = _event_time(event)
                consent = obj.get("consent")
                ticked = isinstance(consent, dict) and consent.get("terms_of_service") == "accepted"
                marked = await store.checkout_paid(tenant_id, session_id, at=when, subscription_id=subscription,
                                                   terms_accepted_at=when if ticked else None)
                if marked is None:
                    # Paid, but not through a session this service opened, so
                    # there is no record of what it showed.
                    logger.warning("billing: paid checkout for %s has no record of what it showed", tenant_id)
                known = await store.billing_for(tenant_id)
                if marked is False and known and known["subscription_id"] and known["subscription_id"] != subscription:
                    # This session's payment was recorded before, and the
                    # account has since moved on to another subscription: an
                    # old event sent again. It changes nothing.
                    return {"received": True}
                # Paid: nothing else the account has open may take a second payment.
                await end_other_sessions(tenant_id, keep=session_id)
                first = await _paying_for_another(tenant_id, subscription)
                if first:
                    # It did anyway (two pages paid before either was heard
                    # of). The record keeps the first subscription, which is
                    # the one Manage billing opens; a person undoes the second.
                    await _tell_paid_twice(tenant_id, first["subscription_id"], subscription, customer)
                    return {"received": True, "plan": PRO, "second_subscription": True}
                if (known and known["subscription_id"] == subscription
                        and known["status"] not in PAID and known["status"] != NOT_YET):
                    # The subscription is already known to have ended: a late
                    # retry of this event must not bring Pro back. One that is
                    # only waiting for its first payment is not ended, and
                    # this event is that payment.
                    return {"received": True}
                await store.save_billing(
                    tenant_id, customer_id=customer, subscription_id=subscription, status="active",
                    cancel_at_period_end=False, current_period_end=None)
                await store.set_plan(tenant_id, PRO)
                return {"received": True, "plan": PRO}
        if kind in ("customer.subscription.created", "customer.subscription.updated",
                    "customer.subscription.deleted"):
            tenant_id = await _tenant_for(obj)
            if tenant_id:
                first = await _paying_for_another(tenant_id, str(obj.get("id") or ""))
                if first:
                    # About a subscription that is not the one on record while
                    # that one is paying: an older one ending late, or a second
                    # one. Neither changes the plan or takes over the record.
                    if kind != "customer.subscription.deleted" and str(obj.get("status") or "") in PAID:
                        await _tell_paid_twice(tenant_id, first["subscription_id"], str(obj.get("id") or ""),
                                               str(obj.get("customer") or ""))
                    return {"received": True, "plan": (await get_store().get_plan(tenant_id))[0]}
                if kind == "customer.subscription.deleted":
                    obj = {**obj, "status": "canceled"}
                return {"received": True, "plan": await apply_subscription(tenant_id, obj)}
        # Anything else, or an account we do not know: acknowledged, so
        # Stripe does not retry it forever.
        return {"received": True}

    return router
