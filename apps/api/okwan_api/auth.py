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
import logging
import os

from fastapi import Depends, Header, HTTPException

from okwan_vault import (
    EnvMasterKey, MemoryStore, PostgresStore, Store, from_env, new_key, resolver_for,
)
from okwan_vault.models import Tenant
from okwan_vault.usage import Quota, billing_root, month_start

logger = logging.getLogger(__name__)

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

    tenant = await get_store().tenant_for_key(key)
    if tenant is None:
        raise HTTPException(401, "invalid or revoked API key")
    return tenant


async def load_credentials(tenant: Tenant, connector_name: str, fields: tuple[str, ...]):
    """Fetch this tenant's credentials for one connector, once per request."""
    return await get_store().credentials_for(tenant.id, connector_name, fields)


async def quota_for(tenant: Tenant) -> Quota:
    """Month-to-date usage against the paying account's plan.

    An ISV's merchants roll up: the subtree shares one allowance, because
    the ISV is the customer and the merchants are not.
    """
    store = get_store()
    root = await billing_root(store, tenant.id)
    plan, limit = await store.get_plan(root)
    used = await store.usage_since(root, month_start())
    return Quota(plan=plan, limit=limit, used=used)


async def check_quota(tenant: Tenant = Depends(current_tenant)) -> Tenant:
    """Reject when the paying account is out of allowance.

    402 rather than 429: this is not a rate limit that resolves by waiting,
    it is a plan that needs upgrading.
    """
    quota = await quota_for(tenant)
    if quota.exceeded:
        raise HTTPException(
            402,
            f"monthly request limit reached — {quota.used}/{quota.limit} "
            f"on the {quota.plan} plan",
        )
    return tenant


async def meter(tenant: Tenant, surface: str) -> None:
    """Record one request. Never fails the request it is counting.

    A metering error is a billing problem, not a customer problem; losing
    a count is strictly better than 500-ing a call that already worked.
    """
    try:
        await get_store().record_request(tenant.id, surface)
    except Exception:  # noqa: BLE001
        logger.warning("usage not recorded for %s", tenant.id, exc_info=True)


def credentials_for(tenant: Tenant):
    """Synchronous resolver, for the in-memory store only."""
    return resolver_for(get_store(), tenant.id)
