"""Tenant provisioning for platforms.

An ISV holds one account and provisions a tenant per merchant. These
routes are the API version of what the CLI does, scoped by the same
boundary: a tenant may act on itself and its descendants, nothing else.

Root tenants are created by self-serve signup (`signup.py`), gated on
a verified email. They were CLI-only until §9 2026-10-02: an endpoint
that mints root accounts was an open door with no user. Once there are
users, the open door is the point, and hand-run onboarding is what stood
between the product and the first ISV.

Every route here accepts an API key or a dashboard session. Anything
outside the caller's subtree answers 404, never 403.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from okwan_core import OkwanError, UpstreamError, all_connectors
from okwan_core import get as get_connector
from okwan_vault.authz import Forbidden, require_administer

from .auth import admin_actor, get_store, meter
from .ratelimit import TEST_IP, TEST_TENANT, client_ip, enforce


class CreateTenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200,
                      description="Merchant or workspace name")


class CredentialIn(BaseModel):
    connector: str = Field(description="Connector name, e.g. 'stripe'")
    field: str = Field(description="Credential field, e.g. 'secret_key'")
    value: str = Field(min_length=1, description="Written to the vault, never returned")


async def _guard(actor, target_id: str) -> None:
    try:
        await require_administer(get_store(), actor.id, target_id)
    except Forbidden as exc:
        # 404 rather than 403: a tenant outside the caller's subtree should
        # not be distinguishable from one that does not exist, or the API
        # becomes an oracle for enumerating other customers' tenant ids.
        raise HTTPException(404, f"no such tenant: {target_id}") from exc


def _public(tenant) -> dict[str, Any]:
    return {
        "id": tenant.id,
        "name": tenant.name,
        "parent_id": tenant.parent_id,
        "created_at": tenant.created_at.isoformat(),
    }


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/tenants", tags=["tenants"])

    @router.get("")
    async def list_tenants(actor=Depends(admin_actor)) -> dict[str, Any]:
        """The caller's own record and the tenants it has provisioned."""
        children = await get_store().children_of(actor.id)
        return {"self": _public(actor), "children": [_public(c) for c in children]}

    @router.post("", status_code=201)
    async def create_tenant(
        body: CreateTenantIn, actor=Depends(admin_actor)
    ) -> dict[str, Any]:
        """Provision a tenant beneath the caller."""
        tenant = await get_store().create_tenant(body.name, parent_id=actor.id)
        return _public(tenant)

    @router.post("/{tenant_id}/keys", status_code=201)
    async def issue_key(
        tenant_id: str, actor=Depends(admin_actor)
    ) -> dict[str, Any]:
        """Issue an API key for a tenant in the caller's subtree.

        The secret is returned exactly once; only its hash is stored.
        """
        await _guard(actor, tenant_id)
        full, record = await get_store().issue_key(tenant_id)
        return {
            "key_id": record.id,
            "prefix": record.prefix,
            "secret": full,
            "note": "shown once — store it now",
        }

    @router.delete("/keys/{key_id}", status_code=204)
    async def revoke_key(key_id: str, actor=Depends(admin_actor)) -> None:
        """Revoke a key. Effective immediately on the next request."""
        store = get_store()
        owner = await store.key_owner(key_id)
        missing = HTTPException(404, f"no such key: {key_id}")
        if owner is None:
            raise missing
        try:
            await _guard(actor, owner)
            await store.revoke_key(key_id)
        except (HTTPException, KeyError) as exc:
            # Someone else's key reads exactly like a key that never existed.
            raise missing from exc

    @router.put("/{tenant_id}/credentials", status_code=204)
    async def put_credential(
        tenant_id: str, body: CredentialIn, actor=Depends(admin_actor)
    ) -> None:
        """Store an upstream credential for a tenant in the caller's subtree.

        Validated against the connector's declared fields, so a typo fails
        here rather than surfacing later as an unexplained auth error.
        """
        await _guard(actor, tenant_id)
        connector = _connector(body.connector, status=400)
        if body.field not in connector.auth.required_fields:
            raise HTTPException(
                400,
                f"{body.connector} takes "
                f"{', '.join(connector.auth.required_fields)}",
            )
        await get_store().put_credential(
            tenant_id, body.connector, body.field, body.value
        )

    @router.get("/{tenant_id}/credentials")
    async def list_credentials(
        tenant_id: str, actor=Depends(admin_actor)
    ) -> dict[str, Any]:
        """Which connectors are configured. Names only — never values."""
        await _guard(actor, tenant_id)
        configured = await get_store().connectors_configured(tenant_id)
        return {"tenant_id": tenant_id, "configured": configured}

    @router.post("/{tenant_id}/connectors/{connector_name}/test")
    async def test_connector(
        tenant_id: str, connector_name: str, request: Request,
        actor=Depends(admin_actor),
    ) -> dict[str, Any]:
        """Prove the stored credentials work: one real list call, limit=1.

        Reads from the vault, not the request, so a pass also proves the
        write landed and the round trip opens. The result says whether
        rows came back and never carries a credential, even inside an
        upstream error message.
        """
        await _guard(actor, tenant_id)
        connector = _connector(connector_name, status=404)
        # After the guard: a tenant outside the subtree is a 404, never a 429.
        enforce(request, (TEST_IP, client_ip(request)), (TEST_TENANT, tenant_id))
        return await _probe(tenant_id, connector)

    return router


def _connector(name: str, status: int):
    try:
        return get_connector(name)
    except KeyError:
        known = ", ".join(sorted(c.name for c in all_connectors()))
        raise HTTPException(status, f"unknown connector; known: {known}") from None


async def _probe(tenant_id: str, connector) -> dict[str, Any]:
    out: dict[str, Any] = {"connector": connector.name}
    probe = connector.probe()
    if probe is None:
        return {**out, "status": "untestable",
                "detail": "every list operation needs an argument only you know, "
                          "so there is no blind read to test with"}
    resource, op, params = probe
    out["operation"] = f"{connector.name}.{resource.name}.{op.name}"

    fields = connector.auth.required_fields
    creds = await get_store().credentials_for(tenant_id, connector.name, fields)
    missing = [f for f in fields if not creds.get(f)]
    if missing:
        return {**out, "status": "missing", "missing": missing,
                "detail": f"not stored yet: {', '.join(missing)}"}
    secrets = [v for v in creds.values() if v]
    try:
        ctx = connector.context(creds)
        try:
            result = await op.handler(ctx, params)
        finally:
            await ctx.client.aclose()
    except UpstreamError as exc:
        return {**out, "status": "failed", "upstream_status": exc.status,
                "detail": _scrub(str(exc), secrets)}
    except OkwanError as exc:
        return {**out, "status": "failed", "detail": _scrub(str(exc), secrets)}
    except Exception as exc:  # noqa: BLE001 — transport errors are a result here
        return {**out, "status": "failed",
                "detail": _scrub(f"{type(exc).__name__}: {exc}", secrets)}
    finally:
        del creds, secrets
    await meter_test(tenant_id, connector.name)
    rows = len(getattr(result, "items", None) or [])
    start, end = getattr(result, "span_start", None), getattr(result, "span_end", None)
    if start is not None and end is not None:
        # A windowed rail (PayPal reads 30 days by default) can be empty
        # because the window is, not because the account is. Say which.
        out["span"] = {"start": start.isoformat(), "end": end.isoformat()}
    if rows:
        detail = "real rows came back"
    elif "span" in out:
        detail = (f"the credentials work; no rows between {start:%Y-%m-%d} "
                  f"and {end:%Y-%m-%d}, the window this list reads by default")
    else:
        detail = "the credentials work, but this list returned no rows"
    return {**out, "status": "rows" if rows else "empty", "rows": rows, "detail": detail}


async def meter_test(tenant_id: str, connector: str) -> None:
    tenant = await get_store().get_tenant(tenant_id)
    if tenant is not None:
        await meter(tenant, f"test:{connector}")


def _scrub(text: str, secrets: list[str]) -> str:
    """Remove any credential an upstream error echoed back.

    Some rails quote the key they rejected; a connection error can carry
    a DSN. The value must not leave through an error message any more
    than through a success.
    """
    for value in secrets:
        if len(value) >= 4:
            text = text.replace(value, "[redacted]")
    return text[:300]
