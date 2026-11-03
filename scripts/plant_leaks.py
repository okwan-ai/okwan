"""Plant cross-tenant leaks one at a time and show what stops each.

    OKWAN_VAULT_DATABASE_URL=postgresql://... python scripts/plant_leaks.py

For an auditor, or anyone changing the isolation code: a test suite that
always passes proves nothing, so this breaks the code on purpose and
reports what happens. Three kinds of break:

* **caught**: a bug above the database (a guard that stops checking, a
  merchant handed its parent's vault, a cache that forgets whose data it
  holds). The isolation suite must fail.
* **held**: one database wall broken, the other intact: a Postgres query
  that drops its WHERE tenant_id (row-level security must still hold), or
  a store that never enters the restricted role (the WHERE must still
  hold). The isolation suite against the deployed store must still pass,
  and the broken wall's own tests must fail, so a weakened wall is
  noticed rather than silently covered for. The WHERE wall's own tests
  are the isolation suite on `postgres_where_only`; the database wall's
  are tests/test_tenant_rls.py.
* **both walls**: both broken at once. The isolation suite must fail.

Every file is restored after each run, also on Ctrl-C. The script refuses
to start while any file it would touch has uncommitted changes, so a
restore can never overwrite someone's work. Exit status 0 only when every
break behaves as expected.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISOLATION = "tests/test_tenant_isolation.py"
#: Each wall tested alone: (test file, -k filter).
WHERE_WALL = (ISOLATION, "postgres_where_only")
DB_WALL = ("tests/test_tenant_rls.py", "")
GUARD_WALL = (ISOLATION, "statement_guard")
#: Everything but the WHERE-only store: what must still pass while one wall is down.
BOTH_WALLS = "not postgres_where_only"

API_ADMIN = "apps/api/okwan_api/admin.py"
API_AUTH = "apps/api/okwan_api/auth.py"
MCP = "packages/query/okwan_query/mcp_http.py"
STORE = "packages/vault/okwan_vault/store.py"
PG = "packages/vault/okwan_vault/postgres.py"
PAYPAL = "packages/connectors/paypal/okwan_paypal/auth.py"
SESSION = "packages/query/okwan_query/session.py"
GUARD = "packages/query/okwan_query/guard.py"

#: (file, exact text, replacement)
Edit = tuple[str, str, str]

NO_SCOPE: Edit = (PG, "    ids = list(tenant_ids)\n    if any(", "    return\n    ids = list(tenant_ids)\n    if any(")
CREDS_NO_WHERE: Edit = (PG, '"WHERE tenant_id = $1 AND connector = ANY($2::text[])"',
                        '"WHERE $1::text IS NOT NULL AND connector = ANY($2::text[])"')
RUN_NO_WHERE: Edit = (PG, "WHERE tenant_id = $1 AND id = $2", "WHERE $1::text IS NOT NULL AND id = $2")
LIST_NO_WHERE: Edit = (PG, '"WHERE tenant_id = $1 AND ($2::text IS NULL OR name = $2) "',
                       '"WHERE $1::text IS NOT NULL AND ($2::text IS NULL OR name = $2) "')
LATEST_NO_WHERE: Edit = (PG, '"WHERE tenant_id = ANY($1::text[]) AND kind = $2 AND name = $3 "',
                         '"WHERE $1::text[] IS NOT NULL AND kind = $2 AND name = $3 "')
ENGINE_OPEN: Edit = (SESSION, 'duckdb.connect(":memory:", config=SANDBOX)', 'duckdb.connect(":memory:")')
#: The guard's reader names and its quoted-path rule, removed.
GUARD_BLIND: Edit = (GUARD, "|\\b(read_\\w+|\\w+_scan|sniff_csv|query|query_table)\\s*\\(", "|(?!x)x")
GUARD_BLIND_PATHS: Edit = (GUARD, "|\\b(FROM|JOIN)\\s+'", "|(?!x)x")


@dataclass(frozen=True)
class Break:
    label: str
    expect: str  # "caught" | "held" | "both walls"
    edits: tuple[Edit, ...]
    #: For "held": the tests of the wall that was broken.
    wall: tuple[str, str] | None = None


BREAKS = [
    Break("the subtree guard stops checking", "caught", ((API_ADMIN,
          "        await require_administer(get_store(), actor.id, target_id)\n", "        pass\n"),)),
    Break("the tenant list returns every tenant", "caught", ((API_ADMIN,
          "        children = await get_store().children_of(actor.id)\n",
          "        children = [t for t in await get_store().list_tenants() if t.id != actor.id]\n"),)),
    Break("a REST connector call reads the parent's vault", "caught", ((API_AUTH,
          "credentials_for(tenant.id, connector_name, fields)",
          "credentials_for(tenant.parent_id or tenant.id, connector_name, fields)"),)),
    Break("the hosted MCP reads the parent's vault", "caught", ((MCP,
          "return tenant, await resolver_for(store, tenant.id)",
          "return tenant, await resolver_for(store, tenant.parent_id or tenant.id)"),)),
    Break("credentials cached per process, not per tenant", "caught", ((STORE,
          "for connector, creds in (await store.credentials_many(tenant_id, wanted)).items()",
          ("for connector, creds in resolver_for.__dict__.setdefault("
          "'seen', await store.credentials_many(tenant_id, wanted)).items()")),)),
    Break("the memory store's get_run ignores the tenant", "caught", ((STORE,
          "        return r if r is not None and r.tenant_id == tenant_id else None\n", "        return r\n"),)),
    Break("the memory store's list_runs ignores the tenant", "caught", ((STORE,
          "            if r.tenant_id == tenant_id and (name is None or r.name == name)\n",
          "            if (name is None or r.name == name)\n"),)),
    Break("the PayPal token cache is keyed by client id alone", "caught", ((PAYPAL,
          'f"{client_id}:{secret}:{token_url}"', 'f"{client_id}:{token_url}"'),)),
    Break("Postgres credential read drops WHERE tenant_id", "held", (CREDS_NO_WHERE,), WHERE_WALL),
    Break("Postgres get_run drops WHERE tenant_id", "held", (RUN_NO_WHERE,), WHERE_WALL),
    Break("Postgres list_runs drops WHERE tenant_id", "held", (LIST_NO_WHERE,), WHERE_WALL),
    Break("Postgres latest_runs drops WHERE tenant_id", "held", (LATEST_NO_WHERE,), WHERE_WALL),
    Break("the store never enters the restricted role", "held", (NO_SCOPE,), DB_WALL),
    Break("the query engine may read files and the network", "caught", (ENGINE_OPEN,)),
    Break("the statement guard misses the readers", "held", (GUARD_BLIND, GUARD_BLIND_PATHS),
          GUARD_WALL),
    Break("an open engine behind a blind guard", "both walls",
          (ENGINE_OPEN, GUARD_BLIND, GUARD_BLIND_PATHS)),
    Break("no restricted role, and get_run drops WHERE", "both walls", (NO_SCOPE, RUN_NO_WHERE)),
    Break("no restricted role, and the credential read drops WHERE", "both walls",
          (NO_SCOPE, CREDS_NO_WHERE)),
    Break("no restricted role, and list_runs drops WHERE", "both walls", (NO_SCOPE, LIST_NO_WHERE)),
]


def _passes(test_file: str, only: str = "") -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test_file]
    if only:
        cmd += ["-k", only]
    run = subprocess.run(cmd, check=False, cwd=ROOT, capture_output=True, text=True)
    return run.returncode == 0


def _apply(edits: tuple[Edit, ...]) -> dict[Path, str]:
    originals: dict[Path, str] = {}
    for rel, old, new in edits:
        path = ROOT / rel
        current = path.read_text()
        originals.setdefault(path, current)
        if current.count(old) != 1:
            for p, text in originals.items():
                p.write_text(text)
            raise SystemExit(f"{rel}: the text to break is not there exactly once; update this script")
        path.write_text(current.replace(old, new))
    return originals


def main() -> int:
    if not os.environ.get("OKWAN_VAULT_DATABASE_URL"):
        print("set OKWAN_VAULT_DATABASE_URL: the database walls need a real Postgres")
        return 2
    touched = sorted({rel for b in BREAKS for rel, _, _ in b.edits})
    if subprocess.run(["git", "diff", "--quiet", "--", *touched], check=False, cwd=ROOT).returncode != 0:
        print(f"uncommitted changes in {touched}; commit or stash them first")
        return 2
    if not (_passes(ISOLATION) and _passes(*DB_WALL)):
        print("the suites fail before anything is broken; fix that first")
        return 2

    ok = 0
    for b in BREAKS:
        originals = _apply(b.edits)
        try:
            if b.expect == "held":
                # The broken wall's own tests are left out of "still isolated".
                own = f" and not {b.wall[1]}" if b.wall == GUARD_WALL else ""
                isolation = _passes(ISOLATION, BOTH_WALLS + own)
                wall = _passes(*b.wall)
            else:
                isolation, wall = _passes(ISOLATION), None
        finally:
            for path, text in originals.items():
                path.write_text(text)
        if b.expect == "held":
            good = isolation and wall is False
            seen = ("isolation held" if isolation else "isolation FAILED") + (
                "; the broken wall's own tests noticed" if wall is False
                else "; the broken wall's own tests did NOT notice")
        else:
            good = not isolation
            seen = "isolation suite failed" if not isolation else "isolation suite PASSED: leak missed"
        ok += good
        print(f"{'ok  ' if good else 'FAIL'}  [{b.expect:>10}]  {b.label}: {seen}")
    print(f"\n{ok}/{len(BREAKS)} breaks behaved as expected")
    return 0 if ok == len(BREAKS) else 1


if __name__ == "__main__":
    sys.exit(main())
