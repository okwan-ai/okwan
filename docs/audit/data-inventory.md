# Data inventory

Everything Okwan stores, from `packages/vault/okwan_vault/schema.sql`,
and what it holds only for the length of a request.

## Stored in the vault database (Neon Postgres)

| Table | Holds | Protection | Kept |
|---|---|---|---|
| `tenants` | Id, name (a signup's email address for self-serve accounts), parent | Identity table: login role only | Until deleted by the operator; children cascade |
| `accounts` | Email, tenant, password hash | scrypt (N=2^14, r=8, p=1), per-password salt; 12 to 256 characters | As the tenant |
| `signups` | Email, password hash, verification token **hash**, expiry | Token is 32 random bytes, stored as SHA-256 only, single use | 24 hours by expiry check; expired rows are not purged |
| `sessions` | Session token **hash**, tenant, expiry, when it was last used | SHA-256 only; httpOnly, Secure, SameSite=Lax cookie on the dashboard | 12 hours from sign-in, or 30 minutes without use, whichever is first; deleted on sign-out and on password reset; expired rows are not purged |
| `api_keys` | Key id, public prefix, key **hash**, revoked time | 32 random bytes, SHA-256 only, shown once | Revoked keys are kept, flagged |
| `credentials` | A tenant's rail credentials | AES-256-GCM per value with its own data key; data key wrapped by the master key; AAD binds tenant, connector and field; row-level security | Until replaced or deleted |
| `usage` | Hourly request counts per tenant and surface | Row-level security | Not purged |
| `plans` | A root tenant's plan and limit | Row-level security | As the tenant |
| `support_conversations` | The support assistant's transcripts: customer text after secret redaction, the assistant's turns, the read-only tools' results (plan and usage, which connections are saved, test results, run summaries and errors, key prefixes) | Row-level security; secrets removed before storage | 30 days after the last message; the save prunes older ones |
| `support_tickets` | What the assistant handed to a person: severity, category, the issue, its brief (findings, likely cause, steps, a draft reply to the customer), secrets removed, and whether the mail went out | Row-level security | Not purged |
| `support_replies` | Replies an operator sent from the console: the text, who sent it, whether the mail went out | Row-level security | Not purged |
| `operator_factors` | Each operator's authenticator secret, sealed like a credential | Identity table: login role only; ciphertext under its own data key | Until re-enrolled |
| `operator_actions` | Every operator action: who, when, what, which ticket or account | Identity table: login role only; append-only, enforced by database triggers | Not purged, and cannot be |
| `billing` | A paying account's Stripe customer and subscription ids, the subscription's state and renewal date. Never card details | Identity table: login role only (the signed webhook finds the tenant by customer) | As the tenant |
| `reconciliation_runs` | Each run's summary and trimmed per-order verdicts (outcome, order reference, currency, totals, per-rail amounts, reason); a scrubbed error, at most 300 characters | Row-level security; no raw rail record is written (`okwan_recon` trims rows) | Newest 50 per tenant, kind and name; the insert prunes the rest |
| `import_files` | One uploaded file's record: kind (settlement batches, settlement transactions or bank deposits), its name, format, the column mapping used, SHA-256 of the bytes, row and skipped-row counts, the names of any card columns it had, the span of dates it covers | Row-level security; the file itself is never written | 13 months from upload: reads skip it from then, an upload prunes the tenant's expired files, the API's hourly sweep deletes every tenant's; deleting the file or the tenant deletes it at once |
| `import_rows` | The mapped fields of each row of an imported file (references, dates, amounts in minor units, merchant id, statement text, at most the last four digits of a card or account), with the file and row it came from | Row-level security; only mapped fields are written; a file holding a full card number anywhere is refused before anything is written (`okwan_files.guard`: network prefix, length and Luhn) | As its file |

**Not stored anywhere:** card numbers (no rail returns one, no table
has a column for one, and an uploaded file holding one is refused
whole), raw rail records, uploaded files themselves, query text or query
results, request bodies, customer email addresses or names from the
rails.

## Held only while a request runs

| What | Where | Gone when |
|---|---|---|
| A tenant's decrypted credentials | A closure in the request (`resolver_for`) | The request ends |
| Rail rows for a SQL query | An in-memory DuckDB connection, one per query | The query returns |
| Rail rows for a reconciliation | Python objects | The response is built; only the trimmed verdict is stored |
| An uploaded file's bytes and every cell in it | The upload request (`okwan_api.imports`), at most 8 MB | The request ends; only the mapped fields of each row are stored, or nothing when the file is refused |
| Rate-limit counters (IP, address, tenant) | Process memory, fixed windows | The window ends, or the process restarts |
| PayPal access tokens | Process memory, keyed by SHA-256 of client id, secret and token URL | Expiry minus 60 seconds |

## Where else data goes

| Recipient | What | Why |
|---|---|---|
| The rails | The tenant's own credentials, as each rail's API requires | To read that tenant's records |
| Resend | Recipient address and the verification link; support escalations (ticket id, tenant id and name, the account's sign-in address, the assistant's summary, the customer's last three messages, secrets removed and every customer value escaped as text) to `OKWAN_SUPPORT_ESCALATION_TO` | To deliver them; tracking must be off for the sending domain |
| Anthropic | A support conversation: the customer's messages and the tools' results, secrets removed, plus Okwan's own documentation | To answer it (Claude Opus 5.5). Anthropic becomes a subprocessor: [owner-and-legal.md](owner-and-legal.md) |
| Stripe | Checkout and portal requests: the account's tenant id (as metadata), its sign-in address, the Pro price id; Stripe collects the card itself | To take payment for Pro. Okwan's own Stripe account, not a connector |
| Render logs | Warnings naming a tenant id when usage or a run could not be recorded; startup errors | Operations. No credential, token, link or query text is logged |
| GitHub | Monitor results: which public check failed and why | Incident issues |

## Secrets the service runs with

Set on the Render services, never in the repository:
`OKWAN_VAULT_MASTER_KEY`, `OKWAN_VAULT_DATABASE_URL`, `RESEND_API_KEY`,
`OKWAN_DASHBOARD_SECRET`, `ANTHROPIC_API_KEY`. The master key opens every stored credential;
see [known-gaps.md](known-gaps.md), "Master key in an environment
variable".
