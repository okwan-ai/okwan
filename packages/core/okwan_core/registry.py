"""Global connector registry — the single list every generator reads."""
from __future__ import annotations

from .connector import Connector

_REGISTRY: dict[str, Connector] = {}


def register(connector: Connector) -> Connector:
    if connector.name in _REGISTRY:
        raise ValueError(f"connector '{connector.name}' already registered")
    _REGISTRY[connector.name] = connector
    return connector


def get(name: str) -> Connector:
    return _REGISTRY[name]


def all_connectors() -> list[Connector]:
    return list(_REGISTRY.values())
