"""Reconciliation registry — the single list every emitter reads."""
from __future__ import annotations

from .declaration import Reconciliation

_REGISTRY: dict[str, Reconciliation] = {}


def register(spec: Reconciliation) -> Reconciliation:
    existing = _REGISTRY.get(spec.name)
    if existing is not None and existing != spec:
        raise ValueError(
            f"reconciliation '{spec.name}' already registered with a different definition"
        )
    _REGISTRY[spec.name] = spec
    return spec


def get(name: str) -> Reconciliation:
    return _REGISTRY[name]


def all_reconciliations() -> list[Reconciliation]:
    return list(_REGISTRY.values())


def clear() -> None:
    _REGISTRY.clear()
