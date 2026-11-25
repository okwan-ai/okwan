"""An account's choices about the daily check and its mail, and the way
out of the mail.

`GET` and `PUT /v1/tenants/{id}/notify` read and set whether the account's
merchants are checked once a day, whether what is new on its open items
is mailed (schedule.py), after how many days an item open that long is
mailed once more, and the amount from which an open item is named in
every mail. They are the account's own settings: a merchant under it has
none.

`POST /v1/unsubscribe` turns the mail off with the token from a mail's
link and nothing else, because a person must be able to stop mail without
signing in. The token names its account and is compared inside that
account's scope; it can do one thing, turn the mail off, and anyone
signed in can turn it back on. The route answers the same 404 for a token
that was never issued, one for an account that is gone and one that is
malformed.
"""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from okwan_vault.models import AGING_DAYS_RANGE, LARGE_AMOUNT_MAX, unsubscribe_tenant
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .admin import _guard
from .auth import admin_actor, get_store
from .ratelimit import UNSUBSCRIBE_IP, client_ip, enforce
from .schedule import FOLD, enabled, mail_blocked, needs

NOT_VALID = "This unsubscribe link isn't valid."


class NotifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_checks: StrictBool | None = Field(None, description="Check every connected merchant once a day")
    finding_emails: StrictBool | None = Field(
        None, description="Mail the account, once a day at most, what is new on its open items")
    aging_days: StrictInt | None = Field(
        None, ge=AGING_DAYS_RANGE[0], le=AGING_DAYS_RANGE[1],
        description="An item open this many days is mailed once more (1 to 365; 7 unless set)")
    large_amount: StrictInt | None = Field(
        None, ge=0, le=LARGE_AMOUNT_MAX,
        description="An open item at or above this many whole units of its own currency is named in "
                    "every mail while it is open (0: none; 1,000 unless set)")


class UnsubscribeIn(BaseModel):
    # Bounded, and otherwise unjudged here: a token of the wrong shape gets
    # the same 404 as one that was never issued.
    token: str = Field(max_length=500, description="The token from a mail's unsubscribe link")


async def _account(actor, tenant_id: str):
    """The tenant, if the caller may administer it and it is an account."""
    await _guard(actor, tenant_id)
    tenant = await get_store().get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(404, f"no such tenant: {tenant_id}")
    if tenant.parent_id is not None:
        raise HTTPException(400, "Daily checks are set on the account, not on one merchant.")
    return tenant


async def _view(tenant_id: str, settings: dict) -> dict[str, Any]:
    from okwan_recon.registry import get_across

    tenant = await get_store().get_tenant(tenant_id)
    sandbox = tenant is not None and tenant.sandbox

    return {
        "daily_checks": settings["daily_checks"],
        "finding_emails": settings["finding_emails"],
        "aging_days": settings["aging_days"],
        "large_amount": settings["large_amount"],
        # Where the mail goes, and whether this server can send it at all.
        # A sandbox gets no mail, and its keys never learn the live account's
        # sign-in address (okwan_api/sandbox.py).
        "email": None if sandbox else await get_store().account_email(tenant_id),
        "emails_blocked": mail_blocked(),
        "paused_by_operator": not enabled(),
        "check": FOLD,
        "needs": list(needs(get_across(FOLD))),
    }


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["tenants"])

    @router.get("/tenants/{tenant_id}/notify")
    async def get_notify(tenant_id: str, actor=Depends(admin_actor)) -> dict[str, Any]:
        """Whether the account's merchants are checked daily, whether what
        is new on its open items is mailed, and the days and amount the mail
        goes by. Both are on until the account turns them off."""
        await _account(actor, tenant_id)
        return await _view(tenant_id, await get_store().get_notify(tenant_id))

    @router.put("/tenants/{tenant_id}/notify")
    async def put_notify(tenant_id: str, body: NotifyIn, actor=Depends(admin_actor)) -> dict[str, Any]:
        """Turn the daily check, or only its mail, on or off for the
        account, or change the days and amount the mail goes by. What is
        left out stays as it is."""
        await _account(actor, tenant_id)
        settings = await get_store().set_notify(
            tenant_id, daily_checks=body.daily_checks, finding_emails=body.finding_emails,
            aging_days=body.aging_days, large_amount=body.large_amount)
        return await _view(tenant_id, settings)

    @router.post("/unsubscribe", status_code=204, tags=["accounts"])
    async def unsubscribe(body: UnsubscribeIn, request: Request) -> None:
        """Stop the daily check's mail with the token from its link. No
        sign-in. The checks themselves go on; Settings turns either back."""
        enforce(request, (UNSUBSCRIBE_IP, client_ip(request)))
        tenant_id = unsubscribe_tenant(body.token)
        held = (await get_store().get_notify(tenant_id))["unsubscribe_token"] if tenant_id else None
        if not held or not hmac.compare_digest(held.encode(), body.token.encode()):
            raise HTTPException(404, NOT_VALID)
        await get_store().set_notify(tenant_id, finding_emails=False)

    return router
