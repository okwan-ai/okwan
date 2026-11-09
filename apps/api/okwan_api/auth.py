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

import logging
import os

from fastapi import Depends, Header, HTTPException, Request
from okwan_vault import (
    ADMIN,
    EnvMasterKey,
    MemoryStore,
    PostgresStore,
    Store,
    accounts,
    from_env,
    new_key,
    resolver_for,
)
from okwan_vault.models import Tenant
from okwan_vault.usage import Quota, quota

from . import access

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
    request: Request,
    authorization: str = Header(default=""),
    x_okwan_key: str = Header(default=""),
) -> Tenant:
    """Resolve the caller's tenant, or 401.

    Accepts `Authorization: Bearer okw_…` or `X-Okwan-Key: okw_…`. The
    key's public prefix and the caller's address are kept for the access
    log, which `meter` writes once a read has succeeded.
    """
    tenant, _ = await _key_access(request, authorization, x_okwan_key)
    return tenant


#: What a read-only key is told at an administration route.
READ_ONLY = ("This API key is read-only: it reads data through the API, SQL and MCP, and cannot manage "
             "merchants, keys, connections or files. Use the dashboard, or a key issued with admin access.")


async def _key_access(request: Request, authorization: str, x_okwan_key: str) -> tuple[Tenant, str]:
    """The key's tenant and scope, or 401."""
    key = x_okwan_key.strip()
    if not key and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    if not key:
        raise HTTPException(
            401, "missing API key — send Authorization: Bearer okw_… or X-Okwan-Key"
        )

    found = await get_store().access_for_key(key)
    if found is None:
        raise HTTPException(401, "invalid or revoked API key")
    access.remember_request(request, key)
    return found


async def admin_actor(
    request: Request,
    authorization: str = Header(default=""),
    x_okwan_key: str = Header(default=""),
) -> Tenant:
    """A dashboard session or an admin API key, for the admin routes only.

    Data routes and the hosted MCP stay on `current_tenant`, so a session
    cannot reach them; those take a key the customer chose to issue. A
    session reads a rail only through the admin routes built for the
    dashboard: the connection test and the reconciliation runs, each
    subtree-guarded and metered to the target. The runs also pass the
    same plan gate as `check_quota`.

    A read-only key is refused here with 403, whatever the route: one
    handed to an AI client or a reporting job can read, but cannot add a
    merchant, issue or revoke a key, replace a saved credential or delete
    an import.
    """
    token = x_okwan_key.strip()
    if not token and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token.startswith(f"{accounts.SESSION_PREFIX}_"):
        tenant = await get_store().tenant_for_session(accounts.hash_token(token))
        if tenant is None:
            raise HTTPException(401, "session expired — sign in again")
        access.remember_request(request, token)
        return tenant
    tenant, scope = await _key_access(request, authorization, x_okwan_key)
    if scope != ADMIN:
        raise HTTPException(403, READ_ONLY)
    return tenant


async def load_credentials(tenant: Tenant, connector_name: str, fields: tuple[str, ...]):
    """Fetch this tenant's credentials for one connector, once per request."""
    return await get_store().credentials_for(tenant.id, connector_name, fields)


async def quota_for(tenant: Tenant) -> Quota:
    """Month-to-date usage against the paying account's plan.

    An ISV's merchants roll up: the subtree shares one allowance, because
    the ISV is the customer and the merchants are not. The arithmetic is
    `okwan_vault.usage.quota`, which the operator's `plan show` also reads.
    """
    return await quota(get_store(), tenant.id)


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
            f"on the {quota.plan} plan. Upgrade in the dashboard under "
            "Settings → Plan & usage, or wait for the 1st of the month",
        )
    return tenant


async def meter(tenant: Tenant, surface: str, target: str | None = None) -> None:
    """Record one request, and who made it. Never fails the request it is
    counting.

    A metering error is a billing problem, not a customer problem; losing
    a count is strictly better than 500-ing a call that already worked.
    The access row (`access.record`) names the caller remembered at
    authentication and what was read: the route, or `target` where the
    route alone does not say (the hosted MCP server's tools).
    """
    try:
        await get_store().record_request(tenant.id, surface)
    except Exception:
        logger.warning("usage not recorded for %s", tenant.id, exc_info=True)
    await access.record(tenant, surface, target)


def credentials_for(tenant: Tenant):
    """Synchronous resolver, for the in-memory store only."""
    return resolver_for(get_store(), tenant.id)
