# System

## What runs where

| Part | Code | Runs on | Talks to |
|---|---|---|---|
| API and hosted MCP | `apps/api`, `packages/*` | Render web service `okwan`: one Docker instance, one uvicorn worker, Python 3.12 (`Dockerfile`) | Vault database, the rails, Resend |
| Dashboard | `apps/dashboard` (Next.js 15, server-rendered) | Render web service `okwan-dashboard` | The API only |
| Public site | `apps/web` (Next.js 15, static export) | Vercel | Nothing at run time |
| Vault database | `packages/vault/okwan_vault/schema.sql` | Neon Postgres | — |
| Verification mail | `apps/api/okwan_api/mail.py` | Resend (sending only) | — |
| Monitor and CI | `.github/workflows/` | GitHub Actions | The public hosts; a throwaway Postgres in CI |

Okwan is **read-only towards every payment rail**: no connector a
reconciliation can use exposes a write operation, and the registry
refuses a declaration over one (`validate_against_registry`). The one
exception on hosted REST is WhatsApp's two send operations, which are a
messaging channel, not a rail; see [known-gaps.md](known-gaps.md).

## Who calls it

- **An agent or a developer's code** calls the REST API or the hosted MCP
  server with an Okwan API key (`okw_…`) as a bearer token. The key
  identifies one tenant. Rail credentials are never in the request; the
  API reads them from that tenant's vault.
- **A person** signs in to the dashboard with an email and password. The
  dashboard holds an API session token (`oks_…`) in an httpOnly cookie and
  calls the API server-side; the browser never sees the token or any
  credential. Session tokens reach only the administration routes.
- **The operator** runs `python -m okwan_vault …` with the database
  address and master key from Codespaces secrets, for what is
  deliberately not an API route (plans, password resets, revoking a key
  by id).

## How one request moves

```
caller ──HTTPS──▶ Edge (size cap, headers) ──▶ route
                                               │
                     key or session ──hash──▶ tenant   (login role: identity lookup)
                                               │
                          quota check, then the tenant's credentials
                          (okwan_tenant role, scoped to this tenant, RLS)
                                               │
                     connector ──HTTPS──▶ rail (read operations only)
                                               │
                rows ──▶ response; for SQL, a sandboxed in-memory DuckDB
                         for this request only; for a reconciliation, a
                         trimmed verdict stored per tenant
                                               │
                          usage counter +1 (okwan_tenant, scoped)
```

Nothing a request loads outlives it: credentials are read once per
request into a closure, each SQL query gets its own DuckDB connection,
and the one process-wide cache (PayPal access tokens) is keyed by a
digest of the client id, secret and token URL.

## Trust boundaries

1. **Internet → API.** Everything a caller sends is untrusted. Bodies over
   1 MiB are refused before parsing (`apps/api/okwan_api/edge.py`). Validation
   errors never echo the input.
2. **Caller → tenant.** The tenant is whoever the key or session hash
   resolves to, never a value in the request. Administration of another
   tenant passes `may_administer` (the caller's subtree) or answers 404.
3. **Tenant → tenant, in the database.** Two walls: a `WHERE tenant_id`
   in every query, and row-level security under the restricted role
   `okwan_tenant`. Each alone keeps tenants apart.
4. **Caller SQL → the server.** DuckDB runs with file, network and
   extension access off and its configuration locked; the statement guard
   is a second wall.
5. **API → caller-chosen hosts.** The Postgres connector dials a host the
   tenant supplied. `packages/core/okwan_core/egress.py` resolves first, refuses any
   address that is not publicly routable, and dials the address it
   checked.
6. **API → rails.** TLS to fixed hosts; read operations only.
7. **Dashboard → API.** Server to server. The dashboard forwards the
   browser's IP for rate limits only alongside a shared secret
   (`OKWAN_DASHBOARD_SECRET`).
8. **Operator → vault.** Full access by design: the master key and the
   database owner role. See [known-gaps.md](known-gaps.md) on the master
   key and on an action log.
