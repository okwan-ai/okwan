"""Durable credential store on Postgres.

Same Store interface as MemoryStore, so nothing above it changes. The
vault belongs in its own database rather than alongside business data:
`postgres.sql.query` runs caller-supplied read-only SQL against whatever
DSN it is handed, and tenant secrets should not be reachable from a
connector at all.

Key lookup is by hash with a partial index on active keys, so
authenticating a request stays O(1) as tenants accumulate.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg

from . import apikey
from .crypto import new_key, open_sealed, seal
from .keys import MasterKeyProvider
from .models import ApiKey, SealedCredential, Tenant

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def _tenant(row) -> Tenant:
    return Tenant(
        id=row["id"],
        name=row["name"],
        parent_id=row["parent_id"],
        created_at=row["created_at"],
    )


class PostgresStore:
    """Async store. Call `await connect()` before use."""

    def __init__(self, dsn: str, master: MasterKeyProvider) -> None:
        self._dsn = dsn
        self._master = master
        self._pool: asyncpg.Pool | None = None

    async def connect(self, migrate: bool = True) -> "PostgresStore":
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        if migrate:
            async with self._pool.acquire() as con:
                await con.execute(SCHEMA)
        return self

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("store is not connected — await store.connect()")
        return self._pool

    # ── tenants ─────────────────────────────────────────────────────

    async def create_tenant(self, name: str, parent_id: str | None = None) -> Tenant:
        """Create a tenant, optionally as a child of an existing one.

        The parent must exist; the foreign key enforces that rather than a
        prior read, so two concurrent creates cannot both pass a check
        against a parent one of them is deleting.
        """
        tenant_id = f"ten_{uuid.uuid4().hex[:16]}"
        try:
            row = await self.pool.fetchrow(
                "INSERT INTO tenants (id, name, parent_id) VALUES ($1, $2, $3) "
                "RETURNING id, name, parent_id, created_at",
                tenant_id, name, parent_id,
            )
        except asyncpg.ForeignKeyViolationError:
            raise KeyError(f"unknown parent tenant {parent_id!r}") from None
        return _tenant(row)

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        row = await self.pool.fetchrow(
            "SELECT id, name, parent_id, created_at FROM tenants WHERE id = $1",
            tenant_id,
        )
        return None if row is None else _tenant(row)

    async def children_of(self, tenant_id: str) -> list[Tenant]:
        rows = await self.pool.fetch(
            "SELECT id, name, parent_id, created_at FROM tenants "
            "WHERE parent_id = $1 ORDER BY created_at",
            tenant_id,
        )
        return [_tenant(r) for r in rows]

    # ── api keys ────────────────────────────────────────────────────

    async def issue_key(self, tenant_id: str) -> tuple[str, ApiKey]:
        full, prefix, hash_hex = apikey.generate()
        key_id = f"key_{uuid.uuid4().hex[:16]}"
        row = await self.pool.fetchrow(
            "INSERT INTO api_keys (id, tenant_id, prefix, hash_hex) "
            "VALUES ($1, $2, $3, $4) RETURNING created_at",
            key_id, tenant_id, prefix, hash_hex,
        )
        return full, ApiKey(
            id=key_id, tenant_id=tenant_id, prefix=prefix,
            hash_hex=hash_hex, created_at=row["created_at"],
        )

    async def revoke_key(self, key_id: str) -> None:
        result = await self.pool.execute(
            "UPDATE api_keys SET revoked_at = now() "
            "WHERE id = $1 AND revoked_at IS NULL",
            key_id,
        )
        if result.endswith("0"):
            raise KeyError(f"unknown or already-revoked key {key_id!r}")

    async def tenant_for_key(self, full_key: str) -> Tenant | None:
        """Indexed hash lookup — no scan over keys, no timing signal."""
        row = await self.pool.fetchrow(
            "SELECT t.id, t.name, t.parent_id, t.created_at "
            "FROM api_keys k JOIN tenants t ON t.id = k.tenant_id "
            "WHERE k.hash_hex = $1 AND k.revoked_at IS NULL",
            apikey.hash_key(full_key),
        )
        return None if row is None else _tenant(row)

    # ── credentials ─────────────────────────────────────────────────

    async def put_credential(
        self, tenant_id: str, connector: str, field_name: str, value: str
    ) -> None:
        data_key = new_key()
        record = SealedCredential(
            tenant_id=tenant_id, connector=connector, field_name=field_name,
            ciphertext=b"", wrapped_key=b"", key_id=self._master.key_id,
        )
        await self.pool.execute(
            "INSERT INTO credentials "
            "(tenant_id, connector, field_name, ciphertext, wrapped_key, key_id) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (tenant_id, connector, field_name) DO UPDATE SET "
            "ciphertext = EXCLUDED.ciphertext, "
            "wrapped_key = EXCLUDED.wrapped_key, "
            "key_id = EXCLUDED.key_id, updated_at = now()",
            tenant_id, connector, field_name,
            seal(data_key, value.encode(), aad=record.aad),
            self._master.wrap(data_key),
            self._master.key_id,
        )

    async def credentials_for(
        self, tenant_id: str, connector: str, fields: tuple[str, ...]
    ) -> dict[str, str]:
        rows = await self.pool.fetch(
            "SELECT field_name, ciphertext, wrapped_key, key_id FROM credentials "
            "WHERE tenant_id = $1 AND connector = $2 AND field_name = ANY($3)",
            tenant_id, connector, list(fields),
        )
        found = {r["field_name"]: r for r in rows}
        out: dict[str, str] = {}
        for name in fields:
            row = found.get(name)
            if row is None:
                out[name] = ""
                continue
            record = SealedCredential(
                tenant_id=tenant_id, connector=connector, field_name=name,
                ciphertext=bytes(row["ciphertext"]),
                wrapped_key=bytes(row["wrapped_key"]),
                key_id=row["key_id"],
            )
            data_key = self._master.unwrap(record.wrapped_key)
            out[name] = open_sealed(data_key, record.ciphertext, aad=record.aad).decode()
        return out

    async def delete_credential(
        self, tenant_id: str, connector: str, field_name: str
    ) -> None:
        await self.pool.execute(
            "DELETE FROM credentials "
            "WHERE tenant_id = $1 AND connector = $2 AND field_name = $3",
            tenant_id, connector, field_name,
        )

    async def connectors_configured(self, tenant_id: str) -> dict[str, list[str]]:
        rows = await self.pool.fetch(
            "SELECT connector, field_name FROM credentials WHERE tenant_id = $1 "
            "ORDER BY connector, field_name",
            tenant_id,
        )
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["connector"], []).append(r["field_name"])
        return out
