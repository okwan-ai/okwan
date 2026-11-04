# Okwan audit pack

Prepared for an independent security and production review before the
first paying customer signs. It describes the system as it is, cites the
code and the test behind every claim, and lists what is missing as
plainly as what is there. Nothing here asks to be taken on trust: every
control in [controls.md](controls.md) names a test you can run, and
`tests/test_audit_pack.py` fails if a file or test this pack cites stops
existing.

| Document | What it answers |
|---|---|
| [system.md](system.md) | What runs where, how data moves, where the trust boundaries are |
| [data-inventory.md](data-inventory.md) | Every kind of data Okwan holds, where, how protected, for how long |
| [threat-model.md](threat-model.md) | Who could attack what, how, and what stops them |
| [controls.md](controls.md) | Each security claim, the code that enforces it, the test that proves it |
| [known-gaps.md](known-gaps.md) | What is missing or weak, ranked, with the plan for each |
| [dependencies.md](dependencies.md) | Direct dependencies, the versions verified, how to inventory the rest |
| [owner-and-legal.md](owner-and-legal.md) | Items for the owner and counsel, flagged, not drafted |
| [../ops/RUNBOOK.md](../ops/RUNBOOK.md) | Operations: who answers, monitoring, failure procedures |

## Scope

In scope: the API and hosted MCP server (`apps/api`, `packages/*`), the
dashboard (`apps/dashboard`), the public site (`apps/web`), the vault
database schema, the CI and monitoring workflows, and the hosting
configuration as documented in `render.yaml` and the runbook.

Out of scope: the rails themselves (Stripe, Shopify, PayPal, Paystack),
the hosting providers' own controls (Render, Neon, Vercel, Resend), and
anything in `NIW_WORKING_PACK.md`.

## Reproduce the results

Everything below runs from a clone, with Python 3.12, Node 22 and a
PostgreSQL 16 the test user may create roles in (as the CI job does):

```bash
pip install -e ".[dev]"
```

```bash
export OKWAN_VAULT_DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/okwan_vault_test
```

The full suite, with the database (nothing tenant-related skips):

```bash
python -m pytest -q
```

Tenant isolation on every surface, over three stores, and the database
wall alone:

```bash
python -m pytest -q tests/test_tenant_isolation.py tests/test_tenant_rls.py
```

The proof that those tests can fail: each planted leak is caught, or
held by the other wall and noticed by the broken wall's own tests. It
edits source files and restores them, and refuses to start on a dirty
tree.

```bash
python scripts/plant_leaks.py
```

The production surface from outside (against the live hosts by default;
`OKWAN_MONITOR_API` and friends point it elsewhere):

```bash
python scripts/monitor.py
```

Both web apps:

```bash
cd apps/dashboard && npm ci && npm run typecheck && npm run build
```

The same commands run in CI on every pull request
(`.github/workflows/ci.yml`) and the proof weekly
(`.github/workflows/isolation.yml`).

## Found while preparing this pack

**Caller SQL could read the server's environment** (closed 2026-11-03).
The query engine had file access on and the statement guard missed
`read_text`, so `SELECT content FROM read_text('/proc/self/environ')`
returned the vault's master key and database address to any API key
holder. Fixed in the engine (`packages/query/okwan_query/session.py`,
`SANDBOX`), tested through REST, MCP and the engine alone, and planted
back by `scripts/plant_leaks.py` to confirm the suite fails without it.
The exposure check and rotation are the owner's (`OKWAN_PROJECT.md` §10
item 29). Recorded in `OKWAN_PROJECT.md` §11.
