"""Durable credential store on Postgres.

Same Store interface as MemoryStore, so nothing above it changes. The
vault belongs in its own database rather than alongside business data:
`postgres.sql.query` runs caller-supplied read-only SQL against whatever
DSN it is handed, and tenant secrets should not be reachable from a
connector at all.

Key lookup is by hash with a partial index on active keys, so
authenticating a request stays O(1) as tenants accumulate.

Tenants are kept apart twice. Every query names its tenant in a WHERE
clause, and every read or write of a tenant's own data also runs as the
restricted TENANT_ROLE inside a transaction scoped to that tenant, where
row-level security (schema.sql) hides every other tenant's rows. Either
wall alone keeps a tenant's data from another; tests/test_tenant_rls.py
proves each one, and scripts/plant_leaks.py shows each holding while the
other is broken.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import asyncpg

from . import apikey
from .accounts import AccountRefused
from .crypto import new_key, open_sealed, seal
from .keys import MasterKeyProvider
from .models import (
    RUNS_KEPT,
    SUPPORT_KEPT_DAYS,
    ApiKey,
    RunRecord,
    SealedCredential,
    SupportConversation,
    SupportTicket,
    Tenant,
)
from .usage import DEFAULT_PLAN, NOT_ROOT_PLAN, PLANS, PlanRefused, hour_bucket

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

#: The role every read and write of a tenant's own data runs as. It cannot
#: log in, owns nothing, does not bypass row-level security, and holds
#: privileges on TENANT_TABLES only.
TENANT_ROLE = "okwan_tenant"

#: Tables holding a tenant's own data. Each has row-level security with one
#: policy: a row is visible, and may be written, only while its tenant_id
#: is in the transaction's scope.
TENANT_TABLES = (
    "credentials", "usage", "plans", "reconciliation_runs",
    "support_conversations", "support_tickets", "support_replies",
)


class IsolationUnavailable(RuntimeError):
    """The database cannot enforce tenant isolation; refuse to serve."""


async def _enter_scope(con, tenant_ids: Iterable[str]) -> None:
    """Become TENANT_ROLE, limited to these tenants, until the transaction ends.

    Both settings are transaction-local, so a pooled connection goes back
    to the pool as the login role with no scope: nothing one request sets
    can be inherited by the next. One round trip for both.
    """
    ids = list(tenant_ids)
    if any("," in t for t in ids):
        raise ValueError("a tenant id cannot contain a comma")
    await con.execute(
        "SELECT set_config('okwan.tenant_ids', $1, true), set_config('role', $2, true)",
        ",".join(ids), TENANT_ROLE,
    )


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
            await self._migrate()
        await self._check_isolation()
        return self

    async def _migrate(self) -> None:
        """Run schema.sql, retrying a deadlock.

        Every step in it checks before it changes anything, so against a
        current database a start takes no exclusive table lock. The first
        start after a schema change does, and can meet autovacuum or an
        instance still serving during a deploy; Postgres then aborts one
        side, and here that side tries again.
        """
        for attempt in range(1, 4):
            try:
                async with self.pool.acquire() as con:
                    await con.execute(SCHEMA)
                return
            except asyncpg.DeadlockDetectedError:
                if attempt == 3:
                    raise
                await asyncio.sleep(0.5 * attempt)

    async def _check_isolation(self) -> None:
        """Fail closed: never serve while the database could not keep
        tenants apart. Checked on every connect, migrating or not."""
        async with self.pool.acquire() as con:
            role = await con.fetchrow(
                "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = $1",
                TENANT_ROLE,
            )
            unprotected = await con.fetch(
                "SELECT c.relname FROM pg_class c "
                "WHERE c.relname = ANY($1::text[]) AND c.relnamespace = current_schema()::regnamespace "
                "AND NOT (c.relrowsecurity AND EXISTS ("
                " SELECT 1 FROM pg_policies p WHERE p.schemaname = current_schema()"
                " AND p.tablename = c.relname AND p.policyname = 'tenant_scope'))",
                list(TENANT_TABLES),
            )
        if role is None:
            raise IsolationUnavailable(f"role {TENANT_ROLE} does not exist; run the migration")
        if role["rolsuper"] or role["rolbypassrls"] or role["rolcanlogin"]:
            raise IsolationUnavailable(
                f"role {TENANT_ROLE} must be NOSUPERUSER NOBYPASSRLS NOLOGIN"
            )
        if unprotected:
            names = ", ".join(r["relname"] for r in unprotected)
            raise IsolationUnavailable(f"row-level security is off for: {names}")

    @asynccontextmanager
    async def _scoped(self, *tenant_ids: str) -> AsyncIterator[asyncpg.Connection]:
        """A connection in a transaction that can see only these tenants' data."""
        async with self.pool.acquire() as con, con.transaction():
            await _enter_scope(con, tenant_ids)
            yield con

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
        async with self._scoped(tenant_id) as con:
            await con.execute(
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
        return (await self.credentials_many(tenant_id, {connector: fields}))[connector]

    async def credentials_many(
        self, tenant_id: str, wanted: dict[str, tuple[str, ...]]
    ) -> dict[str, dict[str, str]]:
        """Several connectors' credentials in one scoped read: what a query
        or reconciliation loads up front. A missing field reads as ""."""
        async with self._scoped(tenant_id) as con:
            rows = await con.fetch(
                "SELECT connector, field_name, ciphertext, wrapped_key, key_id FROM credentials "
                "WHERE tenant_id = $1 AND connector = ANY($2::text[])",
                tenant_id, list(wanted),
            )
        found = {(r["connector"], r["field_name"]): r for r in rows}
        out: dict[str, dict[str, str]] = {}
        for connector, fields in wanted.items():
            out[connector] = {}
            for name in fields:
                row = found.get((connector, name))
                out[connector][name] = "" if row is None else self._open(tenant_id, row)
        return out

    def _open(self, tenant_id: str, row) -> str:
        record = SealedCredential(
            tenant_id=tenant_id, connector=row["connector"], field_name=row["field_name"],
            ciphertext=bytes(row["ciphertext"]),
            wrapped_key=bytes(row["wrapped_key"]),
            key_id=row["key_id"],
        )
        data_key = self._master.unwrap(record.wrapped_key)
        return open_sealed(data_key, record.ciphertext, aad=record.aad).decode()

    async def delete_credential(
        self, tenant_id: str, connector: str, field_name: str
    ) -> None:
        async with self._scoped(tenant_id) as con:
            await con.execute(
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
        async with self._scoped(tenant_id) as con:
            await con.execute(
                "INSERT INTO usage (tenant_id, hour, surface, requests) "
                "VALUES ($1, $2, $3, 1) "
                "ON CONFLICT (tenant_id, hour, surface) DO UPDATE SET "
                "requests = usage.requests + 1",
                tenant_id, hour_bucket(), surface,
            )

    async def usage_since(self, root_id: str, since) -> int:
        """Requests across a tenant and everything it provisioned.

        The subtree is read as the login role (the hierarchy is identity,
        not a tenant's data), then the counters as TENANT_ROLE scoped to
        exactly that subtree, in one transaction.
        """
        async with self.pool.acquire() as con, con.transaction():
            ids = await _subtree(con, root_id)
            await _enter_scope(con, ids)
            return await con.fetchval(
                "SELECT COALESCE(SUM(requests), 0) FROM usage "
                "WHERE tenant_id = ANY($1::text[]) AND hour >= $2",
                ids, since,
            )

    async def usage_buckets(
        self, root_id: str, since
    ) -> list[tuple[str, datetime, str, int]]:
        """The hourly counters behind `usage_since`, per tenant and surface,
        so a dashboard can show where a subtree's allowance went."""
        async with self.pool.acquire() as con, con.transaction():
            ids = await _subtree(con, root_id)
            await _enter_scope(con, ids)
            rows = await con.fetch(
                "SELECT tenant_id, hour, surface, requests FROM usage "
                "WHERE tenant_id = ANY($1::text[]) AND hour >= $2 "
                "ORDER BY tenant_id, hour, surface",
                ids, since,
            )
        return [(r["tenant_id"], r["hour"], r["surface"], r["requests"]) for r in rows]

    # ── reconciliation runs ─────────────────────────────────────────

    async def add_run(
        self, tenant_id: str, *, kind: str, name: str, surface: str, status: str,
        started_at: datetime, finished_at: datetime, summary: dict | None,
        rows: list | None, error: str | None,
    ) -> RunRecord:
        """Insert, then prune to the newest RUNS_KEPT for the same tenant,
        kind and name, in one transaction: history is bounded by
        construction, never by a job that might not run."""
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        try:
            async with self._scoped(tenant_id) as con:
                row = await con.fetchrow(
                    "INSERT INTO reconciliation_runs (id, tenant_id, kind, name, surface, "
                    "status, started_at, finished_at, summary, rows, error) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11) "
                    f"RETURNING {_RUN_COLS}",
                    run_id, tenant_id, kind, name, surface, status, started_at, finished_at,
                    _dumps(summary), _dumps(rows), error,
                )
                await con.execute(
                    "DELETE FROM reconciliation_runs WHERE tenant_id = $1 AND kind = $2 "
                    "AND name = $3 AND id NOT IN ("
                    " SELECT id FROM reconciliation_runs"
                    " WHERE tenant_id = $1 AND kind = $2 AND name = $3"
                    " ORDER BY finished_at DESC, id DESC LIMIT $4)",
                    tenant_id, kind, name, RUNS_KEPT,
                )
        except asyncpg.ForeignKeyViolationError:
            raise KeyError(f"unknown tenant {tenant_id!r}") from None
        return _run(row)

    async def list_runs(
        self, tenant_id: str, name: str | None = None, limit: int = 20
    ) -> list[RunRecord]:
        async with self._scoped(tenant_id) as con:
            rows = await con.fetch(
                f"SELECT {_RUN_COLS_NO_ROWS} FROM reconciliation_runs "
                "WHERE tenant_id = $1 AND ($2::text IS NULL OR name = $2) "
                "ORDER BY finished_at DESC, id DESC LIMIT $3",
                tenant_id, name, limit,
            )
        return [_run(r) for r in rows]

    async def get_run(self, tenant_id: str, run_id: str) -> RunRecord | None:
        async with self._scoped(tenant_id) as con:
            row = await con.fetchrow(
                f"SELECT {_RUN_COLS} FROM reconciliation_runs WHERE tenant_id = $1 AND id = $2",
                tenant_id, run_id,
            )
        return None if row is None else _run(row)

    async def latest_runs(
        self, tenant_ids: list[str], kind: str, name: str
    ) -> dict[str, RunRecord]:
        async with self._scoped(*tenant_ids) as con:
            rows = await con.fetch(
                f"SELECT DISTINCT ON (tenant_id) {_RUN_COLS} FROM reconciliation_runs "
                "WHERE tenant_id = ANY($1::text[]) AND kind = $2 AND name = $3 "
                "ORDER BY tenant_id, finished_at DESC, id DESC",
                list(tenant_ids), kind, name,
            )
        return {r["tenant_id"]: _run(r) for r in rows}

    async def get_plan(self, tenant_id: str) -> tuple[str, int]:
        async with self._scoped(tenant_id) as con:
            row = await con.fetchrow(
                "SELECT name, monthly_requests FROM plans WHERE tenant_id = $1",
                tenant_id,
            )
        if row is None:
            return DEFAULT_PLAN, PLANS[DEFAULT_PLAN]
        return row["name"], row["monthly_requests"]

    async def set_plan(self, tenant_id: str, name: str) -> None:
        """Set a root tenant's plan. A child's is refused, not stored.

        The tenant row is locked for the check so it cannot gain a parent
        between the check and the write.
        """
        if name not in PLANS:
            raise PlanRefused(f"unknown plan {name!r}; known: {', '.join(PLANS)}")
        async with self.pool.acquire() as con, con.transaction():
            row = await con.fetchrow(
                "SELECT parent_id FROM tenants WHERE id = $1 FOR SHARE", tenant_id
            )
            if row is None:
                raise PlanRefused(f"no such tenant: {tenant_id}")
            if row["parent_id"] is not None:
                raise PlanRefused(f"{tenant_id} is not a root tenant; {NOT_ROOT_PLAN}")
            await _enter_scope(con, [tenant_id])
            await con.execute(
                "INSERT INTO plans (tenant_id, name, monthly_requests) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "name = EXCLUDED.name, "
                "monthly_requests = EXCLUDED.monthly_requests, updated_at = now()",
                tenant_id, name, PLANS[name],
            )

    async def connectors_configured(self, tenant_id: str) -> dict[str, list[str]]:
        async with self._scoped(tenant_id) as con:
            rows = await con.fetch(
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

    async def delete_tenant(self, tenant_id: str) -> list[str]:
        """Delete a tenant, every tenant under it and everything they hold,
        in one transaction. Every tenant table cascades from `tenants`; a
        signup for the same address is not tenant data, so it goes by
        address. The operator log keeps its entries: they name ids, not
        contents. The ids deleted; empty when there was no such tenant."""
        async with self.pool.acquire() as con, con.transaction():
            ids = [r["id"] for r in await con.fetch(
                "WITH RECURSIVE sub AS (SELECT id FROM tenants WHERE id = $1 "
                "UNION ALL SELECT t.id FROM tenants t JOIN sub ON t.parent_id = sub.id) "
                "SELECT id FROM sub", tenant_id)]
            if not ids:
                return []
            emails = [r["email"] for r in await con.fetch(
                "SELECT email FROM accounts WHERE tenant_id = ANY($1::text[])", ids)]
            await con.execute("DELETE FROM signups WHERE email = ANY($1::text[])", emails)
            await con.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        return sorted(ids)

    async def reset_password(self, email: str, password_hash: str) -> str:
        """Replace a login's password and end every session it holds.

        One transaction: a session opened with the old password must not
        outlive the change. A tenant has at most one login, so the
        tenant's sessions are exactly this login's.
        """
        async with self.pool.acquire() as con, con.transaction():
            tenant_id = await con.fetchval(
                "UPDATE accounts SET password_hash = $2 WHERE email = $1 "
                "RETURNING tenant_id",
                email, password_hash,
            )
            if tenant_id is None:
                raise AccountRefused(f"no account for {email}")
            await con.execute("DELETE FROM sessions WHERE tenant_id = $1", tenant_id)
        return tenant_id

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


    # ── support ─────────────────────────────────────────────────────

    async def keys_for(self, tenant_id: str) -> list[ApiKey]:
        """A tenant's keys, newest first, as the login role: api_keys is an
        identity table. The hash is never selected."""
        rows = await self.pool.fetch(
            "SELECT id, tenant_id, prefix, created_at, revoked_at FROM api_keys "
            "WHERE tenant_id = $1 ORDER BY created_at DESC",
            tenant_id,
        )
        return [ApiKey(id=r["id"], tenant_id=r["tenant_id"], prefix=r["prefix"], hash_hex="",
                       created_at=r["created_at"], revoked_at=r["revoked_at"]) for r in rows]

    async def get_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> SupportConversation | None:
        async with self._scoped(tenant_id) as con:
            row = await con.fetchrow(
                "SELECT id, tenant_id, messages, turns, created_at, updated_at "
                "FROM support_conversations WHERE tenant_id = $1 AND id = $2",
                tenant_id, conversation_id,
            )
        return None if row is None else _conversation(row)

    async def save_conversation(
        self, tenant_id: str, conversation_id: str | None, messages: list, turns: int
    ) -> str:
        """Insert or replace one conversation, then drop the tenant's
        conversations untouched for SUPPORT_KEPT_DAYS, in one transaction."""
        cid = conversation_id or f"sup_{uuid.uuid4().hex[:16]}"
        try:
            async with self._scoped(tenant_id) as con:
                updated = await con.execute(
                    "INSERT INTO support_conversations (id, tenant_id, messages, turns) "
                    "VALUES ($1, $2, $3::jsonb, $4) "
                    "ON CONFLICT (id) DO UPDATE SET messages = EXCLUDED.messages, "
                    "turns = EXCLUDED.turns, updated_at = now() "
                    "WHERE support_conversations.tenant_id = EXCLUDED.tenant_id",
                    cid, tenant_id, _dumps(messages), turns,
                )
                if updated.endswith(" 0"):
                    raise KeyError(f"unknown conversation {conversation_id!r}")
                await con.execute(
                    "DELETE FROM support_conversations WHERE tenant_id = $1 "
                    "AND updated_at < now() - make_interval(days => $2)",
                    tenant_id, SUPPORT_KEPT_DAYS,
                )
        except asyncpg.ForeignKeyViolationError:
            raise KeyError(f"unknown tenant {tenant_id!r}") from None
        return cid

    async def add_ticket(
        self, tenant_id: str, *, conversation_id: str | None, severity: str,
        category: str, summary: str, notified: bool, ticket_id: str | None = None,
        brief: dict | None = None,
    ) -> SupportTicket:
        try:
            async with self._scoped(tenant_id) as con:
                row = await con.fetchrow(
                    "INSERT INTO support_tickets (id, tenant_id, conversation_id, severity, "
                    "category, summary, notified, brief) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb) "
                    f"RETURNING {_TICKET_COLS}",
                    ticket_id or f"tkt_{uuid.uuid4().hex[:16]}", tenant_id, conversation_id, severity,
                    category, summary, notified, _dumps(brief),
                )
        except asyncpg.ForeignKeyViolationError:
            raise KeyError(f"unknown tenant {tenant_id!r}") from None
        return _ticket(row)

    async def list_tickets(self, limit: int = 50, status: str | None = None) -> list[SupportTicket]:
        """Every tenant's tickets, newest first: for the operator only, as
        the login role. Reached only by the operator CLI and the operator
        console, never by a customer route."""
        rows = await self.pool.fetch(
            f"SELECT {_TICKET_COLS} FROM support_tickets "
            "WHERE ($2::text IS NULL OR status = $2) ORDER BY created_at DESC LIMIT $1",
            limit, status,
        )
        return [_ticket(r) for r in rows]

    async def get_ticket(self, ticket_id: str) -> SupportTicket | None:
        """One ticket from any tenant: for the operator only, as the login role."""
        row = await self.pool.fetchrow(
            f"SELECT {_TICKET_COLS} FROM support_tickets WHERE id = $1", ticket_id)
        return None if row is None else _ticket(row)

    async def set_ticket_status(self, tenant_id: str, ticket_id: str, status: str) -> None:
        async with self._scoped(tenant_id) as con:
            done = await con.execute(
                "UPDATE support_tickets SET status = $3, "
                "resolved_at = CASE WHEN $3 = 'resolved' THEN now() ELSE NULL END "
                "WHERE tenant_id = $1 AND id = $2",
                tenant_id, ticket_id, status,
            )
        if done.endswith(" 0"):
            raise KeyError(f"unknown ticket {ticket_id!r}")

    async def add_reply(
        self, tenant_id: str, ticket_id: str, operator: str, body: str, sent: bool
    ) -> dict:
        async with self._scoped(tenant_id) as con:
            row = await con.fetchrow(
                "INSERT INTO support_replies (id, tenant_id, ticket_id, operator, body, sent) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "RETURNING id, tenant_id, ticket_id, operator, body, sent, created_at",
                f"rpl_{uuid.uuid4().hex[:16]}", tenant_id, ticket_id, operator, body, sent,
            )
        return dict(row)

    async def replies_for(self, tenant_id: str, ticket_id: str) -> list[dict]:
        async with self._scoped(tenant_id) as con:
            rows = await con.fetch(
                "SELECT id, tenant_id, ticket_id, operator, body, sent, created_at "
                "FROM support_replies WHERE tenant_id = $1 AND ticket_id = $2 "
                "ORDER BY created_at",
                tenant_id, ticket_id,
            )
        return [dict(r) for r in rows]

    async def put_operator_factor(self, email: str, secret: str) -> None:
        data_key = new_key()
        record = SealedCredential(tenant_id="operator", connector="totp", field_name=email,
                                  ciphertext=b"", wrapped_key=b"", key_id=self._master.key_id)
        await self.pool.execute(
            "INSERT INTO operator_factors (email, ciphertext, wrapped_key, key_id) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (email) DO UPDATE SET "
            "ciphertext = EXCLUDED.ciphertext, wrapped_key = EXCLUDED.wrapped_key, "
            "key_id = EXCLUDED.key_id, created_at = now()",
            email, seal(data_key, secret.encode(), aad=record.aad),
            self._master.wrap(data_key), self._master.key_id,
        )

    async def operator_factor(self, email: str) -> str | None:
        row = await self.pool.fetchrow(
            "SELECT ciphertext, wrapped_key, key_id FROM operator_factors WHERE email = $1", email)
        if row is None:
            return None
        record = SealedCredential(tenant_id="operator", connector="totp", field_name=email,
                                  ciphertext=bytes(row["ciphertext"]),
                                  wrapped_key=bytes(row["wrapped_key"]), key_id=row["key_id"])
        data_key = self._master.unwrap(record.wrapped_key)
        return open_sealed(data_key, record.ciphertext, aad=record.aad).decode()

    async def set_session_operator(self, token_hash: str, until: datetime | None) -> None:
        await self.pool.execute(
            "UPDATE sessions SET operator_until = $2 WHERE token_hash = $1", token_hash, until)

    async def session_operator_until(self, token_hash: str) -> datetime | None:
        return await self.pool.fetchval(
            "SELECT operator_until FROM sessions WHERE token_hash = $1 AND expires_at > now()",
            token_hash,
        )

    async def log_operator(
        self, operator: str, action: str, target: str | None, detail: dict | None
    ) -> None:
        await self.pool.execute(
            "INSERT INTO operator_actions (operator, action, target, detail) "
            "VALUES ($1, $2, $3, $4::jsonb)",
            operator, action, target, _dumps(detail),
        )

    async def operator_log(self, limit: int = 100) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT at, operator, action, target, detail FROM operator_actions "
            "ORDER BY id DESC LIMIT $1", limit)
        return [{**dict(r), "detail": _loads(r["detail"])} for r in rows]

    async def account_email(self, tenant_id: str) -> str | None:
        """The sign-in address of the tenant's login, or of the nearest
        ancestor's: a merchant has no login, its account does. Identity
        tables, so the login role."""
        return await self.pool.fetchval(
            """
            WITH RECURSIVE up AS (
                SELECT id, parent_id, 0 AS depth FROM tenants WHERE id = $1
                UNION ALL
                SELECT t.id, t.parent_id, up.depth + 1 FROM tenants t JOIN up ON t.id = up.parent_id
            )
            SELECT a.email FROM up JOIN accounts a ON a.tenant_id = up.id
            ORDER BY up.depth LIMIT 1
            """,
            tenant_id,
        )

async def _subtree(con, root_id: str) -> list[str]:
    """A tenant and everything below it. Recursive rather than a join on
    parent_id: the hierarchy is two levels today but this should not be
    the thing that breaks when a reseller tier appears."""
    rows = await con.fetch(
        """
        WITH RECURSIVE subtree AS (
            SELECT id FROM tenants WHERE id = $1
            UNION ALL
            SELECT t.id FROM tenants t JOIN subtree s ON t.parent_id = s.id
        )
        SELECT id FROM subtree
        """,
        root_id,
    )
    return [r["id"] for r in rows]


# ── support: row shapes ─────────────────────────────────────────────

def _conversation(row) -> SupportConversation:
    return SupportConversation(
        id=row["id"], tenant_id=row["tenant_id"], messages=_loads(row["messages"]) or [],
        turns=row["turns"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _ticket(row) -> SupportTicket:
    return SupportTicket(
        id=row["id"], tenant_id=row["tenant_id"], conversation_id=row["conversation_id"],
        severity=row["severity"], category=row["category"], summary=row["summary"],
        notified=row["notified"], created_at=row["created_at"], brief=_loads(row["brief"]),
        status=row["status"], resolved_at=row["resolved_at"],
    )


_TICKET_COLS = ("id, tenant_id, conversation_id, severity, category, summary, notified, "
                "created_at, brief, status, resolved_at")


# ── reconciliation runs: row shapes ─────────────────────────────────

_RUN_COLS = ("id, tenant_id, kind, name, surface, status, started_at, finished_at, "
             "summary, rows, error")
_RUN_COLS_NO_ROWS = ("id, tenant_id, kind, name, surface, status, started_at, "
                     "finished_at, summary, NULL::jsonb AS rows, error")


def _dumps(value) -> str | None:
    return None if value is None else json.dumps(value, default=str)


def _loads(value):
    """asyncpg hands jsonb back as text unless a codec is registered."""
    if value is None or not isinstance(value, str):
        return value
    return json.loads(value)


def _run(row) -> RunRecord:
    return RunRecord(
        id=row["id"], tenant_id=row["tenant_id"], kind=row["kind"], name=row["name"],
        surface=row["surface"], status=row["status"], started_at=row["started_at"],
        finished_at=row["finished_at"], summary=_loads(row["summary"]),
        rows=_loads(row["rows"]), error=row["error"],
    )
