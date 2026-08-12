"""MCP auto-generation — every Okwan connector becomes an MCP server.

Zero per-connector MCP code is permitted. Tools are derived purely
from Connector metadata; tool names follow `{connector}_{resource}_{op}`.
Read-only operations are annotated as such so agent runtimes can apply
consent policies to writes.
"""
from __future__ import annotations

import json
import os
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from okwan_core import Connector


def _tool_name(connector: Connector, resource_name: str, op_name: str) -> str:
    return f"{connector.name}_{resource_name}_{op_name}"


def build_tools(connector: Connector) -> list[Tool]:
    tools: list[Tool] = []
    for res, op in connector.iter_operations():
        schema = op.input_model.model_json_schema()
        # Inline $defs for maximum client compatibility.
        schema.setdefault("type", "object")
        tools.append(
            Tool(
                name=_tool_name(connector, res.name, op.name),
                description=(
                    f"[{connector.name}/{res.name}] {op.description} "
                    f"({'read-only' if op.is_read_only else 'WRITE'})"
                ),
                inputSchema=schema,
            )
        )
    return tools


def build_server(connector: Connector) -> Server:
    """Build a ready-to-run MCP server for one connector.

    Credentials are read from environment variables named
    OKWAN_{CONNECTOR}_{FIELD} (e.g. OKWAN_WHATSAPP_ACCESS_TOKEN),
    keeping secrets out of tool arguments and agent context.
    """
    server: Server = Server(f"okwan-{connector.name}")
    index: dict[str, tuple[Any, Any]] = {
        _tool_name(connector, res.name, op.name): (res, op)
        for res, op in connector.iter_operations()
    }

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return build_tools(connector)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name not in index:
            raise ValueError(f"unknown tool: {name}")
        _res, op = index[name]
        prefix = f"OKWAN_{connector.name.upper()}_"
        credentials = {
            field: os.environ.get(f"{prefix}{field.upper()}", "")
            for field in connector.auth.required_fields
        }
        params = op.input_model.model_validate(arguments)
        ctx = connector.context(credentials)
        try:
            result = await op.handler(ctx, params)
        finally:
            await ctx.client.aclose()
        return [
            TextContent(type="text", text=json.dumps(result.model_dump(), default=str))
        ]

    return server


async def run_stdio(connector: Connector) -> None:
    """Entry point: serve one connector over stdio for local agents."""
    from mcp.server.stdio import stdio_server

    server = build_server(connector)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
