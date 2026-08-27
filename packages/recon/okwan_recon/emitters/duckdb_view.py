"""DuckDB view generated from the same declaration.

Dependency-free materialisation: explicit schema plus executemany, so
no pandas or arrow requirement leaks into the core install.
"""
from __future__ import annotations

import json
from typing import Any

from ..declaration import Reconciliation
from ..engine import ReconResult

_COLUMNS = (
    ("status", "VARCHAR"),
    ("rule", "VARCHAR"),
    ("confidence", "DOUBLE"),
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
        f"SELECT status, rule, confidence, left_record, right_record "
        f'FROM "{schema}"."{backing}"'
    )
    return spec.view_name
