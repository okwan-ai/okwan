"""Request authentication and per-tenant credential resolution.

Replaces the v0 model where a caller sent upstream credentials as request
headers. That required an ISV to transmit their live Stripe secret key to
this server on every call, which no security review would pass and which
no customer should agree to.

Now a caller presents an Okwan API key, the gateway resolves which tenant
it belongs to, and upstream credentials are read from the vault
server-side. The customer's secrets are supplied once at onboarding and
never travel again.
"""
from __future__ import annotations

import os

from fastapi import Header, HTTPException

from okwan_vault import EnvMasterKey, MemoryStore, Store, from_env, new_key, resolver_for
from okwan_vault.models import Tenant

_store: Store | None = None


def get_store() -> Store:
    """Process-wide store. Memory-backed until a durable one is wired.

    The master key falls back to an ephemeral one when unset so tests and
    local runs work; that is safe only because nothing is persisted, and
    a durable store must refuse to start without a configured master.
    """
    global _store
    if _store is None:
        try:
            master = from_env()
        except RuntimeError:
            if os.environ.get("OKWAN_ENV", "dev") != "dev":
                raise
            master = EnvMasterKey(new_key())
        _store = MemoryStore(master)
    return _store


def set_store(store: Store) -> None:
    """Injection point for tests and for a durable implementation."""
    global _store
    _store = store


async def current_tenant(
    authorization: str = Header(default=""),
    x_okwan_key: str = Header(default=""),
) -> Tenant:
    """Resolve the caller's tenant, or 401.

    Accepts `Authorization: Bearer okw_…` or `X-Okwan-Key: okw_…`.
    """
    key = x_okwan_key.strip()
    if not key and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    if not key:
        raise HTTPException(
            401, "missing API key — send Authorization: Bearer okw_… or X-Okwan-Key"
        )

    tenant = get_store().tenant_for_key(key)
    if tenant is None:
        raise HTTPException(401, "invalid or revoked API key")
    return tenant


def credentials_for(tenant: Tenant):
    """A CredentialResolver bound to the authenticated tenant."""
    return resolver_for(get_store(), tenant.id)
