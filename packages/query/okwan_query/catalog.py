"""Which connector resources are SQL-addressable.

A resource qualifies when it has a list or search operation and a record
schema. Containers whose shape is only known at call time (Postgres
RowSet) are excluded — they cannot be declared as a table ahead of the
query, and Postgres already exposes SQL directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from okwan_core import all_connectors
from okwan_core import get as get_connector
from okwan_core.connector import Connector, Operation, OpType, Resource

from .types import columns_for

_LISTABLE = frozenset({OpType.LIST, OpType.SEARCH})

#: Resources whose schema describes the envelope, not the record.
_EXCLUDED = frozenset({("postgres", "rows"), ("postgres", "sql")})


@dataclass(frozen=True, slots=True)
class Table:
    connector: str
    resource: str
    operation: str
    model: type
    columns: tuple[tuple[str, str], ...]
    #: Extra operation inputs — a SQL statement, a status filter.
    params: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.params is None:
            object.__setattr__(self, "params", {})

    @property
    def schema_name(self) -> str:
        return self.connector

    @property
    def qualified(self) -> str:
        return f"{self.connector}.{self.resource}"

    def ddl(self) -> str:
        cols = ", ".join(f'"{n}" {t}' for n, t in self.columns)
        return f'CREATE OR REPLACE TABLE "{self.connector}"."{self.resource}" ({cols})'


def _pick_operation(resource: Resource) -> Operation | None:
    for op in resource.operations.values():
        if op.op_type in _LISTABLE:
            return op
    return None


def tables_for(connector: Connector) -> list[Table]:
    out: list[Table] = []
    for resource in connector.resources.values():
        if (connector.name, resource.name) in _EXCLUDED:
            continue
        op = _pick_operation(resource)
        if op is None:
            continue
        cols = columns_for(resource.schema)
        if not cols:
            continue
        out.append(
            Table(
                connector=connector.name,
                resource=resource.name,
                operation=op.name,
                model=resource.schema,
                columns=tuple(cols),
            )
        )
    return out


#: Tables declared over a raw SQL statement rather than a connector
#: resource. The columns are stated because the statement's shape cannot
#: be introspected ahead of the call.
_DECLARED: list[Table] = []


def declare_sql_table(
    name: str, sql: str, columns: list[tuple[str, str]]
) -> Table:
    """Expose a Postgres query as a named SQL-addressable table."""
    table = Table(
        connector="rail",
        resource=name,
        operation="query",
        model=type(name, (), {}),
        columns=tuple(columns),
        params={"sql": sql},
    )
    _DECLARED[:] = [t for t in _DECLARED if t.qualified != table.qualified]
    _DECLARED.append(table)
    return table


def catalog() -> list[Table]:
    """Every SQL-addressable resource across every registered connector."""
    return [t for c in all_connectors() for t in tables_for(c)] + list(_DECLARED)


def missing_credentials(
    table: Table, resolver=None
) -> tuple[str, ...]:
    """Credential fields the deployment has not supplied for this table.

    Empty means the table can be fetched. Declared SQL tables resolve
    against the connector that actually serves them.
    """
    from okwan_recon.fetch import env_credentials

    resolve = resolver or env_credentials
    name = "postgres" if table.connector == "rail" else table.connector
    try:
        connector = get_connector(name)
    except KeyError:
        return ("<connector not registered>",)

    fields = connector.auth.required_fields
    supplied = resolve(connector.name, fields)
    return tuple(f for f in fields if not supplied.get(f))


def find(qualified: str) -> Table:
    for t in catalog():
        if t.qualified == qualified:
            return t
    raise KeyError(f"unknown table {qualified!r}")
