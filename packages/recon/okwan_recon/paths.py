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
