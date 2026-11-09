"""Connecting a system that has no key to type: Gusto.

A business's Gusto admin connects it from the dashboard. `authorize`
returns Gusto's approval link with a fresh `state`, remembered here for
ten minutes and bound to the tenant and to whoever asked. Gusto sends the
admin back to the dashboard, which hands `callback` the code and the
state. The callback checks the state is the one this caller started for
this tenant, swaps the code for the company's tokens with Okwan's own
client id and secret (okwan_gusto.auth), stores them in the tenant's
vault, and runs the same one-row test read as Test and Save. No token
leaves the server: the response says which company connected and what
the test read, nothing more.

Pending states live in this process, like the recon reuse cache: one API
instance today. A restart drops them, and the admin connects again.
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from okwan_core import get
from okwan_core.errors import CredentialError
from okwan_gusto import auth as gusto_auth
from okwan_gusto.connector import save_tokens
from pydantic import BaseModel, Field

from .admin import _guard, _probe
from .auth import admin_actor

logger = logging.getLogger(__name__)

#: How long a started connection may take: Gusto's own code lives ten minutes.
STATE_TTL = 600
_MAX_PENDING = 10_000

#: state → (tenant id, actor id, deadline on the monotonic clock)
_pending: dict[str, tuple[str, str, float]] = {}


def _remember(state: str, tenant_id: str, actor_id: str) -> None:
    now = time.monotonic()
    for key in [k for k, v in _pending.items() if v[2] < now]:
        del _pending[key]
    if len(_pending) >= _MAX_PENDING:
        _pending.clear()
    _pending[state] = (tenant_id, actor_id, now + STATE_TTL)


def _take(state: str, tenant_id: str, actor_id: str) -> bool:
    """The state, once: this caller started it, for this tenant, in time."""
    held = _pending.pop(state, None)
    return held is not None and held[:2] == (tenant_id, actor_id) and held[2] >= time.monotonic()


class CallbackIn(BaseModel):
    code: str = Field(min_length=1, max_length=512, description="The code Gusto sent back")
    state: str = Field(min_length=1, max_length=256, description="The state `authorize` returned")


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/tenants", tags=["tenants"])

    @router.post("/{tenant_id}/connectors/gusto/authorize")
    async def gusto_authorize(tenant_id: str, actor=Depends(admin_actor)) -> dict[str, Any]:
        """Start connecting Gusto for a tenant in the caller's subtree: the
        link to send its Gusto admin to, and the state to bring back."""
        await _guard(actor, tenant_id)
        if missing := gusto_auth.missing_settings():
            # The operator reads which setting, by name; the customer is
            # told only that Gusto isn't ready here.
            logger.warning("Connect Gusto refused: %s not set on this server", ", ".join(missing))
            raise HTTPException(503, "Gusto isn't set up on this Okwan server yet")
        state = secrets.token_urlsafe(32)
        _remember(state, tenant_id, actor.id)
        return {"url": gusto_auth.authorize_url(state), "state": state, "expires_in": STATE_TTL}

    @router.post("/{tenant_id}/connectors/gusto/callback")
    async def gusto_callback(tenant_id: str, body: CallbackIn, actor=Depends(admin_actor)) -> dict[str, Any]:
        """Finish connecting Gusto: store the company's tokens and test them.

        Refused unless the state is one this caller started for this
        tenant in the last ten minutes, so a code sent back to the wrong
        account, or replayed, connects nothing.
        """
        await _guard(actor, tenant_id)
        if not _take(body.state, tenant_id, actor.id):
            raise HTTPException(400, "This Gusto connection wasn't started here, or took over ten minutes. "
                                     "Connect Gusto again.")
        try:
            tokens = await gusto_auth.exchange_code(body.code)
            company = await gusto_auth.token_company(tokens.access_token)
        except CredentialError as exc:
            raise HTTPException(502, str(exc)) from None
        await save_tokens(tenant_id, tokens, company_uuid=company)
        from .recon_reuse import cache

        cache.forget(tenant_id)
        test = await _probe(tenant_id, get("gusto"))
        return {"connected": True, "connector": "gusto", "company_uuid": company, "test": test}

    return router
