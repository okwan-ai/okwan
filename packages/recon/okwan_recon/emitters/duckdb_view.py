"""DuckDB view generated from the same declaration.

Dependency-free materialisation: explicit schema plus executemany, so
no pandas or arrow requirement leaks into the core install.
"""
from __future__ import annotations

import json
from typing import Any

from ..across import AcrossRails, AcrossResult
from ..declaration import Reconciliation
from ..engine import ReconResult

_COLUMNS = (
    ("status", "VARCHAR"),
    ("rule", "VARCHAR"),
    ("confidence", "DOUBLE"),
    # Why an unverifiable row could not be judged; null on every other.
    ("reason", "VARCHAR"),
    ("left_record", "JSON"),
    ("right_record", "JSON"),
)


def materialize_view(con: Any, spec: Reconciliation, result: ReconResult) -> str:
    schema, _, table = spec.view_name.partition(".")
    table = table or spec.name
    backing = f"_{table}_rows"
    cols = ", ".join(f"{n} {t}" for n, t in _COLUMNS)

    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    con.execute(f'CREATE OR REPLACE TABLE "{schema}"."{backing}" ({cols})')

    payload = [
        (
            row["status"],
            row["rule"],
            row["confidence"],
            row["reason"],
            json.dumps(row["left"], default=str) if row["left"] is not None else None,
            json.dumps(row["right"], default=str) if row["right"] is not None else None,
        )
        for row in result.rows()
    ]
    if payload:
        placeholders = ", ".join("?" for _ in _COLUMNS)
        con.executemany(
            f'INSERT INTO "{schema}"."{backing}" VALUES ({placeholders})', payload
        )

    con.execute(
        f'CREATE OR REPLACE VIEW "{schema}"."{table}" AS '
        f"SELECT {', '.join(n for n, _ in _COLUMNS)} "
        f'FROM "{schema}"."{backing}"'
    )
    return spec.view_name


_ACROSS_COLUMNS = (
    ("outcome", "VARCHAR"),
    ("order_record", "JSON"),
    ("order_total_minor", "BIGINT"),
    ("collected_minor", "BIGINT"),
    ("collected_on", "JSON"),
    ("unverified_rails", "JSON"),
    ("reason", "VARCHAR"),
    ("rails", "JSON"),
)


def materialize_across_view(con: Any, spec: AcrossRails, result: AcrossResult) -> str:
    """One row per order. `collected_on` lists the rails that took it."""
    schema, _, table = spec.view_name.partition(".")
    backing = f"_{table}_rows"
    cols = ", ".join(f"{n} {t}" for n, t in _ACROSS_COLUMNS)

    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    con.execute(f'CREATE OR REPLACE TABLE "{schema}"."{backing}" ({cols})')

    def js(value: Any) -> str:
        return json.dumps(value, default=str)

    payload = [
        (
            row["outcome"],
            js(row["order"]),
            row["order_total_minor"],
            row["collected_minor"],
            js(row["collected_on"]),
            js(row["unverified_rails"]),
            row["reason"],
            js(row["rails"]),
        )
        for row in result.rows()
    ]
    if payload:
        placeholders = ", ".join("?" for _ in _ACROSS_COLUMNS)
        con.executemany(
            f'INSERT INTO "{schema}"."{backing}" VALUES ({placeholders})', payload
        )

    con.execute(
        f'CREATE OR REPLACE VIEW "{schema}"."{table}" AS '
        f"SELECT {', '.join(n for n, _ in _ACROSS_COLUMNS)} "
        f'FROM "{schema}"."{backing}"'
    )
    return spec.view_name
