"""An account's choices about the daily check, and the way out of its mail.

`GET` and `PUT /v1/tenants/{id}/notify` read and set whether the account's
merchants are checked once a day and whether a new finding is mailed
(schedule.py). They are the account's own settings: a merchant under it
has none.

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
from okwan_vault.models import unsubscribe_tenant
from pydantic import BaseModel, Field

from .admin import _guard
from .auth import admin_actor, get_store
from .ratelimit import UNSUBSCRIBE_IP, client_ip, enforce
from .schedule import FOLD, enabled, mail_blocked, needs

NOT_VALID = "This unsubscribe link isn't valid."


class NotifyIn(BaseModel):
    daily_checks: bool | None = Field(None, description="Check every connected merchant once a day")
    finding_emails: bool | None = Field(None, description="Mail the account when a daily check finds something new")


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

    return {
        "daily_checks": settings["daily_checks"],
        "finding_emails": settings["finding_emails"],
        # Where the mail goes, and whether this server can send it at all.
        "email": await get_store().account_email(tenant_id),
        "emails_blocked": mail_blocked(),
        "paused_by_operator": not enabled(),
        "check": FOLD,
        "needs": list(needs(get_across(FOLD))),
    }


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["tenants"])

    @router.get("/tenants/{tenant_id}/notify")
    async def get_notify(tenant_id: str, actor=Depends(admin_actor)) -> dict[str, Any]:
        """Whether the account's merchants are checked daily, and whether a
        new finding is mailed. Both are on until the account turns them off."""
        await _account(actor, tenant_id)
        return await _view(tenant_id, await get_store().get_notify(tenant_id))

    @router.put("/tenants/{tenant_id}/notify")
    async def put_notify(tenant_id: str, body: NotifyIn, actor=Depends(admin_actor)) -> dict[str, Any]:
        """Turn the daily check, or only its mail, on or off for the account."""
        await _account(actor, tenant_id)
        settings = await get_store().set_notify(
            tenant_id, daily_checks=body.daily_checks, finding_emails=body.finding_emails)
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
