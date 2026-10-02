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
from datetime import UTC, datetime
from typing import Protocol

from . import apikey
from .accounts import AccountRefused
from .crypto import new_key, open_sealed, seal
from .keys import MasterKeyProvider
from .models import ApiKey, SealedCredential, Tenant
from .usage import DEFAULT_PLAN, PLANS, hour_bucket


class Store(Protocol):
    async def create_tenant(self, name: str, parent_id: str | None = None) -> Tenant: ...
    async def get_tenant(self, tenant_id: str) -> Tenant | None: ...
    async def children_of(self, tenant_id: str) -> list[Tenant]: ...
    async def issue_key(self, tenant_id: str) -> tuple[str, ApiKey]: ...
    async def revoke_key(self, key_id: str) -> None: ...
    async def tenant_for_key(self, full_key: str) -> Tenant | None: ...
    async def put_credential(
        self, tenant_id: str, connector: str, field_name: str, value: str
    ) -> None: ...
    async def credentials_for(
        self, tenant_id: str, connector: str, fields: tuple[str, ...]
    ) -> dict[str, str]: ...
    async def connectors_configured(self, tenant_id: str) -> dict[str, list[str]]: ...
    async def record_request(self, tenant_id: str, surface: str) -> None: ...
    async def usage_since(self, root_id: str, since) -> int: ...
    async def get_plan(self, tenant_id: str) -> tuple[str, int]: ...
    async def set_plan(self, tenant_id: str, name: str) -> None: ...
    async def key_owner(self, key_id: str) -> str | None: ...
    async def add_signup(
        self, email: str, password_hash: str, token_hash: str, expires_at: datetime
    ) -> None: ...
    async def signup_for(self, token_hash: str) -> tuple[str, str] | None: ...
    async def complete_signup(self, token_hash: str) -> Tenant | None: ...
    async def account_exists(self, email: str) -> bool: ...
    async def account_login(self, email: str) -> tuple[str, str] | None: ...
    async def add_account(self, tenant_id: str, email: str, password_hash: str) -> None: ...
    async def list_tenants(self) -> list[Tenant]: ...
    async def create_session(
        self, tenant_id: str, token_hash: str, expires_at: datetime
    ) -> None: ...
    async def tenant_for_session(self, token_hash: str) -> Tenant | None: ...
    async def delete_session(self, token_hash: str) -> None: ...


class MemoryStore:
    """Reference implementation. Not durable."""

    def __init__(self, master: MasterKeyProvider) -> None:
        self._master = master
        self._tenants: dict[str, Tenant] = {}
        self._keys: dict[str, ApiKey] = {}
        self._creds: dict[tuple[str, str, str], SealedCredential] = {}
        self._usage: dict[tuple, int] = {}
        self._plans: dict[str, str] = {}
        # token_hash → (email, password_hash, expires_at)
        self._signups: dict[str, tuple[str, str, datetime]] = {}
        # email → (tenant_id, password_hash)
        self._accounts: dict[str, tuple[str, str]] = {}
        # token_hash → (tenant_id, expires_at)
        self._sessions: dict[str, tuple[str, datetime]] = {}

    async def create_tenant(self, name: str, parent_id: str | None = None) -> Tenant:
        if parent_id is not None and parent_id not in self._tenants:
            raise KeyError(f"unknown parent tenant {parent_id!r}")
        tenant = Tenant(
            id=f"ten_{uuid.uuid4().hex[:16]}", name=name, parent_id=parent_id
        )
        self._tenants[tenant.id] = tenant
        return tenant

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    async def children_of(self, tenant_id: str) -> list[Tenant]:
        return [t for t in self._tenants.values() if t.parent_id == tenant_id]

    async def list_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())

    async def issue_key(self, tenant_id: str) -> tuple[str, ApiKey]:
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

    async def revoke_key(self, key_id: str) -> None:
        from datetime import datetime

        record = self._keys.get(key_id)
        if record is None:
            raise KeyError(f"unknown key {key_id!r}")
        self._keys[key_id] = ApiKey(
            id=record.id,
            tenant_id=record.tenant_id,
            prefix=record.prefix,
            hash_hex=record.hash_hex,
            created_at=record.created_at,
            revoked_at=datetime.now(UTC),
        )

    async def tenant_for_key(self, full_key: str) -> Tenant | None:
        for record in self._keys.values():
            if record.is_active and apikey.matches(full_key, record.hash_hex):
                return self._tenants.get(record.tenant_id)
        return None

    async def put_credential(
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

    async def credentials_for(
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

    async def record_request(self, tenant_id: str, surface: str) -> None:
        key = (tenant_id, hour_bucket(), surface)
        self._usage[key] = self._usage.get(key, 0) + 1

    async def usage_since(self, root_id: str, since) -> int:
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

    async def get_plan(self, tenant_id: str) -> tuple[str, int]:
        name = self._plans.get(tenant_id, DEFAULT_PLAN)
        return name, PLANS[name]

    async def set_plan(self, tenant_id: str, name: str) -> None:
        if name not in PLANS:
            raise ValueError(f"unknown plan {name!r}; known: {', '.join(PLANS)}")
        self._plans[tenant_id] = name

    async def connectors_configured(self, tenant_id: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for (tid, connector, field_name) in self._creds:
            if tid == tenant_id:
                out.setdefault(connector, []).append(field_name)
        return out

    async def key_owner(self, key_id: str) -> str | None:
        record = self._keys.get(key_id)
        return None if record is None else record.tenant_id

    # ── accounts ────────────────────────────────────────────────────

    async def add_signup(
        self, email: str, password_hash: str, token_hash: str, expires_at: datetime
    ) -> None:
        self._signups[token_hash] = (email, password_hash, expires_at)

    async def signup_for(self, token_hash: str) -> tuple[str, str] | None:
        found = self._signups.get(token_hash)
        if found is None or found[2] <= datetime.now(UTC):
            return None
        return found[0], found[1]

    async def complete_signup(self, token_hash: str) -> Tenant | None:
        found = await self.signup_for(token_hash)
        self._signups.pop(token_hash, None)
        if found is None:
            return None
        email, password_hash = found
        if email in self._accounts:
            return None
        tenant = await self.create_tenant(email)
        self._accounts[email] = (tenant.id, password_hash)
        for other in [h for h, s in self._signups.items() if s[0] == email]:
            del self._signups[other]
        return tenant

    async def account_exists(self, email: str) -> bool:
        return email in self._accounts

    async def account_login(self, email: str) -> tuple[str, str] | None:
        return self._accounts.get(email)

    async def add_account(self, tenant_id: str, email: str, password_hash: str) -> None:
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise AccountRefused(f"no such tenant: {tenant_id}")
        if not tenant.is_root:
            raise AccountRefused(f"{tenant_id} is not a root tenant")
        if any(t == tenant_id for t, _ in self._accounts.values()):
            raise AccountRefused(f"{tenant_id} already has an account")
        if email in self._accounts:
            raise AccountRefused(f"{email} is already taken")
        self._accounts[email] = (tenant_id, password_hash)

    async def create_session(
        self, tenant_id: str, token_hash: str, expires_at: datetime
    ) -> None:
        self._sessions[token_hash] = (tenant_id, expires_at)

    async def tenant_for_session(self, token_hash: str) -> Tenant | None:
        found = self._sessions.get(token_hash)
        if found is None or found[1] <= datetime.now(UTC):
            return None
        return self._tenants.get(found[0])

    async def delete_session(self, token_hash: str) -> None:
        self._sessions.pop(token_hash, None)


async def resolver_for(store: Store, tenant_id: str, connectors=None):
    """Load a tenant's credentials, then return a synchronous resolver.

    The SDK calls a CredentialResolver synchronously deep inside
    fetch_rows, and reachability checks cannot await at all. So the
    credentials are fetched once here and the returned closure reads from
    a plain dict — which is the seam fetch_rows, the query session and the
    reconciliation runner already expect, unchanged.

    Loading up front also bounds the exposure: plaintext exists for the
    life of one request rather than being fetchable at arbitrary depth.
    """
    from okwan_core import all_connectors

    loaded: dict[tuple[str, str], str] = {}
    for connector in connectors or all_connectors():
        creds = await store.credentials_for(
            tenant_id, connector.name, connector.auth.required_fields
        )
        for field, value in creds.items():
            loaded[(connector.name, field)] = value

    def resolve(connector_name: str, fields: tuple[str, ...]) -> dict[str, str]:
        return {f: loaded.get((connector_name, f), "") for f in fields}

    return resolve
