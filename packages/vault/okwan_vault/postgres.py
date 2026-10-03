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
from datetime import datetime
from pathlib import Path

import asyncpg

from . import apikey
from .accounts import AccountRefused
from .crypto import new_key, open_sealed, seal
from .keys import MasterKeyProvider
from .models import ApiKey, SealedCredential, Tenant
from .usage import DEFAULT_PLAN, PLANS, hour_bucket

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class _Lost(Exception):
    """Raised inside a transaction to roll it back."""


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

    async def connect(self, migrate: bool = True) -> PostgresStore:
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

    async def list_tenants(self) -> list[Tenant]:
        rows = await self.pool.fetch(
            "SELECT id, name, parent_id, created_at FROM tenants ORDER BY created_at"
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

    # ── usage ───────────────────────────────────────────────────────

    async def record_request(self, tenant_id: str, surface: str) -> None:
        """Increment the current hour's counter.

        Upsert rather than insert-per-request: a row per API call is write
        amplification the moment anyone has traffic, and no plan bills by
        the second.
        """
        await self.pool.execute(
            "INSERT INTO usage (tenant_id, hour, surface, requests) "
            "VALUES ($1, $2, $3, 1) "
            "ON CONFLICT (tenant_id, hour, surface) DO UPDATE SET "
            "requests = usage.requests + 1",
            tenant_id, hour_bucket(), surface,
        )

    async def usage_since(self, root_id: str, since) -> int:
        """Requests across a tenant and everything it provisioned.

        Recursive rather than a join on parent_id: the hierarchy is two
        levels today but the query should not be the thing that breaks
        when a reseller tier appears.
        """
        return await self.pool.fetchval(
            """
            WITH RECURSIVE subtree AS (
                SELECT id FROM tenants WHERE id = $1
                UNION ALL
                SELECT t.id FROM tenants t JOIN subtree s ON t.parent_id = s.id
            )
            SELECT COALESCE(SUM(u.requests), 0)
            FROM usage u JOIN subtree s ON s.id = u.tenant_id
            WHERE u.hour >= $2
            """,
            root_id, since,
        )

    async def usage_buckets(
        self, root_id: str, since
    ) -> list[tuple[str, datetime, str, int]]:
        """The hourly counters behind `usage_since`, per tenant and surface,
        so a dashboard can show where a subtree's allowance went."""
        rows = await self.pool.fetch(
            """
            WITH RECURSIVE subtree AS (
                SELECT id FROM tenants WHERE id = $1
                UNION ALL
                SELECT t.id FROM tenants t JOIN subtree s ON t.parent_id = s.id
            )
            SELECT u.tenant_id, u.hour, u.surface, u.requests
            FROM usage u JOIN subtree s ON s.id = u.tenant_id
            WHERE u.hour >= $2
            ORDER BY u.tenant_id, u.hour, u.surface
            """,
            root_id, since,
        )
        return [(r["tenant_id"], r["hour"], r["surface"], r["requests"]) for r in rows]

    async def get_plan(self, tenant_id: str) -> tuple[str, int]:
        row = await self.pool.fetchrow(
            "SELECT name, monthly_requests FROM plans WHERE tenant_id = $1",
            tenant_id,
        )
        if row is None:
            return DEFAULT_PLAN, PLANS[DEFAULT_PLAN]
        return row["name"], row["monthly_requests"]

    async def set_plan(self, tenant_id: str, name: str) -> None:
        if name not in PLANS:
            raise ValueError(f"unknown plan {name!r}; known: {', '.join(PLANS)}")
        await self.pool.execute(
            "INSERT INTO plans (tenant_id, name, monthly_requests) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (tenant_id) DO UPDATE SET "
            "name = EXCLUDED.name, "
            "monthly_requests = EXCLUDED.monthly_requests, updated_at = now()",
            tenant_id, name, PLANS[name],
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

    async def key_owner(self, key_id: str) -> str | None:
        return await self.pool.fetchval(
            "SELECT tenant_id FROM api_keys WHERE id = $1", key_id
        )

    # ── accounts ────────────────────────────────────────────────────

    async def add_signup(
        self, email: str, password_hash: str, token_hash: str, expires_at: datetime
    ) -> None:
        await self.pool.execute(
            "INSERT INTO signups (token_hash, email, password_hash, expires_at) "
            "VALUES ($1, $2, $3, $4)",
            token_hash, email, password_hash, expires_at,
        )

    async def signup_for(self, token_hash: str) -> tuple[str, str] | None:
        row = await self.pool.fetchrow(
            "SELECT email, password_hash FROM signups "
            "WHERE token_hash = $1 AND expires_at > now()",
            token_hash,
        )
        return None if row is None else (row["email"], row["password_hash"])

    async def complete_signup(self, token_hash: str) -> Tenant | None:
        """Consume the token and create the root tenant, or neither.

        Verifications for one address are serialized by an advisory lock
        on the address, taken before any row is touched. Without it, two
        tokens for one address deadlock: each deletes its own signup row,
        then the winner's sweep of the address's other signups waits on the
        loser's row while the loser waits on the winner's account insert.
        Under the lock, the loser finds its token already swept and gets
        None. The DELETE ... RETURNING guards one token used twice, and the
        accounts primary key remains the last word on one tenant per address.
        """
        try:
            return await self._complete_signup(token_hash)
        except _Lost:
            # The insert raced a verification for the same address; the
            # transaction rolled back, so no orphan tenant remains.
            return None

    async def _complete_signup(self, token_hash: str) -> Tenant | None:
        async with self.pool.acquire() as con, con.transaction():
            email = await con.fetchval(
                "SELECT email FROM signups WHERE token_hash = $1", token_hash
            )
            if email is None:
                return None
            await con.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", email
            )
            row = await con.fetchrow(
                "DELETE FROM signups WHERE token_hash = $1 AND expires_at > now() "
                "RETURNING email, password_hash",
                token_hash,
            )
            if row is None:
                return None
            email = row["email"]
            if await con.fetchval("SELECT 1 FROM accounts WHERE email = $1", email):
                return None
            tenant = _tenant(await con.fetchrow(
                "INSERT INTO tenants (id, name, parent_id) VALUES ($1, $2, NULL) "
                "RETURNING id, name, parent_id, created_at",
                f"ten_{uuid.uuid4().hex[:16]}", email,
            ))
            try:
                await con.execute(
                    "INSERT INTO accounts (email, tenant_id, password_hash) "
                    "VALUES ($1, $2, $3)",
                    email, tenant.id, row["password_hash"],
                )
            except asyncpg.UniqueViolationError:
                raise _Lost() from None
            await con.execute("DELETE FROM signups WHERE email = $1", email)
            return tenant

    async def account_exists(self, email: str) -> bool:
        return bool(await self.pool.fetchval(
            "SELECT 1 FROM accounts WHERE email = $1", email
        ))

    async def account_login(self, email: str) -> tuple[str, str] | None:
        row = await self.pool.fetchrow(
            "SELECT tenant_id, password_hash FROM accounts WHERE email = $1", email
        )
        return None if row is None else (row["tenant_id"], row["password_hash"])

    async def add_account(self, tenant_id: str, email: str, password_hash: str) -> None:
        """Attach a login to an existing root tenant.

        The checks give the operator a specific reason; the accounts keys
        (email, and tenant_id UNIQUE) are what hold under a race, and a
        violation is mapped back to the same reasons.
        """
        async with self.pool.acquire() as con, con.transaction():
            row = await con.fetchrow(
                "SELECT parent_id FROM tenants WHERE id = $1 FOR UPDATE", tenant_id
            )
            if row is None:
                raise AccountRefused(f"no such tenant: {tenant_id}")
            if row["parent_id"] is not None:
                raise AccountRefused(f"{tenant_id} is not a root tenant")
            if await con.fetchval("SELECT 1 FROM accounts WHERE tenant_id = $1", tenant_id):
                raise AccountRefused(f"{tenant_id} already has an account")
            try:
                await con.execute(
                    "INSERT INTO accounts (email, tenant_id, password_hash) "
                    "VALUES ($1, $2, $3)",
                    email, tenant_id, password_hash,
                )
            except asyncpg.UniqueViolationError as e:
                if e.constraint_name == "accounts_pkey":
                    raise AccountRefused(f"{email} is already taken") from None
                raise AccountRefused(f"{tenant_id} already has an account") from None

    async def create_session(
        self, tenant_id: str, token_hash: str, expires_at: datetime
    ) -> None:
        await self.pool.execute(
            "INSERT INTO sessions (token_hash, tenant_id, expires_at) "
            "VALUES ($1, $2, $3)",
            token_hash, tenant_id, expires_at,
        )

    async def tenant_for_session(self, token_hash: str) -> Tenant | None:
        row = await self.pool.fetchrow(
            "SELECT t.id, t.name, t.parent_id, t.created_at "
            "FROM sessions s JOIN tenants t ON t.id = s.tenant_id "
            "WHERE s.token_hash = $1 AND s.expires_at > now()",
            token_hash,
        )
        return None if row is None else _tenant(row)

    async def delete_session(self, token_hash: str) -> None:
        await self.pool.execute(
            "DELETE FROM sessions WHERE token_hash = $1", token_hash
        )
