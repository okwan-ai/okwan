"""Cursor pagination — the SDK-standard envelope for list operations.

Connectors expose paginated lists as CursorPage[T]; the cursor is an
opaque string the caller passes back to continue. REST, SQL, and MCP
generators all understand this shape, so agents can page any
connector the same way.
"""
from __future__ import annotations

from datetime import datetime

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
    # What the walk actually covered, for lists bounded in time. None on
    # a list that pages over everything; set by a connector whose upstream
    # reads a window, so a caller can tell "none exist" from "not read".
    span_start: datetime | None = Field(
        default=None, description="Earliest instant this walk reads from"
    )
    span_end: datetime | None = Field(
        default=None, description="Latest instant this walk reads to"
    )
    horizon: datetime | None = Field(
        default=None,
        description=(
            "How current the upstream ledger says it is. Records after this "
            "have not landed yet; span_end is clamped to it"
        ),
    )
