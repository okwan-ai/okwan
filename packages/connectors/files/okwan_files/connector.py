"""Imported files as a connector: the three kinds, read back.

What a tenant uploads (`okwan_api.imports`) is not an outside system, but
reading it back is the same act as reading a rail: a paged list of
records in a declared shape. So it is one connector, defined once here,
and it gets REST routes, SQL tables (`files.settlement_batches`,
`files.settlement_transactions`, `files.bank_deposits`) and MCP tools
the way every connector does; a reconciliation reads it like any other
side (`batches_deposits`). Every operation is a read. Upload and delete
are Okwan's own administration, at `/v1/tenants/{id}/imports`, and never
appear here.

**No credential: the tenant is the caller.** The platform names the
calling tenant in the connector context (`ConnectorContext.tenant_id`),
from the key or session that authenticated the request, never from a
request body or a stored value. The store then reads that tenant's rows
under row-level security. Where no tenant is known (a local MCP server
run from the environment) every operation refuses.

The store is handed in once at startup (`use_store`), so this package
never imports the API.
"""
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any, Literal

from okwan_core import Connector, CredentialError, register
from okwan_core.auth import NoCredentials
from okwan_core.client import RateLimitProfile
from okwan_core.connector import ConnectorContext, OpType
from okwan_core.pagination import CursorPage
from pydantic import BaseModel, Field, field_validator

from .schemas import KINDS, BankDeposit, SettlementBatch, SettlementTransaction

_store: Callable[[], Any] | None = None


def use_store(getter: Callable[[], Any]) -> None:
    """Where imported rows are read from: the API's store, at startup."""
    global _store
    _store = getter


class _Reader:
    """The connector's transport: nothing to open, nothing to close."""

    async def aclose(self) -> None:
        return None


def _context(connector: Connector, credentials: dict[str, str]) -> ConnectorContext:
    return ConnectorContext(client=_Reader(), credentials=credentials)


files = register(
    Connector(
        name="files",
        version="1.0.0",
        description=(
            "Imported files: read the settlement reports, transaction exports and bank statements "
            "uploaded to this account, mapped into three kinds. Amounts are minor units (cents). "
            "Read-only; files are uploaded and deleted under /v1/tenants/{id}/imports."
        ),
        base_url="",
        auth=NoCredentials(),
        rate_limit=RateLimitProfile(),
        context_factory=_context,
    )
)


def _encode(row: dict, date_field: str) -> str:
    raw = json.dumps([row[date_field], row["file_id"], row["row"]]).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode(cursor: str) -> tuple[date, str, int]:
    try:
        day, file_id, n = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return date.fromisoformat(day), str(file_id), int(n)
    except (ValueError, TypeError, binascii.Error):
        raise ValueError("not a cursor from this list") from None


class ListImportedIn(BaseModel):
    limit: int = Field(default=100, ge=1, le=500, description="Page size")
    cursor: str | None = Field(default=None, description="Opaque cursor from a previous page's `next_cursor`")
    since: date | None = Field(default=None, description="Rows on or after this day")
    until: date | None = Field(default=None, description="Rows on or before this day")
    start_date: datetime | None = Field(
        default=None, description="Rows on or after this instant's day; a reconciliation's lookback sets it")
    end_date: datetime | None = Field(default=None, description="Rows on or before this instant's day")

    @field_validator("cursor")
    @classmethod
    def _cursor(cls, v: str | None) -> str | None:
        if v is not None:
            _decode(v)
        return v

    def window(self) -> tuple[date | None, date | None]:
        since = self.since or (self.start_date.date() if self.start_date else None)
        until = self.until or (self.end_date.date() if self.end_date else None)
        return since, until


class ListDepositsIn(ListImportedIn):
    direction: Literal["all", "in", "out"] = Field(
        default="all", description="`in`: money in only (deposits); `out`: money out only")


def _span(since: date | None, until: date | None) -> dict[str, datetime | None]:
    return {
        "span_start": datetime.combine(since, time.min, UTC) if since else None,
        "span_end": datetime.combine(until, time.max, UTC) if until else None,
    }


async def _read(kind: str, ctx: ConnectorContext, body: ListImportedIn, direction: str = "all"):
    if _store is None or not ctx.tenant_id:
        raise CredentialError(
            "imported files are read through Okwan's hosted API, as the account they belong to")
    since, until = body.window()
    rows = await _store().import_rows(
        ctx.tenant_id, kind, since=since, until=until, limit=body.limit + 1,
        after=_decode(body.cursor) if body.cursor else None,
        direction=None if direction == "all" else direction,
    )
    more = len(rows) > body.limit
    rows = rows[: body.limit]
    date_field = KINDS[kind].date_field
    return {
        "items": rows, "has_more": more,
        "next_cursor": _encode(rows[-1], date_field) if more and rows else None,
        **_span(since, until),
    }


batches = files.resource("settlement_batches", SettlementBatch, KINDS["settlement_batches"].description)
transactions = files.resource(
    "settlement_transactions", SettlementTransaction, KINDS["settlement_transactions"].description)
deposits = files.resource("bank_deposits", BankDeposit, KINDS["bank_deposits"].description)


@batches.operation(OpType.LIST, ListImportedIn, CursorPage[SettlementBatch])
async def list_batches(ctx: ConnectorContext, body: ListImportedIn) -> CursorPage[SettlementBatch]:
    """List imported settlement batches, oldest first, from files not expired."""
    return CursorPage[SettlementBatch].model_validate(await _read("settlement_batches", ctx, body))


@transactions.operation(OpType.LIST, ListImportedIn, CursorPage[SettlementTransaction])
async def list_transactions(
    ctx: ConnectorContext, body: ListImportedIn
) -> CursorPage[SettlementTransaction]:
    """List imported settlement transactions, oldest first, from files not expired."""
    return CursorPage[SettlementTransaction].model_validate(
        await _read("settlement_transactions", ctx, body))


@deposits.operation(OpType.LIST, ListDepositsIn, CursorPage[BankDeposit])
async def list_deposits(ctx: ConnectorContext, body: ListDepositsIn) -> CursorPage[BankDeposit]:
    """List imported bank statement lines, oldest first, from files not expired."""
    return CursorPage[BankDeposit].model_validate(
        await _read("bank_deposits", ctx, body, body.direction))
