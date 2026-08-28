"""Hosted MCP: one server, every tenant.

The stdio servers read credentials from environment variables, which
makes them single-tenant by construction — fine on a laptop, wrong for a
hosted surface. Over HTTP the caller presents an Okwan API key and the
tenant is resolved per request from the same vault the REST gateway uses.

A tool handler that declares a Context parameter receives one;
`ctx.headers` carries the request headers over HTTP and is None on stdio.
The tenant therefore comes from the transport, never from a tool
argument — an argument could be forged by the agent, a header cannot be
without the key itself.
"""
from __future__ import annotations

import inspect
from typing import Any

from mcp.server.mcpserver.context import Context

from okwan_core import all_connectors

from .guard import UnsafeStatement
from .mcp import catalog_payload
from .session import DEFAULT_LIMIT, QuerySession


class Unauthenticated(Exception):
    """No usable Okwan API key on the request."""


def _bearer(ctx: Context) -> str:
    headers = ctx.headers
    if headers is None:
        raise Unauthenticated(
            "this server requires HTTP transport with an Okwan API key"
        )
    raw = headers.get("authorization") or headers.get("x-okwan-key") or ""
    key = raw[7:] if raw.lower().startswith("bearer ") else raw
    key = key.strip()
    if not key:
        raise Unauthenticated(
            "missing API key — send Authorization: Bearer okw_…"
        )
    return key


async def _tenant_resolver(ctx: Context):
    """Resolve the caller to a tenant and load their credentials once.

    Returns a synchronous resolver over a plain dict, matching the shape
    the SDK expects below this point, and keeping plaintext scoped to one
    tool call.
    """
    from okwan_api.auth import get_store, load_credentials

    store = get_store()
    tenant = store.tenant_for_key(_bearer(ctx))
    if inspect.isawaitable(tenant):
        tenant = await tenant
    if tenant is None:
        raise Unauthenticated("invalid or revoked API key")

    loaded: dict[tuple[str, str], str] = {}
    for connector in all_connectors():
        creds = await load_credentials(
            tenant, connector.name, connector.auth.required_fields
        )
        for field, value in creds.items():
            loaded[(connector.name, field)] = value

    def resolve(connector_name: str, fields: tuple[str, ...]) -> dict[str, str]:
        return {f: loaded.get((connector_name, f), "") for f in fields}

    return resolve


def build_server(max_records: int = DEFAULT_LIMIT):
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer(
        name="okwan",
        instructions=(
            "Federated SQL across live business systems. Tables are named "
            "connector.resource and are backed by API calls made at query "
            "time, so results reflect the upstream now. Read-only. "
            "Authenticate with an Okwan API key as a bearer token."
        ),
        version="0.1.0",
    )
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False)

    async def okwan_describe_tables(ctx: Context) -> dict[str, Any]:
        """List every SQL-queryable table and its columns.

        Call this before writing a query. Tables marked not queryable are
        connectors this account has not configured; the response names
        the missing credential fields.
        """
        try:
            return catalog_payload(await _tenant_resolver(ctx))
        except Unauthenticated as exc:
            return {"error": str(exc), "tables": []}

    async def okwan_query(
        ctx: Context, sql: str, limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        """Run a read-only SQL query across connectors.

        Tables are `connector.resource` — call okwan_describe_tables first
        and use only tables marked queryable. Referenced tables are
        fetched live; unreferenced ones are not called at all. SELECT and
        WITH only.
        """
        try:
            resolver = await _tenant_resolver(ctx)
        except Unauthenticated as exc:
            return {"error": str(exc), "rows": [], "row_count": 0}

        session = QuerySession(resolver=resolver, max_records=min(limit, max_records))
        try:
            return await session.query(sql)
        except UnsafeStatement as exc:
            return {"error": str(exc), "rows": [], "row_count": 0}
        except Exception as exc:  # noqa: BLE001 — surfaced to the agent as data
            return {"error": f"{type(exc).__name__}: {exc}", "rows": [], "row_count": 0}
        finally:
            session.close()

    server.add_tool(
        okwan_describe_tables,
        name="okwan_describe_tables",
        description="[okwan] List queryable tables and their columns.",
        annotations=read_only,
        structured_output=False,
    )
    server.add_tool(
        okwan_query,
        name="okwan_query",
        description="[okwan] Run a read-only SQL query across connectors.",
        annotations=read_only,
        structured_output=False,
    )
    return server
