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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import duckdb
from okwan_core import CredentialError
from okwan_recon.declaration import ResourceRef
from okwan_recon.fetch import CredentialResolver, env_credentials, fetch_rows

from .catalog import RECON, Table, catalog, missing_credentials, recon_spec
from .guard import check as check_statement

#: schema.table references in a SQL string. Deliberately loose — a name
#: that isn't in the catalog is left alone for DuckDB to resolve or reject.
_REF = re.compile(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", re.IGNORECASE)

DEFAULT_LIMIT = 500

#: DuckDB here is a sandbox for one query over rows the session loaded
#: itself (CREATE TABLE and INSERT, never a file). Everything else is off:
#: reading or writing any file (read_text('/proc/self/environ') would hand
#: a caller the server's environment, the vault's master key included),
#: the network, and loading extensions. Locked, so no statement can switch
#: any of it back on. This is the wall; the statement guard in guard.py is
#: a second, coarser one and must never be the only thing in the way.
SANDBOX: dict[str, Any] = {
    "enable_external_access": False,
    "autoload_known_extensions": False,
    "autoinstall_known_extensions": False,
    "allow_community_extensions": False,
    "lock_configuration": True,
}


@dataclass(frozen=True, slots=True)
class ReusedRun:
    """A recent run of a reconciliation this caller may read again."""

    result: Any
    run_id: str | None
    age_seconds: int


class TooManyRuns(Exception):
    """A query needs more live reconciliation runs than the caller may
    start now; `retry_after` seconds until it may."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class QuerySession:
    """One DuckDB connection plus the connector tables it has loaded."""

    def __init__(
        self,
        resolver: CredentialResolver = env_credentials,
        max_records: int = DEFAULT_LIMIT,
        on_run: Callable[..., Awaitable[Any]] | None = None,
        reuse: Callable[[Any], ReusedRun | None] | None = None,
        admit: Callable[[int], None] | None = None,
        fresh: bool = False,
    ) -> None:
        self._con = duckdb.connect(":memory:", config=SANDBOX)
        self._resolver = resolver
        self._max_records = max_records
        #: Called with (spec, started, result=..., error=...) after a
        #: `recon.*` table runs, so the surface stores the run like any
        #: other (§3.4, "every run is stored").
        self._on_run = on_run
        #: A recent run of a declaration to read instead of running it
        #: again, unless `fresh` (recon_reuse.py); and the gate a query's
        #: live runs must pass, raising TooManyRuns.
        self._reuse = None if fresh else reuse
        self._admit = admit
        self._planned: dict[str, ReusedRun] = {}
        #: Per `recon.*` table: the run read, and whether it was reused.
        self.runs: dict[str, dict[str, Any]] = {}
        #: Reads that went upstream (connector tables, live runs). A query
        #: answered from reused runs alone read nothing.
        self.upstream_reads = 0
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
            elif kind == "JSON" or isinstance(value, (dict, list)):
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

        if table.connector == RECON:
            count = await self._run_reconciliation(table)
            self._loaded.add(table.qualified)
            return count

        connector = "postgres" if table.connector == "rail" else table.connector
        resource = "sql" if table.connector == "rail" else table.resource

        self.upstream_reads += 1
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

    async def _run_reconciliation(self, table: Table) -> int:
        """Run the declaration as this session's caller, at its own record
        caps (so SQL and REST give the same verdicts), and materialise its
        view. The query's `limit` caps connector tables, not a run."""
        from okwan_recon.across import AcrossRails, run_across
        from okwan_recon.emitters.duckdb_view import (
            materialize_across_view,
            materialize_view,
        )
        from okwan_recon.runner import run

        spec = recon_spec(table)
        planned = self._planned.get(table.qualified)
        if planned is not None:
            materialise = materialize_across_view if isinstance(spec, AcrossRails) else materialize_view
            materialise(self._con, spec, planned.result)
            self.runs[table.qualified] = {
                "run_id": planned.run_id, "reused": True, "age_seconds": planned.age_seconds,
            }
            return len(planned.result.rows())

        started = datetime.now(UTC)
        self.upstream_reads += 1
        try:
            if isinstance(spec, AcrossRails):
                result = await run_across(spec, self._resolver)
                materialize_across_view(self._con, spec, result)
            else:
                result = await run(spec, self._resolver)
                materialize_view(self._con, spec, result)
        except Exception as exc:
            if self._on_run is not None:
                await self._on_run(spec, started, error=exc)
            raise
        run_id = None
        if self._on_run is not None:
            run_id = await self._on_run(spec, started, result=result)
        self.runs[table.qualified] = {"run_id": run_id, "reused": False}
        return len(result.rows())

    def _plan_runs(self, tables: list[Table]) -> None:
        """Before anything is fetched: which `recon.*` tables read a recent
        run, and whether the caller may start the rest. A table missing a
        connection fails here as it would in `load`, before it can count
        against the limit."""
        live = 0
        for table in tables:
            if table.connector != RECON or table.qualified in self._loaded:
                continue
            reused = self._reuse(recon_spec(table)) if self._reuse is not None else None
            if reused is not None:
                self._planned[table.qualified] = reused
                continue
            missing = missing_credentials(table, self._resolver)
            if missing:
                raise CredentialError(
                    f"{table.qualified} needs {', '.join(missing)} — "
                    "not configured in this deployment"
                )
            live += 1
        if live and self._admit is not None:
            self._admit(live)

    async def query(self, sql: str) -> dict[str, Any]:
        """Load whatever the SQL references, then execute it.

        Read-only by construction: every table is materialised from a
        list/search operation, and there is nothing to write back to.
        """
        sql = check_statement(sql)
        tables = self.referenced_tables(sql)
        self._plan_runs(tables)
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
            **({"runs": self.runs} if self.runs else {}),
        }

    def close(self) -> None:
        self._con.close()
