"""Which connector resources are SQL-addressable.

A resource qualifies when it has a list or search operation and a record
schema. Containers whose shape is only known at call time (Postgres
RowSet) are excluded — they cannot be declared as a table ahead of the
query, and Postgres already exposes SQL directly.
"""
from __future__ import annotations

import inspect
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


#: The schema reconciliation views live in. Never a connector name.
RECON = "recon"


def recon_tables() -> list[Table]:
    """Every registered reconciliation as a SQL table, from the same
    declaration as its REST route and MCP tool (§3, the one-definition
    rule): `recon.<name>` for a pair, `recon.across_<name>` for a fold.
    Querying one runs it live, as the caller, like any connector table."""
    from okwan_recon.emitters.duckdb_view import ACROSS_COLUMNS, COLUMNS
    from okwan_recon.registry import all_across, all_reconciliations

    out = [
        Table(connector=RECON, resource=spec.view_name.partition(".")[2], operation="reconcile",
              model=type(spec.name, (), {}), columns=COLUMNS, params={"name": spec.name})
        for spec in all_reconciliations()
    ]
    out += [
        Table(connector=RECON, resource=spec.view_name.partition(".")[2], operation="across",
              model=type(spec.name, (), {}), columns=ACROSS_COLUMNS, params={"name": spec.name})
        for spec in all_across()
    ]
    return out


def recon_spec(table: Table):
    """The declaration a `recon.*` table runs."""
    from okwan_recon.registry import get, get_across

    return get_across(table.params["name"]) if table.operation == "across" else get(table.params["name"])


def _sides(spec) -> list:
    from okwan_recon.across import AcrossRails

    pairs = spec.members() if isinstance(spec, AcrossRails) else [spec]
    return [ref for p in pairs for ref in (p.left, p.right)]


def catalog() -> list[Table]:
    """Every SQL-addressable resource across every registered connector,
    then every reconciliation."""
    return [t for c in all_connectors() for t in tables_for(c)] + list(_DECLARED) + recon_tables()


def missing_credentials(
    table: Table, resolver=None
) -> tuple[str, ...]:
    """Credential fields the deployment has not supplied for this table.

    Empty means the table can be fetched. Declared SQL tables resolve
    against the connector that actually serves them.
    """
    from okwan_recon.fetch import env_credentials

    resolve = resolver or env_credentials
    if table.connector == RECON:
        # Every system the reconciliation reads, named with its connector
        # since several are involved: ("paypal.client_id", ...).
        missing: list[str] = []
        for ref in _sides(recon_spec(table)):
            for field in missing_credentials(
                Table(connector=ref.connector, resource=ref.resource, operation=ref.operation,
                      model=object, columns=()), resolver,
            ):
                name = f"{ref.connector}.{field}"
                if name not in missing:
                    missing.append(name)
        return tuple(missing)
    name = "postgres" if table.connector == "rail" else table.connector
    try:
        connector = get_connector(name)
    except KeyError:
        return ("<connector not registered>",)

    fields = connector.auth.required_fields
    supplied = resolve(connector.name, fields)
    if inspect.isawaitable(supplied):
        raise TypeError(
            "missing_credentials needs a synchronous resolver; "
            "await the credentials first and pass a dict-backed resolver"
        )
    return tuple(f for f in fields if not supplied.get(f))


def find(qualified: str) -> Table:
    for t in catalog():
        if t.qualified == qualified:
            return t
    raise KeyError(f"unknown table {qualified!r}")
