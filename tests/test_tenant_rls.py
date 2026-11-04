"""The database's own wall between tenants: row-level security.

tests/test_tenant_isolation.py proves no surface returns another tenant's
records. This file proves the second, independent wall under it: inside
Postgres, the role every tenant-data query runs as cannot see or write a
row outside the tenants its transaction names, whatever the SQL says.
scripts/plant_leaks.py shows each wall holding while the other is broken.

The static checks run everywhere. The database checks skip without
OKWAN_VAULT_DATABASE_URL, like tests/test_vault_postgres.py.
"""
from __future__ import annotations

import ast
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from okwan_vault import EnvMasterKey, PostgresStore, new_key
from okwan_vault import postgres as pg
from okwan_vault.postgres import TENANT_ROLE, TENANT_TABLES, IsolationUnavailable

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not DSN, reason="no vault database configured")

#: Tables with a tenant_id that are identity, not a tenant's data: they map
#: a key hash, a session hash or an address to a tenant, which has to work
#: before any tenant is known. The login role reads them; okwan_tenant cannot.
IDENTITY_TABLES = {"api_keys", "sessions", "accounts"}
NO_TENANT_COLUMN = {"tenants", "signups", "operator_factors", "operator_actions"}

#: Store methods that read tenant data across every tenant, on purpose, as
#: the login role. Each is reached only by the operator CLI, never by a
#: route: `support list` shows the operator every escalation.
OPERATOR_READS = {"list_tickets", "get_ticket"}


# ── static: every query over tenant data enters a scope ────────────────

_DATA_SQL = re.compile(
    r"\b(?:FROM|INTO|UPDATE|JOIN)\s+(" + "|".join(TENANT_TABLES) + r")\b", re.IGNORECASE
)


def _methods():
    tree = ast.parse(Path(pg.__file__).read_text())
    store = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PostgresStore")
    for fn in store.body:
        if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
            yield fn


def _sql_in(fn) -> list[str]:
    body = fn.body[1:] if fn.body and isinstance(fn.body[0], ast.Expr) else fn.body  # not the docstring
    out = []
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append(node.value)
    return out


def _enters_scope(fn) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "_scoped":
                return True
            if isinstance(f, ast.Name) and f.id == "_enter_scope":
                return True
    return False


def test_every_store_method_over_tenant_data_runs_scoped():
    touching = {fn.name: fn for fn in _methods() if any(_DATA_SQL.search(s) for s in _sql_in(fn))}
    assert len(touching) >= 10, f"the check found too few methods to mean anything: {sorted(touching)}"
    unscoped = sorted(name for name, fn in touching.items()
                      if not _enters_scope(fn) and name not in OPERATOR_READS)
    assert not unscoped, (
        f"PostgresStore methods that read or write {TENANT_TABLES} as the login role, "
        f"outside row-level security: {unscoped}. Use `async with self._scoped(tenant_id)`."
    )


def test_the_schema_lists_the_same_tenant_tables_as_the_store():
    schema = Path(pg.__file__).with_name("schema.sql").read_text()
    tenant, identity = (tuple(re.findall(r"'(\w+)'", group))
                        for group in re.findall(r"FOREACH t IN ARRAY ARRAY\[([^\]]+)\]", schema))
    assert tenant == TENANT_TABLES
    assert set(identity) == IDENTITY_TABLES | NO_TENANT_COLUMN


@needs_db
async def test_a_start_against_a_current_database_takes_no_exclusive_lock(store):
    """The migration runs on every start, while another instance may be
    serving: once the schema is current it must not block anyone."""
    async with store.pool.acquire() as con, con.transaction():
        await con.execute(pg.SCHEMA)
        held = await con.fetch(
            "SELECT c.relname, l.mode FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
            "WHERE l.pid = pg_backend_pid() AND c.relkind = 'r' "
            "AND l.mode IN ('AccessExclusiveLock', 'ExclusiveLock', 'ShareRowExclusiveLock')")
    assert not held, f"a start locks tables: {[(r['relname'], r['mode']) for r in held]}"


# ── the database ───────────────────────────────────────────────────────

@pytest.fixture
async def store():
    s = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    created: list[str] = []
    create = s.create_tenant

    async def tracked(name, parent_id=None):
        tenant = await create(name, parent_id=parent_id)
        created.append(tenant.id)
        return tenant

    s.create_tenant = tracked
    try:
        yield s
    finally:
        for tenant_id in reversed(created):
            await s.pool.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await s.close()


@pytest.fixture
async def two(store):
    """Two tenants with a row of their own in every tenant table."""
    now = datetime.now(UTC)
    out = []
    for name in ("Acme", "Rival"):
        t = await store.create_tenant(name)
        await store.put_credential(t.id, "stripe", "secret_key", f"sk_{name}")
        await store.record_request(t.id, "rest:test")
        await store.set_plan(t.id, "pro")
        await store.add_run(t.id, kind="across", name="rails", surface="rest", status="ok",
                            started_at=now, finished_at=now, summary={}, rows=[], error=None)
        cid = await store.save_conversation(t.id, None, [{"role": "user", "content": "hi"}], 1)
        ticket = await store.add_ticket(t.id, conversation_id=cid, severity="sev3", category="other",
                                        summary=f"{name} asked for help", notified=False)
        await store.add_reply(t.id, ticket.id, "owner@okwan.test", "We're on it.", True)
        out.append(t)
    return out


class _Undo(Exception):
    """Raised to roll a probing transaction back."""


async def _as_tenant_role(con, scope: str | None):
    await con.execute(f"SET LOCAL ROLE {TENANT_ROLE}")
    if scope is not None:
        await con.execute("SELECT set_config('okwan.tenant_ids', $1, true)", scope)


@needs_db
async def test_the_role_cannot_log_in_owns_nothing_and_does_not_bypass(store):
    async with store.pool.acquire() as con:
        role = await con.fetchrow(
            "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
            "FROM pg_roles WHERE rolname = $1", TENANT_ROLE)
        owned = await con.fetchval(
            "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = $1",
            TENANT_ROLE)
        tables = [r["table_name"] for r in await con.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'")]
        readable = {t for t in tables if await con.fetchval(
            "SELECT has_table_privilege($1, $2, 'SELECT')", TENANT_ROLE, t)}
    assert dict(role) == {"rolcanlogin": False, "rolsuper": False, "rolbypassrls": False,
                          "rolcreaterole": False, "rolcreatedb": False}
    assert owned == 0
    assert readable == set(TENANT_TABLES), f"okwan_tenant can read {sorted(readable)}"


@needs_db
async def test_every_table_with_a_tenant_is_classified_and_protected(store):
    async with store.pool.acquire() as con:
        with_tenant = {r["table_name"] for r in await con.fetch(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND column_name = 'tenant_id'")}
        protected = {r["relname"] for r in await con.fetch(
            "SELECT c.relname FROM pg_class c JOIN pg_policies p ON p.tablename = c.relname "
            "AND p.schemaname = current_schema() AND p.policyname = 'tenant_scope' "
            "WHERE c.relrowsecurity AND c.relnamespace = current_schema()::regnamespace")}
    assert with_tenant == set(TENANT_TABLES) | IDENTITY_TABLES, (
        "a table with a tenant_id is neither tenant data (add it to TENANT_TABLES and the "
        "schema's row-level security) nor declared identity here: "
        f"{sorted(with_tenant - set(TENANT_TABLES) - IDENTITY_TABLES)}"
    )
    assert protected == set(TENANT_TABLES)


@needs_db
@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_with_no_scope_the_role_sees_nothing_and_writes_nothing(store, two, table):
    a, _ = two
    async with store.pool.acquire() as con:
        async with con.transaction():
            await _as_tenant_role(con, None)
            assert await con.fetchval(f"SELECT count(*) FROM {table}") == 0
        with pytest.raises(Exception, match="row-level security"):
            async with con.transaction():
                await _as_tenant_role(con, None)
                await con.execute(f"UPDATE {table} SET tenant_id = tenant_id")  # touches nothing
                await con.execute(*_insert(table, a.id))


@needs_db
@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_scoped_to_one_tenant_a_query_without_where_sees_only_that_tenant(store, two, table):
    a, b = two
    async with store.pool.acquire() as con:
        try:
            async with con.transaction():
                await _as_tenant_role(con, a.id)
                seen = {r["tenant_id"] for r in await con.fetch(f"SELECT tenant_id FROM {table}")}
                changed = await con.execute(f"UPDATE {table} SET tenant_id = tenant_id")
                deleted = await con.execute(f"DELETE FROM {table}")
                await con.execute("SET LOCAL ROLE NONE")
                survivors = await con.fetchval(f"SELECT count(*) FROM {table} WHERE tenant_id = $1", b.id)
                raise _Undo  # leave the seed rows as they were
        except _Undo:
            pass
    assert seen == {a.id}
    own = changed.split()[-1]
    assert own == deleted.split()[-1] != "0", "the scope should reach the tenant's own rows"
    assert survivors >= 1, "a DELETE without WHERE removed another tenant's rows"


@needs_db
@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_scoped_to_one_tenant_the_role_cannot_write_another(store, two, table):
    a, b = two
    async with store.pool.acquire() as con:
        with pytest.raises(Exception, match="row-level security"):
            async with con.transaction():
                await _as_tenant_role(con, a.id)
                await con.execute(*_insert(table, b.id))
        with pytest.raises(Exception, match="row-level security"):
            async with con.transaction():
                await _as_tenant_role(con, a.id)
                await con.execute(f"UPDATE {table} SET tenant_id = $1", b.id)  # move A's rows to B


def _insert(table: str, tenant_id: str) -> tuple:
    now = datetime.now(UTC)
    return {
        "credentials": (("INSERT INTO credentials (tenant_id, connector, field_name, ciphertext, "
                        "wrapped_key, key_id) VALUES ($1, 'paypal', 'client_id', '\\x00', '\\x00', 'k')"),
                        tenant_id),
        "usage": ("INSERT INTO usage (tenant_id, hour, surface, requests) VALUES ($1, $2, 'x', 1)",
                  tenant_id, now),
        "plans": (("INSERT INTO plans (tenant_id, name, monthly_requests) VALUES ($1, 'x', 1) "
                  "ON CONFLICT (tenant_id) DO UPDATE SET name = 'x'"), tenant_id),
        "reconciliation_runs": (("INSERT INTO reconciliation_runs (id, tenant_id, kind, name, surface, "
                                "status, started_at, finished_at) VALUES ('run_rls_probe', $1, "
                                "'across', 'rails', 'rest', 'ok', $2, $2)"), tenant_id, now),
        "support_conversations": (("INSERT INTO support_conversations (id, tenant_id, messages) "
                                   "VALUES ('sup_rls_probe', $1, '[]')"), tenant_id),
        "support_tickets": (("INSERT INTO support_tickets (id, tenant_id, severity, category, "
                             "summary) VALUES ('tkt_rls_probe', $1, 'sev3', 'other', 'probe')"),
                            tenant_id),
        "support_replies": (("INSERT INTO support_replies (id, tenant_id, ticket_id, operator, body, "
                             "sent) VALUES ('rpl_rls_probe', $1, 't', 'o', 'b', true)"), tenant_id),
    }[table]


@needs_db
@pytest.mark.parametrize("table", ["tenants", "api_keys", "sessions", "accounts", "signups",
                                   "operator_factors", "operator_actions"])
async def test_the_role_cannot_read_the_identity_tables(store, table):
    async with store.pool.acquire() as con:
        with pytest.raises(Exception, match="permission denied"):
            async with con.transaction():
                await _as_tenant_role(con, "ten_anything")
                await con.fetch(f"SELECT * FROM {table} LIMIT 1")


@needs_db
@pytest.mark.parametrize("ending", ["commit", "error"])
async def test_a_scope_never_outlives_its_transaction(store, two, ending):
    """Pooled connections are shared by every tenant: what one request set
    must be gone before the connection serves the next."""
    a, _ = two
    pool = store.pool
    async with pool.acquire() as con:
        login = await con.fetchval("SELECT current_user")
        try:
            async with con.transaction():
                await pg._enter_scope(con, [a.id])
                assert await con.fetchval("SELECT current_user") == TENANT_ROLE
                if ending == "error":
                    raise RuntimeError("the request failed half way")
        except RuntimeError:
            pass
        assert await con.fetchval("SELECT current_user") == login
        assert await con.fetchval("SELECT coalesce(current_setting('okwan.tenant_ids', true), '')") == ""


@needs_db
async def test_the_store_reads_through_the_wall(store, two):
    """The store's own scoped connection: no WHERE in sight, one tenant's rows."""
    a, b = two
    async with store._scoped(a.id) as con:
        assert await con.fetchval("SELECT current_user") == TENANT_ROLE
        for table in TENANT_TABLES:
            assert {r["tenant_id"] for r in await con.fetch(f"SELECT tenant_id FROM {table}")} == {a.id}
    assert (await store.credentials_for(b.id, "stripe", ("secret_key",)))["secret_key"] == "sk_Rival"


@needs_db
async def test_the_api_refuses_to_start_without_the_wall(store):
    """Fail closed: a database where a tenant table lost its row-level
    security is one the store will not serve from."""
    other = PostgresStore(DSN, EnvMasterKey(new_key()))
    async with store.pool.acquire() as con:
        await con.execute("ALTER TABLE credentials DISABLE ROW LEVEL SECURITY")
        try:
            with pytest.raises(IsolationUnavailable, match="credentials"):
                await other.connect(migrate=False)
        finally:
            await con.execute("ALTER TABLE credentials ENABLE ROW LEVEL SECURITY")
            await other.close()
    await other.connect(migrate=False)  # and starts again once it is back
    await other.close()


@needs_db
async def test_comma_in_a_tenant_id_cannot_widen_a_scope(store):
    async with store.pool.acquire() as con, con.transaction():
        with pytest.raises(ValueError, match="comma"):
            await pg._enter_scope(con, ["ten_a,ten_b"])
