"""Federated SQL over live connectors.

DuckDB is the execution engine; the connectors are the storage. A query
names tables like `shopify.orders`; the session resolves each to a
connector operation, fetches rows through the same paging contract the
reconciliation layer uses, materialises them, and lets DuckDB do the
join.

Fetch is lazy and per-session: only the tables a query actually names
are pulled, and they are pulled once. Nothing is persisted — this is
federation, not ETL. The rows exist for the life of the session and the
next query sees the upstream as it is then.
"""
from __future__ import annotations

import json
import re
from typing import Any

import duckdb

from okwan_core import CredentialError

from okwan_recon.fetch import CredentialResolver, env_credentials, fetch_rows
from okwan_recon.declaration import ResourceRef

from .catalog import Table, catalog, missing_credentials
from .guard import check as check_statement

#: schema.table references in a SQL string. Deliberately loose — a name
#: that isn't in the catalog is left alone for DuckDB to resolve or reject.
_REF = re.compile(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", re.IGNORECASE)

DEFAULT_LIMIT = 500


class QuerySession:
    """One DuckDB connection plus the connector tables it has loaded."""

    def __init__(
        self,
        resolver: CredentialResolver = env_credentials,
        max_records: int = DEFAULT_LIMIT,
    ) -> None:
        self._con = duckdb.connect(":memory:")
        self._resolver = resolver
        self._max_records = max_records
        self._loaded: set[str] = set()
        self._catalog = {t.qualified: t for t in catalog()}

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        return self._con

    @property
    def loaded(self) -> frozenset[str]:
        return frozenset(self._loaded)

    def referenced_tables(self, sql: str) -> list[Table]:
        """Catalog tables named in a SQL string."""
        seen: dict[str, Table] = {}
        for schema, table in _REF.findall(sql):
            key = f"{schema.lower()}.{table.lower()}"
            if key in self._catalog and key not in seen:
                seen[key] = self._catalog[key]
        return list(seen.values())

    def _coerce(self, table: Table, row: dict[str, Any]) -> tuple[Any, ...]:
        out: list[Any] = []
        for name, kind in table.columns:
            value = row.get(name)
            if value is None:
                out.append(None)
            elif kind == "JSON":
                out.append(json.dumps(value, default=str))
            elif isinstance(value, (dict, list)):
                out.append(json.dumps(value, default=str))
            else:
                out.append(value)
        return tuple(out)

    async def load(self, table: Table) -> int:
        """Fetch a table's rows and materialise them. Idempotent per session."""
        if table.qualified in self._loaded:
            return 0

        missing = missing_credentials(table, self._resolver)
        if missing:
            raise CredentialError(
                f"{table.qualified} needs {', '.join(missing)} — "
                "not configured in this deployment"
            )

        connector = "postgres" if table.connector == "rail" else table.connector
        resource = "sql" if table.connector == "rail" else table.resource

        rows = await fetch_rows(
            ResourceRef(
                connector=connector,
                resource=resource,
                operation=table.operation,
                params=table.params,
            ),
            self._resolver,
            self._max_records,
        )

        self._con.execute(f'CREATE SCHEMA IF NOT EXISTS "{table.connector}"')
        self._con.execute(table.ddl())
        if rows:
            placeholders = ", ".join("?" for _ in table.columns)
            self._con.executemany(
                f'INSERT INTO "{table.connector}"."{table.resource}" '
                f"VALUES ({placeholders})",
                [self._coerce(table, r) for r in rows],
            )
        self._loaded.add(table.qualified)
        return len(rows)

    async def query(self, sql: str) -> dict[str, Any]:
        """Load whatever the SQL references, then execute it.

        Read-only by construction: every table is materialised from a
        list/search operation, and there is nothing to write back to.
        """
        sql = check_statement(sql)
        tables = self.referenced_tables(sql)
        loaded: dict[str, int] = {}
        for table in tables:
            count = await self.load(table)
            loaded[table.qualified] = count

        cursor = self._con.execute(sql)
        columns = [d[0] for d in (cursor.description or [])]
        rows = [dict(zip(columns, r)) for r in cursor.fetchall()]
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "sources": loaded or {t: 0 for t in self.loaded},
        }

    def close(self) -> None:
        self._con.close()
