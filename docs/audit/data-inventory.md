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
| `api_keys` | Key id, public prefix, key **hash**, revoked time, scope (`read` or `admin`) | 32 random bytes, SHA-256 only, shown once | Revoked keys are kept, flagged |
| `credentials` | A tenant's rail credentials. For Gusto, nothing typed: the company's uuid, access and refresh tokens and the access token's expiry, written by Connect Gusto's callback and rewritten each time the two-hour token is renewed | AES-256-GCM per value with its own data key; data key wrapped by the master key; AAD binds tenant, connector and field; row-level security | Until replaced or deleted |
| `usage` | Hourly request counts per tenant and surface | Row-level security | Not purged |
| `plans` | A root tenant's plan and limit | Row-level security | As the tenant |
| `support_conversations` | The support assistant's transcripts: customer text after secret redaction, the assistant's turns, the read-only tools' results (plan and usage, which connections are saved, test results, run summaries and errors, key prefixes) | Row-level security; secrets removed before storage | 30 days after the last message; the save prunes older ones |
| `support_tickets` | What the assistant handed to a person: severity, category, the issue, its brief (findings, likely cause, steps, a draft reply to the customer), secrets removed, and whether the mail went out | Row-level security | Not purged |
| `support_replies` | Replies an operator sent from the console: the text, who sent it, whether the mail went out | Row-level security | Not purged |
| `operator_factors` | Each operator's authenticator secret, sealed like a credential | Identity table: login role only; ciphertext under its own data key | Until re-enrolled |
| `operator_actions` | Every operator action: who, when, what, which ticket or account | Identity table: login role only; append-only, enforced by database triggers | Not purged, and cannot be |
| `billing` | A paying account's Stripe customer and subscription ids, the subscription's state and renewal date. Never card details | Identity table: login role only (the signed webhook finds the tenant by customer) | As the tenant |
| `billing_checkouts` | One row for each Stripe Checkout session an account opened: Stripe's session id, the refund policy's version shown, the time, and the caller's IP address; once paid, when, the subscription it started, and when Stripe reported the terms as agreed to (where a tick is required). Written once, marked paid once. Never card details | Identity table: login role only | As the tenant; an unpaid session's row goes as soon as Stripe confirms the session ended (when the account opens another, or pays), and after seven days otherwise |
| `billing_customers` | Each Stripe customer that has paid for Pro, or begun to, for an account: Stripe's customer id, the account, and when the two were linked. An account can have more than one. How a payment is known to be Okwan's on a Stripe account that takes other products' payments | Identity table: login role only | Kept when the tenant is deleted, with the tenant's id cleared: Stripe may still charge that customer, and the payment has to be recognised as Okwan's |
| `billing_payments` | Each payment Stripe reported succeeding: Stripe's charge id and customer id, the amount in minor units, the currency, the time, and whether it is a payment for Pro (its customer is in `billing_customers`). The Stripe account takes payments for other products, and Stripe sends them all: one that is not Okwan's is kept unmatched for a week and then deleted. Never card details | Identity table: login role only | Okwan's own are kept when the tenant is deleted, with the tenant's id cleared: they are what the dispute rate is measured against. Unmatched ones go after seven days |
| `billing_disputes` | Each dispute, inquiry and early fraud warning on the Stripe account: Stripe's ids, the amount, the bank's reason, the state, the date an answer is due, when it became a dispute, whether evidence was saved and whether an answer was sent, whether its charge is a payment for Pro, what the operator was last mailed about it, and since when a mail about it is being sent. The evidence itself is not stored: it is put together from the other tables when the operator opens the dispute | Identity table: login role only; read only by an unlocked operator | Kept when the tenant is deleted, with the tenant's id cleared: the dispute still has to be answered |
| `reconciliation_runs` | Each run's summary and trimmed per-order verdicts (outcome, order reference, currency, totals, per-rail amounts, reason); for a two-sided check, each row's status, each side's reference, the amounts compared and the dates, a record with no counterpart by its reference, amount and date, and a group's members by reference, amount and date; a scrubbed error, at most 300 characters | Row-level security; no raw rail record is written (`okwan_recon` trims rows) | Newest 50 per tenant, kind and name; the insert prunes the rest |
| `import_files` | One uploaded file's record: kind (settlement batches, settlement transactions, bank deposits, delivery lines, delivery payouts or tax filings), its name, format, the column mapping used, SHA-256 of the bytes, row and skipped-row counts, the names of any card columns it had, the span of dates it covers | Row-level security; the file itself is never written | 13 months from upload: reads skip it from then, an upload prunes the tenant's expired files, the API's hourly sweep deletes every tenant's; deleting the file or the tenant deletes it at once |
| `import_layouts` | A tenant's saved column mappings, by name: the kind, which column fills which field, and the reading choices (currency, date order, decimal mark). Column names only, never a value from a file | Row-level security | Until deleted, or with the tenant |
| `access_log` | One row per metered read of a tenant's data: when, the API key's public prefix (`okw_` and eight characters) or "session" for the dashboard, the caller's IP address and its network (/24 or /48), the surface, and what was read (a route, or a hosted MCP tool and the reconciliation it ran). Never the key, query text, parameters, rows or results | Row-level security | 90 days; the API's hourly sweep deletes older rows; deleting the tenant deletes them at once |
| `access_alerts` | What the access log raised: a key on a network it had not used in 30 days (the address and network, the surface and route) or an unusual hour (the count and the key's busiest hour before), the key's prefix, whether the mail went out | Row-level security | 400 days; the hourly sweep deletes older ones; deleting the tenant deletes them at once |
| `notify_settings` | An account's two choices about the daily check (run it; mail what is new), the token in the mail's unsubscribe link (`unsub_<account id>.<24 random bytes>`), and when the account was last mailed findings | Row-level security. The token is kept as it is: it can only turn the mail off, and anyone signed in can turn it back on | As the tenant |
| `daily_checks` | The daily check's own record, one row a merchant: when it was last checked; a fingerprint of each finding the account has been told of (the first 20 hex characters of a SHA-256 over the outcome and the order reference; never the reference); and, for each finding not yet mailed, its fingerprint, outcome, three-letter currency code and amounts in minor units, kept until the mail goes | Row-level security. Written by the scheduler only: no route reads or writes it, and nothing in it is read back from stored runs | As the tenant |
| `import_rows` | The mapped fields of each row of an imported file (references, dates, amounts in minor units, merchant or store id, an order's status and type, statement text, at most the last four digits of a card or account), with the file and row it came from | Row-level security; only mapped fields are written; a file holding a full card number anywhere is refused before anything is written (`okwan_files.guard`: network prefix, length and Luhn) | As its file |

**Not stored anywhere:** card numbers (no rail returns one, no table
has a column for one, and an uploaded file holding one is refused
whole), raw rail records, uploaded files themselves, query text or query
results, request bodies, customer email addresses or names from the
rails, and anything about a business's employees beyond what a check
reads in the request: Gusto's employee uuid and the employee's tips.

## Held only while a request runs

| What | Where | Gone when |
|---|---|---|
| A tenant's decrypted credentials | A closure in the request (`resolver_for`) | The request ends |
| Rail rows for a SQL query | An in-memory DuckDB connection, one per query | The query returns |
| Rail rows for a reconciliation | Python objects | The response is built; only the trimmed verdict is stored |
| A payroll's detail from Gusto, read for `gusto.payroll_tips`: Okwan takes each employee's uuid and two tip amounts (cash and paycheck) and drops the rest of the response (names, wages, pay, deductions, taxes) as it parses it | Python objects (`okwan_gusto.schemas.PayrollTip`) | The page is built |
| An uploaded file's bytes and every cell in it | The upload request (`okwan_api.imports`), at most 8 MB | The request ends; only the mapped fields of each row are stored, or nothing when the file is refused |
| Rate-limit counters (IP, address, tenant) | Process memory, fixed windows | The window ends, or the process restarts |
| PayPal access tokens | Process memory, keyed by SHA-256 of client id, secret and token URL | Expiry minus 60 seconds |
| Who is calling: the key's public prefix or "session", and the caller's address | The request's context (`okwan_api.access`), for the access row | The request ends |
| Which (tenant, key prefix, network) raised a new-network alert | Process memory, so two reads racing in from one new network raise one alert | An hour |
| A Gusto connection being started: its state, the tenant and the account that started it | Process memory (`okwan_api.connect`) | Ten minutes, or its use |
| The new Gusto token pair each spent refresh token became, so two racing requests renew once | Process memory (`okwan_gusto.auth`) | The process restarts |
| A Clover merchant's timezone and default currency | Process memory, one entry per connection, held weakly by that connection's transport and never shared between two | The connection closes |

## Where else data goes

| Recipient | What | Why |
|---|---|---|
| The rails | The tenant's own credentials, as each rail's API requires. For Gusto, Okwan's own client id and secret with the approval code or the refresh token, to Gusto's token endpoint | To read that tenant's records |
| Resend (the daily check's mail) | The account's sign-in address; the account's name; each merchant with something new, by name and tenant id (in a dashboard link); how many orders per outcome and the amounts; the unsubscribe link. Never an order reference, a customer, a payment record or a credential | To tell an account what its daily check found (`okwan_api.schedule`); off per account in Settings or by the mail's own link |
| Resend | Recipient address and the verification link; access alerts (the account's sign-in address, its name and tenant id, the key's prefix, the caller's address and network, the time, the surface and route or the hour's count) to the account and to `OKWAN_SUPPORT_ESCALATION_TO`; support escalations (ticket id, tenant id and name, the account's sign-in address, the assistant's summary, the customer's last three messages, secrets removed and every customer value escaped as text) to `OKWAN_SUPPORT_ESCALATION_TO`; notices of a dispute on a payment for Pro (Stripe's ids for the dispute and the payment, the amount, the bank's reason, the date an answer is due, Okwan's dispute rate, a link to the console page, and the account's Okwan id; never its name or sign-in address, and nothing a customer wrote) to `OKWAN_SUPPORT_ESCALATION_TO` | To deliver them; tracking must be off for the sending domain |
| Anthropic | A support conversation: the customer's messages and the tools' results, secrets removed, plus Okwan's own documentation | To answer it (Claude Opus 5.5). Anthropic becomes a subprocessor: [owner-and-legal.md](owner-and-legal.md) |
| Stripe | Checkout and portal requests: the account's tenant id (as metadata), its sign-in address, the Pro price id; Stripe collects the card itself | To take payment for Pro. Okwan's own Stripe account, not a connector |
| Render logs | Warnings naming a tenant id when usage, a run or an access row could not be recorded; an access alert's kind, tenant id and alert id (not the address); uvicorn's line for each request (the connecting address, method, path with its query string, status); startup errors | Operations. No credential, token, link or query text is logged |
| GitHub | Monitor results: which public check failed and why | Incident issues |

## Secrets the service runs with

Set on the Render services, never in the repository:
`OKWAN_VAULT_MASTER_KEY`, `OKWAN_VAULT_DATABASE_URL`, `RESEND_API_KEY`,
`OKWAN_DASHBOARD_SECRET`, `ANTHROPIC_API_KEY`, `OKWAN_GUSTO_CLIENT_ID`, `OKWAN_GUSTO_CLIENT_SECRET`. The master key opens every stored credential;
see [known-gaps.md](known-gaps.md), "Master key in an environment
variable".
