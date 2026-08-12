"""Cursor pagination — the SDK-standard envelope for list operations.

Connectors expose paginated lists as CursorPage[T]; the cursor is an
opaque string the caller passes back to continue. REST, SQL, and MCP
generators all understand this shape, so agents can page any
connector the same way.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CursorPageIn(BaseModel):
    """Standard inputs for cursor-paginated list operations."""

    limit: int = Field(default=25, ge=1, le=100, description="Page size")
    cursor: str | None = Field(
        default=None,
        description="Opaque cursor from a previous page's `next_cursor`",
    )


class CursorPage[T](BaseModel):
    """Standard envelope for cursor-paginated results."""

    items: list[T]
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` to fetch the next page"
    )
    has_more: bool = False
