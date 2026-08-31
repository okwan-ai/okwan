"""MCP auto-generation — every Okwan connector becomes an MCP server.

Zero per-connector MCP code is permitted. Tools are derived purely
from Connector metadata; tool names follow `{connector}_{resource}_{op}`.
Read-only operations carry `readOnlyHint` annotations so agent
runtimes can apply consent policies to writes.

Requires mcp >= 2.0 (MCPServer / add_tool API).
"""
from __future__ import annotations

import inspect
import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from okwan_core import Connector
from okwan_core.connector import Operation


def _tool_name(connector: Connector, resource_name: str, op_name: str) -> str:
    return f"{connector.name}_{resource_name}_{op_name}"


def _env_credentials(connector: Connector) -> dict[str, str]:
    """Credentials come from env vars OKWAN_{CONNECTOR}_{FIELD},
    keeping secrets out of tool arguments and agent context."""
    prefix = f"OKWAN_{connector.name.upper()}_"
    return {
        field: os.environ.get(f"{prefix}{field.upper()}", "")
        for field in connector.auth.required_fields
    }


def _make_tool_fn(connector: Connector, op: Operation):
    """Build an async function whose signature mirrors the operation's
    Pydantic input model, so MCPServer derives a flat, agent-friendly
    argument schema."""

    async def tool_fn(**kwargs: Any) -> dict[str, Any]:
        params = op.input_model.model_validate(kwargs)
        ctx = connector.context(_env_credentials(connector))
        try:
            result = await op.handler(ctx, params)
        finally:
            await ctx.client.aclose()
        return result.model_dump(mode="json")

    sig_params: list[inspect.Parameter] = []
    for fname, f in op.input_model.model_fields.items():
        sig_params.append(
            inspect.Parameter(
                fname,
                kind=inspect.Parameter.KEYWORD_ONLY,
                annotation=f.annotation,
                default=(
                    inspect.Parameter.empty if f.is_required() else f.default
                ),
            )
        )
    tool_fn.__signature__ = inspect.Signature(sig_params)  # type: ignore[attr-defined]
    tool_fn.__doc__ = op.description
    return tool_fn


def build_server(connector: Connector) -> MCPServer:
    """Build a ready-to-run MCP server for one connector."""
    server = MCPServer(
        name=f"okwan-{connector.name}",
        instructions=connector.description,
        version=connector.version,
    )
    for res, op in connector.iter_operations():
        server.add_tool(
            _make_tool_fn(connector, op),
            name=_tool_name(connector, res.name, op.name),
            description=(
                f"[{connector.name}/{res.name}] {op.description}"
            ),
            annotations=ToolAnnotations(
                readOnlyHint=op.is_read_only,
                destructiveHint=not op.is_read_only,
            ),
            structured_output=False,
        )
    return server


async def list_tool_names(connector: Connector) -> list[str]:
    """Convenience for tests and docs generation."""
    server = build_server(connector)
    return [t.name for t in await server.list_tools()]


async def run_stdio(connector: Connector) -> None:
    """Entry point: serve one connector over stdio for local agents."""
    server = build_server(connector)
    await server.run_stdio_async()
