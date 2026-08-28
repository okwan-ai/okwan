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

import inspect
import os

from fastapi import Header, HTTPException

from okwan_vault import (
    EnvMasterKey, MemoryStore, PostgresStore, Store, from_env, new_key, resolver_for,
)
from okwan_vault.models import Tenant

_store: Store | None = None


def get_store() -> Store:
    """Process-wide store.

    Durable when a vault database is configured, in-memory otherwise. The
    memory fallback generates an ephemeral master key so local runs and
    tests work without setup — safe only because nothing is persisted.
    A durable store refuses to start without a configured master, since
    an ephemeral key would seal secrets nobody could ever open again.
    """
    global _store
    if _store is None:
        _store = MemoryStore(_dev_master())
    return _store


def _dev_master():
    try:
        return from_env()
    except RuntimeError:
        if os.environ.get("OKWAN_ENV", "dev") != "dev":
            raise
        return EnvMasterKey(new_key())


async def open_store() -> Store:
    """Called at startup. Durable if OKWAN_VAULT_DATABASE_URL is set."""
    global _store
    dsn = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
    if not dsn:
        _store = MemoryStore(_dev_master())
        return _store
    _store = await PostgresStore(dsn, from_env()).connect()
    return _store


async def close_store() -> None:
    global _store
    if _store is not None and hasattr(_store, "close"):
        await _store.close()
    _store = None


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
    if inspect.isawaitable(tenant):
        tenant = await tenant
    if tenant is None:
        raise HTTPException(401, "invalid or revoked API key")
    return tenant


async def load_credentials(tenant: Tenant, connector_name: str, fields: tuple[str, ...]):
    """Fetch this tenant's credentials for one connector, once per request."""
    store = get_store()
    result = store.credentials_for(tenant.id, connector_name, fields)
    if inspect.isawaitable(result):
        result = await result
    return result


def credentials_for(tenant: Tenant):
    """Synchronous resolver, for the in-memory store only."""
    return resolver_for(get_store(), tenant.id)
