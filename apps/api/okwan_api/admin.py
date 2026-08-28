"""Tenant provisioning for platforms.

An ISV holds one account and provisions a tenant per merchant. These
routes are the API version of what the CLI does, scoped by the same
boundary: a tenant may act on itself and its descendants, nothing else.

Deliberately absent: a route to create a root tenant. Signing up an ISV
is a commercial act, not an anonymous one, and an endpoint that mints
root accounts is an open door with no user until self-serve billing
exists. Root tenants come from the CLI.
"""
from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from okwan_core import all_connectors, get as get_connector
from okwan_vault.authz import Forbidden, require_administer

from .auth import current_tenant, get_store


class CreateTenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200,
                      description="Merchant or workspace name")


class CredentialIn(BaseModel):
    connector: str = Field(description="Connector name, e.g. 'stripe'")
    field: str = Field(description="Credential field, e.g. 'secret_key'")
    value: str = Field(min_length=1, description="Written to the vault, never returned")


async def _maybe(value):
    return await value if inspect.isawaitable(value) else value


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
    async def list_tenants(actor=Depends(current_tenant)) -> dict[str, Any]:
        """The caller's own record and the tenants it has provisioned."""
        children = await _maybe(get_store().children_of(actor.id))
        return {"self": _public(actor), "children": [_public(c) for c in children]}

    @router.post("", status_code=201)
    async def create_tenant(
        body: CreateTenantIn, actor=Depends(current_tenant)
    ) -> dict[str, Any]:
        """Provision a tenant beneath the caller."""
        tenant = await _maybe(
            get_store().create_tenant(body.name, parent_id=actor.id)
        )
        return _public(tenant)

    @router.post("/{tenant_id}/keys", status_code=201)
    async def issue_key(
        tenant_id: str, actor=Depends(current_tenant)
    ) -> dict[str, Any]:
        """Issue an API key for a tenant in the caller's subtree.

        The secret is returned exactly once; only its hash is stored.
        """
        await _guard(actor, tenant_id)
        full, record = await _maybe(get_store().issue_key(tenant_id))
        return {
            "key_id": record.id,
            "prefix": record.prefix,
            "secret": full,
            "note": "shown once — store it now",
        }

    @router.delete("/keys/{key_id}", status_code=204)
    async def revoke_key(key_id: str, actor=Depends(current_tenant)) -> None:
        """Revoke a key. Effective immediately on the next request."""
        store = get_store()
        try:
            await _maybe(store.revoke_key(key_id))
        except KeyError as exc:
            raise HTTPException(404, f"no such key: {key_id}") from exc

    @router.put("/{tenant_id}/credentials", status_code=204)
    async def put_credential(
        tenant_id: str, body: CredentialIn, actor=Depends(current_tenant)
    ) -> None:
        """Store an upstream credential for a tenant in the caller's subtree.

        Validated against the connector's declared fields, so a typo fails
        here rather than surfacing later as an unexplained auth error.
        """
        await _guard(actor, tenant_id)
        try:
            connector = get_connector(body.connector)
        except KeyError:
            known = ", ".join(sorted(c.name for c in all_connectors()))
            raise HTTPException(400, f"unknown connector; known: {known}") from None
        if body.field not in connector.auth.required_fields:
            raise HTTPException(
                400,
                f"{body.connector} takes "
                f"{', '.join(connector.auth.required_fields)}",
            )
        await _maybe(
            get_store().put_credential(
                tenant_id, body.connector, body.field, body.value
            )
        )

    @router.get("/{tenant_id}/credentials")
    async def list_credentials(
        tenant_id: str, actor=Depends(current_tenant)
    ) -> dict[str, Any]:
        """Which connectors are configured. Names only — never values."""
        await _guard(actor, tenant_id)
        configured = await _maybe(get_store().connectors_configured(tenant_id))
        return {"tenant_id": tenant_id, "configured": configured}

    return router
