# Data inventory

Everything Okwan stores, from `packages/vault/okwan_vault/schema.sql`,
and what it holds only for the length of a request.

## Stored in the vault database (Neon Postgres)

| Table | Holds | Protection | Kept |
|---|---|---|---|
| `tenants` | Id, name (a signup's email address for self-serve accounts), parent | Identity table: login role only | Until deleted by the operator; children cascade |
| `accounts` | Email, tenant, password hash | scrypt (N=2^14, r=8, p=1), per-password salt; 12 to 256 characters | As the tenant |
| `signups` | Email, password hash, verification token **hash**, expiry | Token is 32 random bytes, stored as SHA-256 only, single use | 24 hours by expiry check; expired rows are not purged |
| `sessions` | Session token **hash**, tenant, expiry | SHA-256 only; httpOnly, Secure, SameSite=Lax cookie on the dashboard | 7 days by expiry check; deleted on sign-out and on password reset; expired rows are not purged |
| `api_keys` | Key id, public prefix, key **hash**, revoked time | 32 random bytes, SHA-256 only, shown once | Revoked keys are kept, flagged |
| `credentials` | A tenant's rail credentials | AES-256-GCM per value with its own data key; data key wrapped by the master key; AAD binds tenant, connector and field; row-level security | Until replaced or deleted |
| `usage` | Hourly request counts per tenant and surface | Row-level security | Not purged |
| `plans` | A root tenant's plan and limit | Row-level security | As the tenant |
| `support_conversations` | The support assistant's transcripts: customer text after secret redaction, the assistant's turns, the read-only tools' results (plan and usage, which connections are saved, test results, run summaries and errors, key prefixes) | Row-level security; secrets removed before storage | 30 days after the last message; the save prunes older ones |
| `support_tickets` | What the assistant handed to a person: severity, category, its summary (secrets removed), whether the mail went out | Row-level security | Not purged |
| `reconciliation_runs` | Each run's summary and trimmed per-order verdicts (outcome, order reference, currency, totals, per-rail amounts, reason); a scrubbed error, at most 300 characters | Row-level security; no raw rail record is written (`okwan_recon` trims rows) | Newest 50 per tenant, kind and name; the insert prunes the rest |

**Not stored anywhere:** card numbers (no rail returns one and no table
has a column for one), raw rail records, query text or query results,
request bodies, customer email addresses or names from the rails.

## Held only while a request runs

| What | Where | Gone when |
|---|---|---|
| A tenant's decrypted credentials | A closure in the request (`resolver_for`) | The request ends |
| Rail rows for a SQL query | An in-memory DuckDB connection, one per query | The query returns |
| Rail rows for a reconciliation | Python objects | The response is built; only the trimmed verdict is stored |
| Rate-limit counters (IP, address, tenant) | Process memory, fixed windows | The window ends, or the process restarts |
| PayPal access tokens | Process memory, keyed by SHA-256 of client id, secret and token URL | Expiry minus 60 seconds |

## Where else data goes

| Recipient | What | Why |
|---|---|---|
| The rails | The tenant's own credentials, as each rail's API requires | To read that tenant's records |
| Resend | Recipient address and the verification link; support escalations (tenant id and name, the assistant's summary, the customer's last three messages, secrets removed) to `OKWAN_SUPPORT_ESCALATION_TO` | To deliver them; tracking must be off for the sending domain |
| Anthropic | A support conversation: the customer's messages and the tools' results, secrets removed, plus Okwan's own documentation | To answer it (Claude Opus 5.5). Anthropic becomes a subprocessor: [owner-and-legal.md](owner-and-legal.md) |
| Render logs | Warnings naming a tenant id when usage or a run could not be recorded; startup errors | Operations. No credential, token, link or query text is logged |
| GitHub | Monitor results: which public check failed and why | Incident issues |

## Secrets the service runs with

Set on the Render services, never in the repository:
`OKWAN_VAULT_MASTER_KEY`, `OKWAN_VAULT_DATABASE_URL`, `RESEND_API_KEY`,
`OKWAN_DASHBOARD_SECRET`, `ANTHROPIC_API_KEY`. The master key opens every stored credential;
see [known-gaps.md](known-gaps.md), "Master key in an environment
variable".
