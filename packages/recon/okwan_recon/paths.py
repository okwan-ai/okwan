"""Dotted-path lookup across dicts and models. Missing -> None."""
from __future__ import annotations

from typing import Any


def dig(row: Any, path: str) -> Any:
    cur = row
    for part in path.split("."):
        if cur is None:
            return None
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
    return cur


def put(row: dict[str, Any], path: str, value: Any) -> None:
    """Set a dotted path on a dict, creating the dicts on the way."""
    *parents, last = path.split(".")
    cur = row
    for part in parents:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = cur[part] = {}
        cur = nxt
    cur[last] = value
