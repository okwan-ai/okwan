"""Resolve a ResourceRef to rows by driving the connector SDK.

Pages through the SDK's CursorPage envelope, which every connector
speaks regardless of how its upstream actually pages. Credentials are
resolved per connector by an injected resolver so the same fetch path
serves MCP (env vars) and REST (request headers).
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from okwan_core import get as get_connector

from .declaration import ResourceRef

Row = dict[str, Any]
CredentialResolver = Callable[[str, tuple[str, ...]], dict[str, str]]

PAGE_SIZE = 100


def env_credentials(connector_name: str, fields: tuple[str, ...]) -> dict[str, str]:
    """Default resolver: OKWAN_{CONNECTOR}_{FIELD} environment variables.

    Same convention the MCP generator uses, so secrets never travel
    through tool arguments or agent context.
    """
    prefix = f"OKWAN_{connector_name.upper()}_"
    return {f: os.environ.get(f"{prefix}{f.upper()}", "") for f in fields}


def _is_page(result: Any) -> bool:
    """CursorPage: pageable, carries a cursor."""
    return hasattr(result, "items") and hasattr(result, "next_cursor")


def _unwrap_container(result: Any) -> list[Row] | None:
    """Non-paged list containers: RowSet.rows, TableList.items, ...

    Returns None when the result is a single record rather than a
    collection, so the caller can append it directly.
    """
    for attr in ("rows", "items"):
        value = getattr(result, attr, None)
        if isinstance(value, list):
            return [
                v.model_dump(mode="json") if hasattr(v, "model_dump") else dict(v)
                for v in value
            ]
    return None


async def fetch_rows(
    ref: ResourceRef,
    resolver: CredentialResolver = env_credentials,
    max_records: int = 500,
    overrides: dict[str, Any] | None = None,
) -> list[Row]:
    connector = get_connector(ref.connector)
    op = connector.resources[ref.resource].operations[ref.operation]
    fields = op.input_model.model_fields

    ctx = connector.context(resolver(connector.name, connector.auth.required_fields))
    rows: list[Row] = []
    cursor: str | None = None
    try:
        while True:
            payload: dict[str, Any] = {**ref.params, **(overrides or {})}
            payload = {k: v for k, v in payload.items() if k in fields}
            if "limit" in fields:
                payload["limit"] = min(PAGE_SIZE, max_records - len(rows))
            if "cursor" in fields and cursor is not None:
                payload["cursor"] = cursor

            result = await op.handler(ctx, op.input_model.model_validate(payload))

            if not _is_page(result):
                container = _unwrap_container(result)
                if container is None:
                    rows.append(result.model_dump(mode="json"))
                else:
                    rows.extend(container)
                break

            rows.extend(item.model_dump(mode="json") for item in result.items)
            cursor = result.next_cursor
            if not cursor or not result.has_more or len(rows) >= max_records:
                break
    finally:
        await ctx.client.aclose()

    return rows[:max_records]
