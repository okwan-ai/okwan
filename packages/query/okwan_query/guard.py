"""Statement guard for agent-supplied SQL.

Every other tool in the platform has a fixed shape — the agent chooses
arguments, not operations. A query tool takes arbitrary SQL, so read-only
has to be enforced here rather than derived from an op type.

The upstream connectors are safe either way: materialisation only ever
calls list operations. The exposure is DuckDB itself, which can write
files (COPY TO), load extensions (INSTALL), and attach local databases.
A readOnlyHint on a tool that can do those things would be a false claim.
"""
from __future__ import annotations

import re

from okwan_core import OkwanError


class UnsafeStatement(OkwanError):
    """The submitted SQL is not a read-only single statement."""


_ALLOWED_HEADS = frozenset({"SELECT", "WITH", "DESCRIBE", "SUMMARIZE", "EXPLAIN"})

#: Rejected outright even inside an otherwise-SELECT statement.
_FORBIDDEN = re.compile(
    r"\b(ATTACH|DETACH|INSTALL|LOAD|COPY|EXPORT|IMPORT|"
    r"CREATE|DROP|ALTER|INSERT|UPDATE|DELETE|TRUNCATE|"
    r"PRAGMA|SET|CALL|read_csv|read_parquet|read_json|glob)\b",
    re.IGNORECASE,
)

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def check(sql: str) -> str:
    """Return the statement if it is a safe read, else raise."""
    stripped = _COMMENT.sub(" ", sql).strip().rstrip(";").strip()
    if not stripped:
        raise UnsafeStatement("empty statement")

    if ";" in stripped:
        raise UnsafeStatement("multiple statements are not allowed")

    head = stripped.split(None, 1)[0].upper()
    if head not in _ALLOWED_HEADS:
        raise UnsafeStatement(
            f"read-only statements only; got '{head}'. "
            f"Allowed: {', '.join(sorted(_ALLOWED_HEADS))}"
        )

    found = _FORBIDDEN.search(stripped)
    if found:
        raise UnsafeStatement(
            f"'{found.group(0)}' is not permitted — it can write files, "
            "load extensions, or reach outside the connector catalog"
        )

    return stripped
