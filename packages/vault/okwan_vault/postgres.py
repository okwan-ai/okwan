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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import asyncpg

from . import access as access_rules
from . import accounts, apikey
from .access import Seen
from .accounts import AccountRefused
from .crypto import new_key, open_sealed, seal
from .keys import MasterKeyProvider
from .models import (
    IMPORT_KEPT_MONTHS,
    KEY_SCOPES,
    LAYOUTS_KEPT,
    NOTIFY_DEFAULTS,
    RUNS_KEPT,
    SKIPPED_ROWS_KEPT,
    SUPPORT_KEPT_DAYS,
    ApiKey,
    DailyCheck,
    DuplicateImport,
    ImportFile,
    RunRecord,
    SealedCredential,
    SupportConversation,
    SupportTicket,
    Tenant,
    add_months,
    new_unsubscribe_token,
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
    "import_files", "import_rows", "import_layouts", "access_log", "access_alerts",
    "notify_settings",
    "daily_checks",
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

    async def issue_key(self, tenant_id: str, *, scope: str) -> tuple[str, ApiKey]:
        if scope not in KEY_SCOPES:
            raise ValueError(f"a key's scope is one of {KEY_SCOPES}, not {scope!r}")
        full, prefix, hash_hex = apikey.generate()
        key_id = f"key_{uuid.uuid4().hex[:16]}"
        row = await self.pool.fetchrow(
            "INSERT INTO api_keys (id, tenant_id, prefix, hash_hex, scope) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING created_at",
            key_id, tenant_id, prefix, hash_hex, scope,
        )
        return full, ApiKey(
            id=key_id, tenant_id=tenant_id, prefix=prefix,
            hash_hex=hash_hex, created_at=row["created_at"], scope=scope,
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
        found = await self.access_for_key(full_key)
        return found[0] if found else None

    async def access_for_key(self, full_key: str) -> tuple[Tenant, str] | None:
        """The key's tenant and what the key may do. Indexed hash lookup —
        no scan over keys, no timing signal."""
        row = await self.pool.fetchrow(
            "SELECT t.id, t.name, t.parent_id, t.created_at, k.scope "
            "FROM api_keys k JOIN tenants t ON t.id = k.tenant_id "
            "WHERE k.hash_hex = $1 AND k.revoked_at IS NULL",
            apikey.hash_key(full_key),
        )
        return None if row is None else (_tenant(row), row["scope"])

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

    async def save_billing(
        self, tenant_id: str, *, customer_id: str, subscription_id: str, status: str,
        cancel_at_period_end: bool, current_period_end: datetime | None,
    ) -> None:
        """Record or update a paying account's Stripe link, as the login
        role (billing is an identity table). An empty id keeps the one
        already known."""
        await self.pool.execute(
            "INSERT INTO billing (tenant_id, customer_id, subscription_id, status, "
            "cancel_at_period_end, current_period_end) VALUES ($1, NULLIF($2, ''), NULLIF($3, ''), $4, $5, $6) "
            "ON CONFLICT (tenant_id) DO UPDATE SET "
            "customer_id = COALESCE(EXCLUDED.customer_id, billing.customer_id), "
            "subscription_id = COALESCE(EXCLUDED.subscription_id, billing.subscription_id), "
            "status = EXCLUDED.status, cancel_at_period_end = EXCLUDED.cancel_at_period_end, "
            "current_period_end = COALESCE(EXCLUDED.current_period_end, billing.current_period_end), "
            "updated_at = now()",
            tenant_id, customer_id, subscription_id, status, cancel_at_period_end, current_period_end,
        )

    async def billing_for(self, tenant_id: str) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT tenant_id, customer_id, subscription_id, status, cancel_at_period_end, "
            "current_period_end, updated_at, policy_version, checkout_at, checkout_ip, terms_accepted_at "
            "FROM billing WHERE tenant_id = $1", tenant_id)
        return dict(row) if row else None

    async def note_checkout(self, tenant_id: str, *, policy_version: str, at: datetime, ip: str) -> None:
        """What the account was shown as it went to pay: the policy's
        version, when, and from which address. The latest checkout wins."""
        await self.pool.execute(
            "INSERT INTO billing (tenant_id, status, policy_version, checkout_at, checkout_ip) "
            "VALUES ($1, 'none', $2, $3, $4) ON CONFLICT (tenant_id) DO UPDATE SET "
            "policy_version = EXCLUDED.policy_version, checkout_at = EXCLUDED.checkout_at, "
            "checkout_ip = EXCLUDED.checkout_ip",
            tenant_id, policy_version, at, ip)

    async def note_terms_accepted(self, tenant_id: str, at: datetime) -> None:
        """Stripe reported the terms as agreed to at checkout."""
        await self.pool.execute(
            "UPDATE billing SET terms_accepted_at = $2 WHERE tenant_id = $1", tenant_id, at)

    async def tenant_for_billing_customer(self, customer_id: str) -> str | None:
        return await self.pool.fetchval("SELECT tenant_id FROM billing WHERE customer_id = $1", customer_id)

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
        """The session's tenant while it is live: before its absolute expiry
        and used within SESSION_IDLE. Using it moves the idle clock, written
        at most once per SESSION_TOUCH, in the same statement."""
        row = await self.pool.fetchrow(
            "WITH live AS ("
            "  SELECT tenant_id FROM sessions WHERE token_hash = $1"
            "  AND expires_at > now() AND last_seen_at > now() - $2::interval"
            "), touch AS ("
            "  UPDATE sessions SET last_seen_at = now() WHERE token_hash = $1"
            "  AND last_seen_at < now() - $3::interval AND EXISTS (SELECT 1 FROM live)"
            ") "
            "SELECT t.id, t.name, t.parent_id, t.created_at "
            "FROM live JOIN tenants t ON t.id = live.tenant_id",
            token_hash, accounts.SESSION_IDLE, accounts.SESSION_TOUCH,
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

    # ── imported files ──────────────────────────────────────────────

    async def add_import(
        self, tenant_id: str, *, kind: str, filename: str, format: str, layout: str,
        mapping: dict, sha256: str, rows: list[tuple[int, date, dict]], skipped: list[int],
        card_columns: list[str], first_date: date | None, last_date: date | None,
        now: datetime | None = None,
    ) -> ImportFile:
        """Store a mapped file and its rows in one transaction, after pruning
        the tenant's expired files. Rows go in by INSERT … SELECT FROM
        unnest in chunks: COPY does not run under row-level security."""
        now = now or datetime.now(UTC)
        file_id = f"imp_{uuid.uuid4().hex[:16]}"
        expires = add_months(now, IMPORT_KEPT_MONTHS)
        try:
            async with self._scoped(tenant_id) as con:
                await con.execute(
                    "DELETE FROM import_files WHERE tenant_id = $1 AND expires_at <= $2",
                    tenant_id, now)
                same = await con.fetchval(
                    "SELECT id FROM import_files WHERE tenant_id = $1 AND kind = $2 AND sha256 = $3",
                    tenant_id, kind, sha256)
                if same is not None:
                    raise DuplicateImport(same)
                row = await con.fetchrow(
                    "INSERT INTO import_files (id, tenant_id, kind, filename, format, layout, "
                    "mapping, sha256, row_count, skipped, skipped_rows, card_columns, first_date, "
                    "last_date, created_at, expires_at) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, "
                    "$8, $9, $10, $11::jsonb, $12::jsonb, $13, $14, $15, $16) "
                    f"RETURNING {_IMPORT_COLS}",
                    file_id, tenant_id, kind, filename, format, layout, _dumps(mapping), sha256,
                    len(rows), len(skipped), _dumps(skipped[:SKIPPED_ROWS_KEPT]),
                    _dumps(card_columns), first_date, last_date, now, expires,
                )
                for start in range(0, len(rows), _IMPORT_CHUNK):
                    chunk = rows[start:start + _IMPORT_CHUNK]
                    await con.execute(
                        "INSERT INTO import_rows (file_id, tenant_id, kind, n, row_date, data) "
                        "SELECT $1, $2, $3, u.n, u.d, u.j::jsonb "
                        "FROM unnest($4::int[], $5::date[], $6::text[]) AS u(n, d, j)",
                        file_id, tenant_id, kind, [n for n, _, _ in chunk],
                        [d for _, d, _ in chunk], [json.dumps(data) for _, _, data in chunk],
                    )
        except asyncpg.UniqueViolationError:
            raise DuplicateImport(None) from None
        except asyncpg.ForeignKeyViolationError:
            raise KeyError(f"unknown tenant {tenant_id!r}") from None
        return _import(row)

    async def list_imports(
        self, tenant_id: str, kind: str | None = None, now: datetime | None = None
    ) -> list[ImportFile]:
        async with self._scoped(tenant_id) as con:
            rows = await con.fetch(
                f"SELECT {_IMPORT_COLS} FROM import_files WHERE tenant_id = $1 "
                "AND ($2::text IS NULL OR kind = $2) AND expires_at > $3 "
                "ORDER BY created_at DESC, id DESC",
                tenant_id, kind, now or datetime.now(UTC))
        return [_import(r) for r in rows]

    async def get_import(
        self, tenant_id: str, file_id: str, now: datetime | None = None
    ) -> ImportFile | None:
        async with self._scoped(tenant_id) as con:
            row = await con.fetchrow(
                f"SELECT {_IMPORT_COLS} FROM import_files WHERE tenant_id = $1 AND id = $2 "
                "AND expires_at > $3", tenant_id, file_id, now or datetime.now(UTC))
        return None if row is None else _import(row)

    async def delete_import(self, tenant_id: str, file_id: str) -> bool:
        """The file and, by cascade, every row it produced."""
        async with self._scoped(tenant_id) as con:
            gone = await con.fetchval(
                "DELETE FROM import_files WHERE tenant_id = $1 AND id = $2 RETURNING id",
                tenant_id, file_id)
        return gone is not None

    async def import_rows(
        self, tenant_id: str, kind: str, *, since: date | None = None,
        until: date | None = None, after: tuple[date, str, int] | None = None,
        limit: int = 500, now: datetime | None = None, direction: str | None = None,
    ) -> list[dict]:
        """Mapped rows of one kind, oldest first, from files not expired.
        `after` is the (row date, file id, row) of the last row already read;
        `direction` "in" or "out" keeps rows whose amount is above or below 0."""
        a_date, a_file, a_n = after if after else (None, None, None)
        async with self._scoped(tenant_id) as con:
            rows = await con.fetch(
                "SELECT r.file_id, r.n, r.data, f.layout FROM import_rows r "
                "JOIN import_files f ON f.id = r.file_id AND f.tenant_id = r.tenant_id "
                "WHERE r.tenant_id = $1 AND r.kind = $2 AND f.expires_at > $3 "
                "AND ($4::date IS NULL OR r.row_date >= $4) "
                "AND ($5::date IS NULL OR r.row_date <= $5) "
                "AND ($6::date IS NULL OR (r.row_date, r.file_id, r.n) > ($6, $7::text, $8::int)) "
                "AND ($10::text IS NULL OR ($10 = 'in' AND (r.data->>'amount_minor')::bigint > 0) "
                "     OR ($10 = 'out' AND (r.data->>'amount_minor')::bigint < 0)) "
                "ORDER BY r.row_date, r.file_id, r.n LIMIT $9",
                tenant_id, kind, now or datetime.now(UTC), since, until, a_date, a_file, a_n, limit,
                direction)
        return [{**_loads(r["data"]), "file_id": r["file_id"], "row": r["n"], "layout": r["layout"]}
                for r in rows]

    async def import_summary(
        self, tenant_id: str, now: datetime | None = None
    ) -> dict[str, dict]:
        """Per kind, what the tenant's live files cover: how many files and
        rows, and the first and last day of them."""
        async with self._scoped(tenant_id) as con:
            rows = await con.fetch(
                "SELECT kind, count(*) AS files, sum(row_count) AS rows, min(first_date) AS first, "
                "max(last_date) AS last FROM import_files WHERE tenant_id = $1 AND expires_at > $2 "
                "GROUP BY kind", tenant_id, now or datetime.now(UTC))
        return {r["kind"]: {"files": r["files"], "rows": int(r["rows"] or 0),
                            "first_date": r["first"], "last_date": r["last"]} for r in rows}

    async def save_layout(self, tenant_id: str, name: str, mapping: dict) -> dict:
        """Create or replace a saved mapping by name."""
        try:
            async with self._scoped(tenant_id) as con:
                count = await con.fetchval(
                    "SELECT count(*) FROM import_layouts WHERE tenant_id = $1 AND name <> $2",
                    tenant_id, name)
                if count >= LAYOUTS_KEPT:
                    raise ValueError(f"at most {LAYOUTS_KEPT} saved mappings; delete one first")
                row = await con.fetchrow(
                    "INSERT INTO import_layouts (tenant_id, name, kind, mapping) "
                    "VALUES ($1, $2, $3, $4::jsonb) ON CONFLICT (tenant_id, name) DO UPDATE "
                    "SET kind = EXCLUDED.kind, mapping = EXCLUDED.mapping, updated_at = now() "
                    f"RETURNING {_LAYOUT_COLS}",
                    tenant_id, name, mapping["kind"], _dumps(mapping))
        except asyncpg.ForeignKeyViolationError:
            raise KeyError(f"unknown tenant {tenant_id!r}") from None
        return _layout(row)

    async def list_layouts(self, tenant_id: str) -> list[dict]:
        async with self._scoped(tenant_id) as con:
            rows = await con.fetch(
                f"SELECT {_LAYOUT_COLS} FROM import_layouts WHERE tenant_id = $1 ORDER BY name",
                tenant_id)
        return [_layout(r) for r in rows]

    async def get_layout(self, tenant_id: str, name: str) -> dict | None:
        async with self._scoped(tenant_id) as con:
            row = await con.fetchrow(
                f"SELECT {_LAYOUT_COLS} FROM import_layouts WHERE tenant_id = $1 AND name = $2",
                tenant_id, name)
        return None if row is None else _layout(row)

    async def delete_layout(self, tenant_id: str, name: str) -> bool:
        async with self._scoped(tenant_id) as con:
            gone = await con.fetchval(
                "DELETE FROM import_layouts WHERE tenant_id = $1 AND name = $2 RETURNING name",
                tenant_id, name)
        return gone is not None

    async def sweep_imports(self, now: datetime | None = None) -> int:
        """Delete every tenant's expired files, and by cascade their rows.

        Across tenants on purpose, so as the login role, like deleting a
        tenant: it reads nothing back and touches only what has expired.
        """
        status = await self.pool.execute(
            "DELETE FROM import_files WHERE expires_at <= $1", now or datetime.now(UTC))
        return int(status.split()[-1])

    # ── access log ──────────────────────────────────────────────────

    async def record_access(
        self, tenant_id: str, *, credential: str, ip: str, network: str, surface: str,
        target: str = "", at: datetime | None = None,
    ) -> Seen:
        """Write one access row, and say what was known before it.

        Both lookups are one index probe each: the oldest row of this
        credential, and any row of it from this network within the window.
        """
        at = at or datetime.now(UTC)
        async with self._scoped(tenant_id) as con:
            before = await con.fetchrow(
                "SELECT (SELECT min(at) FROM access_log WHERE tenant_id = $1 AND credential = $2) "
                "AS first_seen, EXISTS (SELECT 1 FROM access_log WHERE tenant_id = $1 "
                "AND credential = $2 AND network = $3 AND at > $4) AS known",
                tenant_id, credential, network, at - access_rules.NETWORK_MEMORY)
            await con.execute(
                "INSERT INTO access_log (tenant_id, at, credential, ip, network, surface, target) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                tenant_id, at, credential, ip, network, surface, target)
        return Seen(known_network=before["known"], first_seen=before["first_seen"])

    async def access_entries(self, root_id: str, since: datetime, limit: int = 200) -> list[dict]:
        """A tenant's and its merchants' newest access rows, scoped to
        exactly that subtree, as `usage_buckets` reads the meter."""
        async with self.pool.acquire() as con, con.transaction():
            ids = await _subtree(con, root_id)
            await _enter_scope(con, ids)
            rows = await con.fetch(
                "SELECT tenant_id, at, credential, ip, network, surface, target FROM access_log "
                "WHERE tenant_id = ANY($1::text[]) AND at >= $2 ORDER BY at DESC LIMIT $3",
                ids, since, limit)
        return [dict(r) for r in rows]

    async def add_access_alert(
        self, tenant_id: str, *, kind: str, credential: str, detail: dict, at: datetime | None = None,
    ) -> dict:
        alert = {"id": f"alr_{uuid.uuid4().hex[:16]}", "tenant_id": tenant_id,
                 "at": at or datetime.now(UTC), "kind": kind, "credential": credential,
                 "detail": dict(detail), "mailed": False}
        async with self._scoped(tenant_id) as con:
            await con.execute(
                "INSERT INTO access_alerts (id, tenant_id, at, kind, credential, detail) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb)",
                alert["id"], tenant_id, alert["at"], kind, credential, json.dumps(detail))
        return alert

    async def set_alert_mailed(self, tenant_id: str, alert_id: str) -> None:
        async with self._scoped(tenant_id) as con:
            await con.execute(
                "UPDATE access_alerts SET mailed = true WHERE tenant_id = $1 AND id = $2",
                tenant_id, alert_id)

    async def access_alerts(self, root_id: str, since: datetime) -> list[dict]:
        async with self.pool.acquire() as con, con.transaction():
            ids = await _subtree(con, root_id)
            await _enter_scope(con, ids)
            rows = await con.fetch(
                "SELECT id, tenant_id, at, kind, credential, detail, mailed FROM access_alerts "
                "WHERE tenant_id = ANY($1::text[]) AND at >= $2 ORDER BY at DESC",
                ids, since)
        return [{**dict(r), "detail": _loads(r["detail"])} for r in rows]

    async def volume_spikes(
        self, now: datetime | None = None, *, floor: int = access_rules.VOLUME_FLOOR,
        factor: int = access_rules.VOLUME_FACTOR,
    ) -> list[dict]:
        """Keys that read unusually much in the last hour, across tenants.

        The tenant list is identity, read as the login role; the access
        rows are read as TENANT_ROLE scoped to a batch of those tenants at
        a time, so row-level security holds for a check that spans them.
        A key with a volume alert in the quiet window is left out.
        """
        now = now or datetime.now(UTC)
        hour = timedelta(hours=1)
        ids = [r["id"] for r in await self.pool.fetch("SELECT id FROM tenants ORDER BY id")]
        out: list[dict] = []
        for i in range(0, len(ids), 500):
            batch = ids[i:i + 500]
            async with self._scoped(*batch) as con:
                rows = await con.fetch(
                    """
                    WITH recent AS (
                        SELECT tenant_id, credential, count(*) AS n FROM access_log
                        WHERE tenant_id = ANY($1::text[]) AND at > $2 AND at <= $3
                          AND starts_with(credential, $4)
                        GROUP BY tenant_id, credential
                    ), hours AS (
                        SELECT tenant_id, credential, count(*) AS n FROM access_log
                        WHERE tenant_id = ANY($1::text[]) AND at > $5 AND at <= $2
                          AND starts_with(credential, $4)
                        GROUP BY tenant_id, credential, date_trunc('hour', at)
                    ), prior AS (
                        SELECT tenant_id, credential, max(n) AS busiest FROM hours
                        GROUP BY tenant_id, credential
                    )
                    SELECT r.tenant_id, r.credential, r.n AS requests,
                           COALESCE(p.busiest, 0) AS busiest
                    FROM recent r LEFT JOIN prior p USING (tenant_id, credential)
                    WHERE r.n >= $6 AND r.n > $7 * COALESCE(p.busiest, 0)
                      AND NOT EXISTS (
                          SELECT 1 FROM access_alerts a
                          WHERE a.tenant_id = r.tenant_id AND a.credential = r.credential
                            AND a.kind = 'volume' AND a.at > $8)
                    ORDER BY r.tenant_id, r.credential
                    """,
                    batch, now - hour, now, access_rules.KEY_MARK,
                    now - hour - access_rules.VOLUME_WEEK, floor, factor,
                    now - access_rules.VOLUME_QUIET)
            out.extend(dict(r) for r in rows)
        return out

    # ── the daily check's settings ────────────────────────────────────

    async def get_notify(self, tenant_id: str) -> dict:
        """An account's choices about the daily check; both on when it has
        made none. `unsubscribe_token` is None until a mail needs one."""
        async with self._scoped(tenant_id) as con:
            row = await con.fetchrow(
                "SELECT daily_checks, finding_emails, unsubscribe_token, mailed_at FROM notify_settings "
                "WHERE tenant_id = $1", tenant_id)
        return dict(row) if row else dict(NOTIFY_DEFAULTS)

    async def set_notify(
        self, tenant_id: str, *, daily_checks: bool | None = None, finding_emails: bool | None = None,
    ) -> dict:
        """Change one choice or both; what is not given keeps its value."""
        try:
            async with self._scoped(tenant_id) as con:
                row = await con.fetchrow(
                    "INSERT INTO notify_settings (tenant_id, daily_checks, finding_emails) "
                    "VALUES ($1, COALESCE($2, true), COALESCE($3, true)) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET "
                    "daily_checks = COALESCE($2, notify_settings.daily_checks), "
                    "finding_emails = COALESCE($3, notify_settings.finding_emails), updated_at = now() "
                    "RETURNING daily_checks, finding_emails, unsubscribe_token, mailed_at",
                    tenant_id, daily_checks, finding_emails)
        except asyncpg.ForeignKeyViolationError:
            raise KeyError(f"unknown tenant {tenant_id!r}") from None
        return dict(row)

    async def notify_token(self, tenant_id: str) -> str:
        """The account's unsubscribe token, made on first use and kept, so
        the link in every mail it was ever sent goes on working."""
        try:
            async with self._scoped(tenant_id) as con:
                return await con.fetchval(
                    "INSERT INTO notify_settings (tenant_id, unsubscribe_token) VALUES ($1, $2) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET unsubscribe_token = "
                    "COALESCE(notify_settings.unsubscribe_token, EXCLUDED.unsubscribe_token) "
                    "RETURNING unsubscribe_token",
                    tenant_id, new_unsubscribe_token(tenant_id))
        except asyncpg.ForeignKeyViolationError:
            raise KeyError(f"unknown tenant {tenant_id!r}") from None

    async def notify_mailed(self, tenant_id: str, at: datetime | None) -> None:
        """When the account was last mailed findings: at most one a day.
        None puts back "never", for a mail that did not go."""
        try:
            async with self._scoped(tenant_id) as con:
                await con.execute(
                    "INSERT INTO notify_settings (tenant_id, mailed_at) VALUES ($1, $2) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET mailed_at = EXCLUDED.mailed_at",
                    tenant_id, at)
        except asyncpg.ForeignKeyViolationError:
            raise KeyError(f"unknown tenant {tenant_id!r}") from None

    # ── the daily check's own record ──────────────────────────────────

    async def daily_checks(self, tenant_ids: list[str]) -> dict[str, DailyCheck]:
        """The scheduler's record for each of these merchants that has one."""
        if not tenant_ids:
            return {}
        async with self._scoped(*tenant_ids) as con:
            rows = await con.fetch(
                "SELECT tenant_id, checked_at, told, news FROM daily_checks "
                "WHERE tenant_id = ANY($1::text[])", list(tenant_ids))
        return {r["tenant_id"]: DailyCheck(
            tenant_id=r["tenant_id"], checked_at=r["checked_at"],
            told=tuple(_loads(r["told"]) or ()), news=tuple(_loads(r["news"]) or ())) for r in rows}

    async def claim_daily_check(self, tenant_id: str, at: datetime) -> None:
        """Say the merchant's check for today is taken, before it runs: a
        check that then fails, or cannot be stored, is still not due again
        until tomorrow."""
        try:
            async with self._scoped(tenant_id) as con:
                await con.execute(
                    "INSERT INTO daily_checks (tenant_id, checked_at) VALUES ($1, $2) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET checked_at = EXCLUDED.checked_at",
                    tenant_id, at)
        except asyncpg.ForeignKeyViolationError:
            raise KeyError(f"unknown tenant {tenant_id!r}") from None

    async def settle_daily_check(
        self, tenant_id: str, *, told: list[str], news: list[dict],
    ) -> None:
        """What the account has now been told of, and what is still to mail."""
        async with self._scoped(tenant_id) as con:
            await con.execute(
                "UPDATE daily_checks SET told = $2::jsonb, news = $3::jsonb WHERE tenant_id = $1",
                tenant_id, _dumps(list(told)), _dumps(list(news)))

    async def sweep_access(self, now: datetime | None = None) -> int:
        """Delete access rows past RETENTION and alerts past ALERT_RETENTION.

        Across tenants, as the login role, like `sweep_imports`: it reads
        nothing back and touches only what has aged out.
        """
        now = now or datetime.now(UTC)
        status = await self.pool.execute(
            "DELETE FROM access_log WHERE at < $1", now - access_rules.RETENTION)
        await self.pool.execute(
            "DELETE FROM access_alerts WHERE at < $1", now - access_rules.ALERT_RETENTION)
        return int(status.split()[-1])

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
    return None if value is None else json.dumps(_storable(value), default=str)


def _storable(value):
    """The same value with the two things jsonb refuses in a string taken
    out: U+0000 and a surrogate with no pair. Both can arrive in a rail's own
    text (an order reference), and one of them in one row would otherwise
    stop the whole run from being stored."""
    if isinstance(value, str):
        if "\x00" in value:
            value = value.replace("\x00", "\ufffd")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            value = value.encode("utf-8", "replace").decode("utf-8")
        return value
    if isinstance(value, dict):
        return {_storable(k) if isinstance(k, str) else k: _storable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_storable(v) for v in value]
    return value


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


# ── imported files: row shapes ──────────────────────────────────────

_IMPORT_COLS = ("id, tenant_id, kind, filename, format, layout, mapping, sha256, row_count, "
                "skipped, skipped_rows, card_columns, first_date, last_date, created_at, expires_at")
#: Rows per INSERT: six arrays of this length stay well inside one message.
_IMPORT_CHUNK = 5000


def _import(row) -> ImportFile:
    return ImportFile(
        id=row["id"], tenant_id=row["tenant_id"], kind=row["kind"], filename=row["filename"],
        format=row["format"], layout=row["layout"], mapping=_loads(row["mapping"]) or {},
        sha256=row["sha256"], row_count=row["row_count"], skipped=row["skipped"],
        skipped_rows=_loads(row["skipped_rows"]) or [], card_columns=_loads(row["card_columns"]) or [],
        first_date=row["first_date"], last_date=row["last_date"], created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


_LAYOUT_COLS = "name, kind, mapping, created_at, updated_at"


def _layout(row) -> dict:
    return {"name": row["name"], "kind": row["kind"], "mapping": _loads(row["mapping"]) or {},
            "created_at": row["created_at"], "updated_at": row["updated_at"]}

