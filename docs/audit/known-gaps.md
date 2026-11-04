# Known gaps

What is missing or weak, most serious first. Each has a plan; none is
hidden from the auditor. Closing them is tracked in `OKWAN_PROJECT.md` §10.

| # | Gap | Why it matters | Plan |
|---|---|---|---|
| G1 | **Master key in an environment variable.** Production seals credentials under `OKWAN_VAULT_MASTER_KEY` on the Render service. `KmsMasterKey` exists (`packages/vault/okwan_vault/keys.py`) but its client library is not a dependency, so it cannot run as deployed | Whoever reads the service's environment (Render access, or a hole like the one closed on 2026-11-03) can open every stored credential. There is no tool to re-wrap data keys under a new master key | Move to a KMS (the provider interface is already in place), and add a re-wrap command so the master key can be rotated without re-entering credentials |
| G2 | **No action log.** Credential changes, key issue and revoke, sign-ins, plan changes and operator commands are not recorded | An incident cannot be reconstructed, and an insider's actions leave no trace | An append-only table written in the same transaction as each action, readable per tenant; `OKWAN_PROJECT.md` §10 item 24. **Narrowed 2026-11-04:** the operator console records everything done in it, append-only (C33); the rest is still open |
| G3 | **No second factor at sign-in, no idle timeout.** A session lasts 7 days from sign-in | A stolen password or cookie gives a week of access to that tenant's administration | TOTP for logins, and a shorter idle expiry on sessions |
| G4 | **Keys are all-powerful within their tenant.** No scopes (read one connector, query only), no expiry, no IP allowlist, one login per tenant and no roles | A leaked agent key reads everything its tenant can | Scoped keys first (they also fit agents best), then members with roles |
| G5 | **No purge of expired rows.** Expired sessions and signups, revoked keys and old usage stay until deleted; there is no tenant offboarding command | Holds data longer than needed; a deletion request has no procedure | A scheduled purge, and an operator `tenant delete` that cascades, with a written retention policy (owner and counsel, [owner-and-legal.md](owner-and-legal.md)) |
| G6 | **A query has no memory limit or timeout.** One uvicorn worker serves everyone | One expensive query from one tenant slows or stops every tenant | DuckDB `memory_limit` and `threads` in the sandbox, and a wall-clock limit on query execution |
| G7 | **Single instance, limits in memory, no edge firewall in front of Render** | A restart resets rate limits; one instance is one failure; floods reach the app | A second instance once there is traffic, with limits in the database or a shared cache; Render or a CDN in front for volumetric protection |
| G8 | **WhatsApp send operations are mounted on hosted REST** | The only write operations an API key can call. Not a payment rail, but a key holder can send messages from the tenant's WhatsApp number | Unmount non-read operations from hosted REST, or keep them behind a scope (G4); `OKWAN_PROJECT.md` §10 item 23 |
| G9 | **Supply chain.** Python dependencies are ranges with no lockfile; GitHub Actions are pinned by tag, not commit; no automated vulnerability scan | A compromised release or retagged action runs in the build or CI | A lockfile for the image (`pip-compile` or `uv lock`), actions pinned to commits, `pip-audit` and `npm audit` in CI |
| G10 | **A rail's 401 passes through as Okwan's 401** | A customer reads "unauthorized" and suspects their Okwan key when their Stripe key is wrong | Map upstream auth failures to a distinct status or error code |
| G11 | **No backup restore has been tested.** Neon keeps point-in-time history according to its plan; nobody has restored from it | A backup never restored is a hope | A restore drill into a branch, written into the runbook |
| G13 | **The support assistant can be wrong.** It answers from Okwan's documentation and the account's own data, but a model can misread either | A customer acts on a wrong instruction. It cannot change anything itself, and it says it is an AI | Escalation on anything unresolved; every conversation kept 30 days for review; customer reports of wrong answers treated as bugs (runbook, "Support assistant") |
| G14 | **Secret redaction is pattern-based.** Okwan's own tokens, the rails' key formats, connection-string passwords and long opaque tokens are caught; a short password typed in prose is not | A customer's password reaches the model and the transcript | Tell customers in the panel never to paste secrets (it does); widen the patterns as formats are seen |
| G15 | **One person behind the assistant.** Tier 1 is automated; everything it escalates reaches one person | An escalation waits while that person is away | A second on-call engineer (owner decision, runbook "Owner decisions") |
| G16 | **The console's second factor is one authenticator app.** There are no recovery codes, and the operator log is readable only by operators and the database owner | A lost phone locks the owner out of the console until they re-enroll from the Codespace | Recovery is re-running `python -m okwan_vault operator enroll`, which needs the vault master key; a hardware key later |
| G12 | **No penetration test, no SOC 2.** This pack and its tests are the evidence today | Larger customers will ask for both | The owner's choice of firm and timing ([owner-and-legal.md](owner-and-legal.md)) |

## Closed while preparing this pack

| Was | Closed by |
|---|---|
| Caller SQL could read files and the environment, including the master key (critical) | DuckDB sandbox, `packages/query/okwan_query/session.py`; controls C11–C13 |
| Tenant isolation lived only in application `WHERE` clauses | Row-level security as a second wall; controls C5–C9 |
| Isolation was asserted, not proven per surface | `tests/test_tenant_isolation.py`, `scripts/plant_leaks.py`; controls C1–C4, C10 |
| No request-size limit; missing protective headers | `apps/api/okwan_api/edge.py`, `apps/dashboard/next.config.ts`; controls C18–C19 |
| A SQL error answered 500 | 400 with the engine's message (`packages/query/okwan_query/rest.py`) |
| Tests ran only where someone ran them; the Postgres half skipped without a database | `.github/workflows/ci.yml`; control C22 |
| No monitoring, alerting or runbook | `scripts/monitor.py`, `.github/workflows/monitor.yml`, `docs/ops/RUNBOOK.md`; live since 2026-11-04 with a signed-in check |
| A change could reach `main`, and so production, without passing CI | Ruleset `main`: pull request and the three CI checks required, force push and deletion blocked; control C23 |
