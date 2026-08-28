"""Okwan API gateway — REST interface auto-generated from connectors.

Route shape: POST /v1/{connector}/{resource}/{operation}

Callers authenticate with an Okwan API key. Upstream credentials are
read from the vault server-side and never appear in a request: an ISV
supplies them once at onboarding rather than transmitting a live secret
key on every call. No connector may register a bespoke route —
everything flows from the SDK definition.
"""
from __future__ import annotations

import inspect
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

import okwan_paystack.connector  # noqa: F401  (registers the connector)
import okwan_postgres.connector  # noqa: F401  (registers the connector)
import okwan_shopify.connector  # noqa: F401  (registers the connector)
import okwan_stripe.connector  # noqa: F401  (registers the connector)
import okwan_whatsapp.connector  # noqa: F401  (registers the connector)
from okwan_core import (
    CredentialError,
    OkwanError,
    UpstreamError,
    all_connectors,
)
from okwan_core.connector import Connector, Operation, Resource
import okwan_recon.declarations  # noqa: F401  (registers reconciliations)
import okwan_query.declarations  # noqa: F401  (registers declared tables)
from okwan_api.admin import build_router as build_admin_router
from okwan_api.auth import (
    check_quota, close_store, current_tenant, load_credentials, meter, open_store,
)
from okwan_query.mcp_http import build_server as build_mcp_server
from okwan_query.rest import build_router as build_query_router
from okwan_recon.emitters.rest import build_router

def _transport_security():
    """DNS-rebinding protection for the MCP endpoint.

    There is no wildcard host value, so the boolean is the off switch.
    Disabled when no hosts are named: behind Render the Host header is
    terminated by the platform proxy before uvicorn sees it, and the
    check would reject every request while guarding nothing. Set
    OKWAN_ALLOWED_HOSTS once a domain is registered.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = [
        h.strip()
        for h in os.environ.get("OKWAN_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    ]
    if not hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"https://{h}" for h in hosts],
    )


_mcp = build_mcp_server()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Open the vault, then run the MCP session manager.

    Mounting an ASGI app does not run its lifespan, so the streamable
    HTTP transport's task group has to be entered here or every MCP
    request fails before reaching a handler.
    """
    await open_store()
    async with _mcp.session_manager.run():
        yield
    await close_store()


app = FastAPI(
    title="Okwan API",
    version="0.1.0",
    description="The data connectivity layer built for AI agents.",
    lifespan=lifespan,
)


class ConnectorInfo(BaseModel):
    name: str
    version: str
    description: str
    resources: dict[str, list[str]]


@app.get("/v1/connectors", response_model=list[ConnectorInfo])
async def list_connectors() -> list[ConnectorInfo]:
    return [
        ConnectorInfo(
            name=c.name,
            version=c.version,
            description=c.description,
            resources={r.name: sorted(r.operations) for r in c.resources.values()},
        )
        for c in all_connectors()
    ]


async def _credentials(connector: Connector, tenant) -> dict[str, str]:
    """Vault lookup for the authenticated tenant. Never from the request."""
    return await load_credentials(
        tenant, connector.name, connector.auth.required_fields
    )


def _mount(connector: Connector, resource: Resource, op: Operation) -> None:
    path = f"/v1/{connector.name}/{resource.name}/{op.name}"

    async def endpoint(body: BaseModel, tenant=Depends(check_quota)) -> Any:
        credentials = await _credentials(connector, tenant)
        try:
            connector.auth.validate(credentials)
            ctx = connector.context(credentials)
            try:
                result = await op.handler(ctx, body)
            finally:
                await ctx.client.aclose()
            await meter(tenant, f"rest:{connector.name}")
            return result
        except CredentialError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.body) from exc
        except OkwanError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # FastAPI introspects the signature; give it the real input model.
    endpoint.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter(
                "body",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=op.input_model,
            ),
            inspect.Parameter(
                "tenant",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=Depends(check_quota),
            ),
        ]
    )
    endpoint.__name__ = f"{connector.name}_{resource.name}_{op.name}"

    app.post(
        path,
        response_model=op.output_model,
        summary=op.description,
        tags=[connector.name],
        operation_id=endpoint.__name__,
    )(endpoint)


for _connector in all_connectors():
    for _resource, _op in _connector.iter_operations():
        _mount(_connector, _resource, _op)


# Reconciliations span connectors, so they mount as one router rather
# than per-connector routes. Registrations must be imported first.
app.include_router(build_router())
app.include_router(build_query_router())
app.include_router(build_admin_router())

# Hosted MCP. Mounted rather than run as a separate service so agents
# authenticate through the same vault the REST routes use — one tenant
# model, not two.
app.mount(
    "/mcp",
    _mcp.streamable_http_app(
        streamable_http_path="/",
        transport_security=_transport_security(),
    ),
)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Liveness plus vault reachability.

    Returns 503 when the credential store cannot be reached, so an
    instance that cannot serve any authenticated request is replaced
    rather than left answering 401 to everything.
    """
    from okwan_api.auth import get_store

    store = get_store()
    vault = "memory"
    pool = getattr(store, "_pool", None)
    if pool is not None:
        try:
            await pool.fetchval("SELECT 1")
            vault = "postgres"
        except Exception as exc:
            raise HTTPException(503, f"vault unreachable: {exc}") from exc

    return {
        "status": "ok",
        "vault": vault,
        "connectors": len(all_connectors()),
    }
