"""Reconciliation registry — the single list every emitter reads."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .declaration import Reconciliation

if TYPE_CHECKING:
    from .across import AcrossRails

_REGISTRY: dict[str, Reconciliation] = {}
_ACROSS: dict[str, AcrossRails] = {}


def register(spec: Reconciliation) -> Reconciliation:
    # One name space across both kinds, so a name dispatches to one thing.
    if spec.name in _ACROSS:
        raise ValueError(f"'{spec.name}' already names an across-rails fold")
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


def register_across(spec: AcrossRails) -> AcrossRails:
    existing = _ACROSS.get(spec.name)
    if existing is not None and existing != spec:
        raise ValueError(
            f"across-rails fold '{spec.name}' already registered with a different definition"
        )
    if spec.name in _REGISTRY:
        raise ValueError(f"'{spec.name}' already names a reconciliation")
    # Views share the recon schema: recon.across_<name> must not shadow a
    # two-sided reconciliation's recon.<name>.
    if f"across_{spec.name}" in _REGISTRY:
        raise ValueError(f"'{spec.view_name}' already names a reconciliation view")
    _ACROSS[spec.name] = spec
    return spec


def get_across(name: str) -> AcrossRails:
    return _ACROSS[name]


def all_across() -> list[AcrossRails]:
    return list(_ACROSS.values())


def clear() -> None:
    _REGISTRY.clear()
    _ACROSS.clear()
