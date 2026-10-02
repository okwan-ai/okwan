"""Self-serve signup: an email address becomes a root tenant.

Reverses the CLI-only rule in §9 (2026-10-02). Root tenants were
CLI-only while there were no users, because an endpoint that mints
accounts was attack surface with nobody to serve. The open door is now
the point: onboarding run by hand does not reach the first ISV.

What keeps it a door rather than a hole:

* **Verified address first.** A signup owns nothing; the root tenant is
  created only when the emailed token comes back with the password that
  made it.
* **One tenant per address**, held by the accounts primary key.
* **No enumeration.** Signup answers the same way for a new address, a
  pending one and a registered one, and sign-in fails with one message
  and one cost whether or not the address exists — the same reasoning as
  404-not-403 on the tenant routes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from okwan_vault import accounts

from .auth import admin_actor, get_store
from .mail import dashboard_url, get_mailer
from .ratelimit import (
    SIGNIN_ADDRESS, SIGNIN_IP, SIGNUP_ADDRESS, SIGNUP_IP, VERIFY_IP, client_ip, enforce,
    off_loop,
)

_ACCEPTED = {
    "status": "pending",
    "detail": "if this address can be registered, a verification link is on its way",
}


class SignupIn(BaseModel):
    email: str = Field(max_length=254, pattern=accounts.EMAIL_PATTERN)
    password: str = Field(min_length=accounts.MIN_PASSWORD, max_length=accounts.MAX_PASSWORD)


class VerifyIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=256)


class SessionIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)


async def _session(tenant_id: str) -> dict[str, Any]:
    full, token_hash = accounts.new_token(accounts.SESSION_PREFIX)
    expires_at = accounts.expires(accounts.SESSION_TTL)
    await get_store().create_session(tenant_id, token_hash, expires_at)
    return {"session": full, "tenant_id": tenant_id, "expires_at": expires_at.isoformat()}


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["accounts"])

    @router.post("/signup", status_code=202)
    async def signup(body: SignupIn, request: Request) -> dict[str, Any]:
        """Start a signup. The account exists only once the email is verified."""
        mailer = get_mailer()
        if mailer is None:
            raise HTTPException(503, "signup is not open yet: email verification "
                                     "has no mail provider configured")
        email = accounts.normalize_email(body.email)
        # Limited by address whether or not it is registered, so a 429 is
        # not an existence oracle either.
        enforce(request, (SIGNUP_IP, client_ip(request)), (SIGNUP_ADDRESS, email))
        store = get_store()
        # Hash before the existence check so both branches cost the same.
        password_hash = await off_loop(accounts.hash_password, body.password)
        if await store.account_exists(email):
            return _ACCEPTED
        full, token_hash = accounts.new_token(accounts.VERIFY_PREFIX)
        await store.add_signup(
            email, password_hash, token_hash, accounts.expires(accounts.SIGNUP_TTL)
        )
        await mailer.send_verification(email, f"{dashboard_url()}/verify?token={full}")
        return _ACCEPTED

    @router.post("/signup/verify", status_code=201)
    async def verify(body: VerifyIn, request: Request) -> dict[str, Any]:
        """Create the root tenant and open a session.

        The password must be the one this signup was made with, so a link
        from a signup someone else started for this address does not
        produce an account they can sign into.
        """
        enforce(request, (VERIFY_IP, client_ip(request)))
        store = get_store()
        token_hash = accounts.hash_token(body.token)
        pending = await store.signup_for(token_hash)
        invalid = HTTPException(400, "this link is invalid, expired or already used "
                                     "— sign in, or sign up again")
        if not await off_loop(accounts.check_password, body.password,
                              pending and pending[1]):
            raise invalid
        tenant = await store.complete_signup(token_hash)
        if tenant is None:
            raise invalid
        return {"tenant": {"id": tenant.id, "name": tenant.name},
                **await _session(tenant.id)}

    @router.post("/sessions", status_code=201)
    async def sign_in(body: SessionIn, request: Request) -> dict[str, Any]:
        email = accounts.normalize_email(body.email)
        enforce(request, (SIGNIN_IP, client_ip(request)), (SIGNIN_ADDRESS, email))
        found = await get_store().account_login(email)
        if not await off_loop(accounts.check_password, body.password,
                              found and found[1]):
            raise HTTPException(401, "invalid email or password")
        return await _session(found[0])

    @router.delete("/sessions/current", status_code=204)
    async def sign_out(
        authorization: str = Header(default=""), _=Depends(admin_actor)
    ) -> None:
        token = authorization[7:].strip()
        await get_store().delete_session(accounts.hash_token(token))

    return router
