"""Credential storage.

The Store interface is what the gateway depends on; the in-memory
implementation is what tests and local development use. A Postgres-backed
implementation drops in behind the same interface without touching the
call sites.

Sealing and opening happen here rather than in the caller, so plaintext
credentials exist only inside a single function's stack frame.
"""
from __future__ import annotations

import uuid
from typing import Protocol

from okwan_core import CredentialError

from . import apikey
from .crypto import new_key, open_sealed, seal
from .keys import MasterKeyProvider
from .models import ApiKey, SealedCredential, Tenant
from .usage import DEFAULT_PLAN, PLANS, hour_bucket


class Store(Protocol):
    def create_tenant(self, name: str, parent_id: str | None = None) -> Tenant: ...
    def get_tenant(self, tenant_id: str) -> Tenant | None: ...
    def children_of(self, tenant_id: str) -> list[Tenant]: ...
    def issue_key(self, tenant_id: str) -> tuple[str, ApiKey]: ...
    def revoke_key(self, key_id: str) -> None: ...
    def tenant_for_key(self, full_key: str) -> Tenant | None: ...
    def put_credential(
        self, tenant_id: str, connector: str, field_name: str, value: str
    ) -> None: ...
    def credentials_for(
        self, tenant_id: str, connector: str, fields: tuple[str, ...]
    ) -> dict[str, str]: ...
    def connectors_configured(self, tenant_id: str) -> dict[str, list[str]]: ...


class MemoryStore:
    """Reference implementation. Not durable."""

    def __init__(self, master: MasterKeyProvider) -> None:
        self._master = master
        self._tenants: dict[str, Tenant] = {}
        self._keys: dict[str, ApiKey] = {}
        self._creds: dict[tuple[str, str, str], SealedCredential] = {}
        self._usage: dict[tuple, int] = {}
        self._plans: dict[str, str] = {}

    def create_tenant(self, name: str, parent_id: str | None = None) -> Tenant:
        if parent_id is not None and parent_id not in self._tenants:
            raise KeyError(f"unknown parent tenant {parent_id!r}")
        tenant = Tenant(
            id=f"ten_{uuid.uuid4().hex[:16]}", name=name, parent_id=parent_id
        )
        self._tenants[tenant.id] = tenant
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def children_of(self, tenant_id: str) -> list[Tenant]:
        return [t for t in self._tenants.values() if t.parent_id == tenant_id]

    def issue_key(self, tenant_id: str) -> tuple[str, ApiKey]:
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        full, prefix, hash_hex = apikey.generate()
        record = ApiKey(
            id=f"key_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            prefix=prefix,
            hash_hex=hash_hex,
        )
        self._keys[record.id] = record
        return full, record

    def revoke_key(self, key_id: str) -> None:
        from datetime import datetime, timezone

        record = self._keys.get(key_id)
        if record is None:
            raise KeyError(f"unknown key {key_id!r}")
        self._keys[key_id] = ApiKey(
            id=record.id,
            tenant_id=record.tenant_id,
            prefix=record.prefix,
            hash_hex=record.hash_hex,
            created_at=record.created_at,
            revoked_at=datetime.now(timezone.utc),
        )

    def tenant_for_key(self, full_key: str) -> Tenant | None:
        for record in self._keys.values():
            if record.is_active and apikey.matches(full_key, record.hash_hex):
                return self._tenants.get(record.tenant_id)
        return None

    def put_credential(
        self, tenant_id: str, connector: str, field_name: str, value: str
    ) -> None:
        if tenant_id not in self._tenants:
            raise KeyError(f"unknown tenant {tenant_id!r}")
        data_key = new_key()
        record = SealedCredential(
            tenant_id=tenant_id,
            connector=connector,
            field_name=field_name,
            ciphertext=b"",
            wrapped_key=self._master.wrap(data_key),
            key_id=self._master.key_id,
        )
        sealed = seal(data_key, value.encode(), aad=record.aad)
        self._creds[(tenant_id, connector, field_name)] = SealedCredential(
            tenant_id=tenant_id,
            connector=connector,
            field_name=field_name,
            ciphertext=sealed,
            wrapped_key=record.wrapped_key,
            key_id=record.key_id,
        )

    def credentials_for(
        self, tenant_id: str, connector: str, fields: tuple[str, ...]
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in fields:
            record = self._creds.get((tenant_id, connector, name))
            if record is None:
                out[name] = ""
                continue
            data_key = self._master.unwrap(record.wrapped_key)
            out[name] = open_sealed(
                data_key, record.ciphertext, aad=record.aad
            ).decode()
        return out

    def record_request(self, tenant_id: str, surface: str) -> None:
        key = (tenant_id, hour_bucket(), surface)
        self._usage[key] = self._usage.get(key, 0) + 1

    def usage_since(self, root_id: str, since) -> int:
        subtree = {root_id}
        changed = True
        while changed:
            changed = False
            for t in self._tenants.values():
                if t.parent_id in subtree and t.id not in subtree:
                    subtree.add(t.id)
                    changed = True
        return sum(
            n for (tid, hour, _), n in self._usage.items()
            if tid in subtree and hour >= since
        )

    def get_plan(self, tenant_id: str) -> tuple[str, int]:
        name = self._plans.get(tenant_id, DEFAULT_PLAN)
        return name, PLANS[name]

    def set_plan(self, tenant_id: str, name: str) -> None:
        if name not in PLANS:
            raise ValueError(f"unknown plan {name!r}; known: {', '.join(PLANS)}")
        self._plans[tenant_id] = name

    def connectors_configured(self, tenant_id: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for (tid, connector, field_name) in self._creds:
            if tid == tenant_id:
                out.setdefault(connector, []).append(field_name)
        return out


def resolver_for(store: Store, tenant_id: str):
    """A CredentialResolver bound to one tenant.

    This is the seam the connector SDK already expects, so the vault
    replaces header-supplied credentials without any change to fetch_rows,
    the query session, or the reconciliation runner.
    """

    def resolve(connector_name: str, fields: tuple[str, ...]) -> dict[str, str]:
        return store.credentials_for(tenant_id, connector_name, fields)

    return resolve
