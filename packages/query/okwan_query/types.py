"""Pydantic field types projected onto DuckDB column types.

The connector's Resource.schema is the source of truth for both the
REST response shape and the SQL column shape. They cannot drift because
neither is written by hand.
"""
from __future__ import annotations

from typing import Any

_SCALARS = {
    "integer": "BIGINT",
    "number": "DOUBLE",
    "boolean": "BOOLEAN",
    "string": "VARCHAR",
}

_FORMATS = {
    "date-time": "TIMESTAMP",
    "date": "DATE",
}


def _unwrap_optional(spec: dict[str, Any]) -> dict[str, Any]:
    """`str | None` becomes anyOf; take the non-null branch."""
    branches = spec.get("anyOf") or spec.get("oneOf")
    if not branches:
        return spec
    concrete = [b for b in branches if b.get("type") != "null"]
    return concrete[0] if len(concrete) == 1 else {}


def column_type(spec: dict[str, Any]) -> str:
    """Map one JSON-schema property to a DuckDB type.

    Nested objects and arrays become JSON rather than being flattened:
    a flattened shape would diverge from the REST payload, and the point
    of the one-definition rule is that it cannot.
    """
    spec = _unwrap_optional(spec)
    if not spec:
        return "JSON"

    kind = spec.get("type")
    if kind == "string":
        return _FORMATS.get(spec.get("format", ""), "VARCHAR")
    if kind in _SCALARS:
        return _SCALARS[kind]
    if kind in ("object", "array") or "$ref" in spec:
        return "JSON"
    return "JSON"


def columns_for(model: type) -> list[tuple[str, str]]:
    """Ordered (name, duckdb_type) pairs for a Pydantic model.

    Serialization mode so computed fields are included — amount_major and
    net_payment_minor are columns, not afterthoughts.
    """
    schema = model.model_json_schema(mode="serialization")
    props = schema.get("properties", {})
    return [(name, column_type(spec)) for name, spec in props.items()]
