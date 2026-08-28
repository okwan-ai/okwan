"""MCP server exposing federated SQL across every registered connector.

The other MCP tools let an agent call one operation on one connector.
This one lets it ask a question that spans them — the difference between
fetching data and querying it.

The catalog is surfaced as its own tool so an agent can discover what is
queryable before writing SQL, rather than guessing table names.
"""
from __future__ import annotations

import inspect
from typing import Any

from .catalog import catalog, missing_credentials
from okwan_core import CredentialError

from .guard import UnsafeStatement
from .session import DEFAULT_LIMIT, QuerySession


def catalog_payload(resolver=None) -> dict[str, Any]:
    """Every table, flagged by whether this deployment can actually fetch it.

    An agent that cannot tell a configured table from an unconfigured one
    has to discover the difference by failing, which teaches it to avoid
    the tool entirely. That is not hypothetical: the first live run listed
    stripe.charges, the fetch died on a missing secret_key, and the agent
    hand-wrote SQL against a connector it trusted instead.
    """
    tables = []
    unavailable = 0
    for t in catalog():
        missing = missing_credentials(t, resolver)
        if missing:
            unavailable += 1
        tables.append(
            {
                "name": t.qualified,
                "source": f"{t.connector}.{t.resource}.{t.operation}",
                "queryable": not missing,
                "missing_credentials": list(missing),
                "columns": [{"name": n, "type": k} for n, k in t.columns],
            }
        )
    return {
        "tables": tables,
        "queryable_count": len(tables) - unavailable,
        "unavailable_count": unavailable,
    }


def _describe_tool():
    async def okwan_describe_tables() -> dict[str, Any]:
        """List every SQL-queryable table and its columns.

        Call this before writing a query. Tables are named
        `connector.resource` and are backed by live API calls, not a
        warehouse — there is no sync lag and no historical data beyond
        what the upstream returns.
        """
        return catalog_payload()

    okwan_describe_tables.__signature__ = inspect.Signature([])  # type: ignore[attr-defined]
    return okwan_describe_tables


def _query_tool(max_records: int):
    async def okwan_query(sql: str, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """Run a read-only SQL query across connectors.

        Tables are `connector.resource` — call okwan_describe_tables first
        and use only tables marked queryable.
        Referenced tables are fetched live at query time; unreferenced ones
        are not called at all. SELECT and WITH only.
        """
        session = QuerySession(max_records=min(limit, max_records))
        try:
            return await session.query(sql)
        except (UnsafeStatement, CredentialError) as exc:
            return {"error": str(exc), "rows": [], "row_count": 0}
        finally:
            session.close()

    okwan_query.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter("sql", inspect.Parameter.KEYWORD_ONLY, annotation=str),
            inspect.Parameter(
                "limit", inspect.Parameter.KEYWORD_ONLY, annotation=int,
                default=DEFAULT_LIMIT,
            ),
        ]
    )
    return okwan_query


def build_server(max_records: int = DEFAULT_LIMIT):
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer(
        name="okwan-query",
        instructions=(
            "Federated SQL across live business systems. Tables are named "
            "connector.resource and are backed by API calls made at query "
            "time, so results reflect the upstream now. Read-only."
        ),
        version="0.1.0",
    )
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False)

    server.add_tool(
        _describe_tool(),
        name="okwan_describe_tables",
        description="[query] List queryable tables and their columns.",
        annotations=read_only,
        structured_output=False,
    )
    server.add_tool(
        _query_tool(max_records),
        name="okwan_query",
        description="[query] Run a read-only SQL query across connectors.",
        annotations=read_only,
        structured_output=False,
    )
    return server


async def run_stdio() -> None:
    await build_server().run_stdio_async()
