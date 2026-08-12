"""Postgres/Neon connector — Okwan connector #2, the read-path flagship.

Defined once via the Okwan SDK. Non-HTTP transport: an asyncpg
connection supplied through `context_factory`, proving the SDK's
one-definition rule holds beyond REST APIs.

Safety model (enforced, not documented):
- every operation runs inside a READ ONLY transaction
- raw SQL is restricted to a single statement
- identifiers (tables/columns) are validated against pg_catalog
  before interpolation; values always travel as bind parameters
"""
from __future__ import annotations

from typing import Any

import asyncpg

from okwan_core import (
    ConnectionStringAuth,
    Connector,
    ConnectorContext,
    OpType,
    RateLimitProfile,
    UpstreamError,
    register,
)

from .schemas import (
    Column,
    GetSchemaIn,
    ListTablesIn,
    QueryIn,
    RowSet,
    SearchRowsIn,
    Table,
    TableList,
    TableSchema,
)


class PgTransport:
    """Lazy asyncpg connection bound to one request context."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: asyncpg.Connection | None = None

    async def conn(self) -> asyncpg.Connection:
        if self._conn is None:
            try:
                self._conn = await asyncpg.connect(self._dsn, timeout=10)
            except (OSError, asyncpg.PostgresError) as exc:
                raise UpstreamError(status=502, body=f"postgres connect failed: {exc}")
        return self._conn

    async def fetch_readonly(
        self, sql: str, *args: Any
    ) -> list[asyncpg.Record]:
        conn = await self.conn()
        try:
            async with conn.transaction(readonly=True):
                return await conn.fetch(sql, *args)
        except asyncpg.PostgresError as exc:
            raise UpstreamError(status=400, body=f"postgres error: {exc}")

    async def aclose(self) -> None:
        if self._conn is not None:
            await self._conn.close()


def _pg_context(connector: Connector, credentials: dict[str, str]) -> ConnectorContext:
    return ConnectorContext(
        client=PgTransport(credentials["connection_string"]),
        credentials=credentials,
    )


postgres = register(
    Connector(
        name="postgres",
        version="0.1.0",
        description=(
            "PostgreSQL / Neon: introspect schemas, read rows with "
            "filters, run read-only SQL. Works with any Postgres DSN."
        ),
        base_url="",  # non-HTTP transport
        auth=ConnectionStringAuth(),
        rate_limit=RateLimitProfile(requests_per_second=50, burst=20),
        docs_url="https://neon.tech/docs",
        context_factory=_pg_context,
    )
)

tables = postgres.resource("tables", schema=Table, description="Database tables")
rows = postgres.resource("rows", schema=RowSet, description="Table row data")
sql = postgres.resource("sql", schema=RowSet, description="Read-only SQL access")


async def _validate_identifiers(
    t: PgTransport, schema: str, table: str, columns: list[str]
) -> list[str]:
    """Confirm table + columns exist; return the actual column list."""
    recs = await t.fetch_readonly(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        ORDER BY ordinal_position
        """,
        schema,
        table,
    )
    actual = [r["column_name"] for r in recs]
    if not actual:
        raise UpstreamError(status=404, body=f"table {schema}.{table} not found")
    unknown = [c for c in columns if c not in actual]
    if unknown:
        raise UpstreamError(status=400, body=f"unknown columns: {', '.join(unknown)}")
    return columns or actual


def _qi(identifier: str) -> str:
    """Quote a validated identifier."""
    return '"' + identifier.replace('"', '""') + '"'


@tables.operation(
    OpType.LIST,
    input_model=ListTablesIn,
    output_model=TableList,
    description="List tables in a schema.",
)
async def list_tables(ctx: ConnectorContext, params: ListTablesIn) -> TableList:
    recs = await ctx.client.fetch_readonly(
        """
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_schema = $1 AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """,
        params.schema_name,
    )
    return TableList(
        items=[Table(schema=r["table_schema"], name=r["table_name"]) for r in recs]
    )


@tables.operation(
    OpType.GET,
    input_model=GetSchemaIn,
    output_model=TableSchema,
    name="get_schema",
    description="Get column names, types, and nullability for a table.",
)
async def get_schema(ctx: ConnectorContext, params: GetSchemaIn) -> TableSchema:
    recs = await ctx.client.fetch_readonly(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        ORDER BY ordinal_position
        """,
        params.schema_name,
        params.table,
    )
    if not recs:
        raise UpstreamError(
            status=404, body=f"table {params.schema_name}.{params.table} not found"
        )
    return TableSchema(
        schema=params.schema_name,
        name=params.table,
        columns=[
            Column(
                name=r["column_name"],
                data_type=r["data_type"],
                is_nullable=r["is_nullable"] == "YES",
                default=r["column_default"],
            )
            for r in recs
        ],
    )


@rows.operation(
    OpType.SEARCH,
    input_model=SearchRowsIn,
    output_model=RowSet,
    description=(
        "Read rows from a table with optional column projection and "
        "equality filters. Identifiers are validated; values are "
        "bind parameters — injection-safe by construction."
    ),
)
async def search_rows(ctx: ConnectorContext, params: SearchRowsIn) -> RowSet:
    cols = await _validate_identifiers(
        ctx.client,
        params.schema_name,
        params.table,
        params.columns + list(params.equals.keys()),
    )
    projected = params.columns or cols
    select_list = ", ".join(_qi(c) for c in projected)
    where, args = "", []
    if params.equals:
        clauses = []
        for i, (col, val) in enumerate(params.equals.items(), start=1):
            clauses.append(f"{_qi(col)} = ${i}")
            args.append(val)
        where = " WHERE " + " AND ".join(clauses)
    stmt = (
        f"SELECT {select_list} FROM "
        f"{_qi(params.schema_name)}.{_qi(params.table)}{where} "
        f"ORDER BY 1 LIMIT {params.limit} OFFSET {params.offset}"
    )
    recs = await ctx.client.fetch_readonly(stmt, *args)
    return RowSet(
        columns=projected,
        rows=[{c: rec[c] for c in projected} for rec in recs],
        row_count=len(recs),
    )


@sql.operation(
    OpType.SEARCH,
    input_model=QueryIn,
    output_model=RowSet,
    name="query",
    description=(
        "Run a single read-only SQL statement (SELECT/WITH). Executed "
        "inside a READ ONLY transaction: any write is rejected by "
        "Postgres itself."
    ),
)
async def query(ctx: ConnectorContext, params: QueryIn) -> RowSet:
    stmt = params.sql.strip().rstrip(";")
    if ";" in stmt:
        raise UpstreamError(status=400, body="multiple statements are not allowed")
    first_word = stmt.split(None, 1)[0].upper() if stmt else ""
    if first_word not in {"SELECT", "WITH", "TABLE", "VALUES", "SHOW", "EXPLAIN"}:
        raise UpstreamError(
            status=400, body=f"read-only statements only; got '{first_word}'"
        )
    recs = await ctx.client.fetch_readonly(
        f"SELECT * FROM ({stmt}) okwan_q LIMIT {params.limit}"
        if first_word in {"SELECT", "WITH", "TABLE", "VALUES"}
        else stmt
    )
    columns = list(recs[0].keys()) if recs else []
    return RowSet(
        columns=columns,
        rows=[dict(r) for r in recs],
        row_count=len(recs),
    )
