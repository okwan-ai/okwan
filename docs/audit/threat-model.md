# Threat model

**What is worth stealing:** a tenant's rail credentials (they read that
merchant's payments), the master key and database address (together,
every tenant's credentials), one tenant's reconciliation results, and
the ability to act as another tenant.

**Who might try:** any member of the public (signup is self-serve, so an
attacker can be a tenant), a tenant reaching for another tenant, a
malicious or compromised agent holding a legitimate key, someone with a
stolen key or session, and an insider with hosting access.

| # | Threat | Path | What stops it | Residual risk |
|---|---|---|---|---|
| T1 | One tenant reads another's data through the API | Any data route, any MCP tool, the admin routes | The tenant comes from the key or session hash only; subtree guard answering 404; `WHERE tenant_id`; row-level security | Low. Proven per surface and per wall; see controls C1–C6 |
| T2 | Caller SQL reads the server: files, environment, network | `POST /v1/query`, MCP `okwan_query` | DuckDB with external access off, extensions off, configuration locked; statement guard as a second wall | Low since 2026-11-03. Before that, open: [README](README.md), "Found while preparing this pack" |
| T3 | Caller SQL exhausts the one worker | A huge cross join, `range()` | Row limit on results; 1 MiB body cap | **Open.** No memory limit or timeout on a query; [known-gaps.md](known-gaps.md) G6 |
| T4 | Caller-chosen host reaches internal services (SSRF) | Postgres connector connection string | Egress guard: resolve, refuse non-public addresses, dial the checked address (`packages/core/okwan_core/egress.py`) | Low |
| T5 | Stolen API key | Leaked by the customer | Stored only as a hash; revocation is immediate; every read is recorded with the key's prefix and the caller's address and network, and a key on a new network or reading far above its usual hour alerts the account (C39, C40) | Medium: a stolen read key reads every connector until revoked (no per-connector scope, expiry or IP allowlist); it can no longer add merchants, issue keys or replace credentials (C44, T21); detection is by mail, within the hour for volume; G4, G17 |
| T6 | Stolen or guessed session | Cookie theft, password guessing | httpOnly, Secure, SameSite=Lax cookie; scrypt; sign-in limited per address and per IP; reset ends every session; a session ends after 30 minutes without use and 12 hours after sign-in (C37) | Medium: no second factor at customer sign-in; G3 |
| T7 | Account and tenant enumeration | Signup, sign-in, admin ids | Signup's answer never says whether an address is registered; one 429 message for every limit; a foreign tenant is 404 like a missing one | Low |
| T8 | Database dump or backup leaks | Neon compromise, a copied backup | Credentials are ciphertext under per-value keys; AAD stops moving ciphertext between tenants; tokens, keys and passwords are hashes | Medium: the master key sits in the API's environment, not a KMS; G1 |
| T9 | Verification or reset link intercepted | Mail provider, link tracking | Token single-use, 24-hour expiry, hashed at rest; tracking off; operator resets are out of band | Low |
| T10 | Writing to a rail | A connector operation that moves money | No reconciliation can be declared over a write operation (`validate_against_registry`); the SQL guard and sandbox allow reads only | Low. WhatsApp sends are mounted on REST; G8 |
| T11 | Response embedded, sniffed or cached | Framing, MIME sniffing, shared caches | DENY framing, nosniff, no-store, a locked policy on JSON, HSTS (`apps/api/okwan_api/edge.py`); dashboard headers (`apps/dashboard/next.config.ts`) | Low |
| T12 | Hosted MCP reached through DNS rebinding | A browser on a hostile page | Host and origin allowlist when `OKWAN_ALLOWED_HOSTS` is set | Low while set; it is set in production (`render.yaml`) |
| T13 | Insider or compromised hosting account | Render, Neon or GitHub access | Secrets only in provider settings and Codespaces; nothing secret in the repository | **Open.** Hosting access is full access, and no action log records what an operator did; G1, G2 |
| T14 | Dependency or CI supply chain | A malicious package or action | Python 3.12 pinned in the image; npm lockfiles; ruff pinned to a minor | Medium: Python dependencies are ranges with no lockfile; actions pinned by tag, not commit; no automated vulnerability scan; G9 |
| T16 | The support assistant is steered by what it reads | A customer's message, or text inside a tool result (a run error, a tenant name), telling the model to do something else | Every tool reads and runs as the caller through the subtree guard; the only write is a ticket; tool results are framed as data in the system prompt; the transcript is server-side, so earlier turns cannot be forged | Low: the worst a steered assistant can do is say something wrong or open a ticket. G13 |
| T17 | A secret reaches the model or its transcript | A customer pastes a key; a rail quotes one in an error | `redact` on every customer message and every tool result before the model or the database sees it; the customer is told to replace what was removed | Medium: pattern-based, so an unusual secret format can pass. G14 |
| T18 | Someone runs up the model bill | Scripted messages to the assistant | Rate limit per account (40 an hour) and per address (60); six tool rounds a message, twenty messages a conversation; the Anthropic console's spend limit | Low |
| T19 | Someone reaches the operator console | A stolen operator session, a customer probing `/v1/ops`, an API key | Allowlist on the sign-in address; a plain 404 for everyone else; an authenticator code per session for 30 minutes, single-use and rate limited; every action logged append-only | Low |
| T20 | Another website acts as the signed-in user, or reads what Okwan returns | A page the user visits sends requests to the API or the dashboard in the background | No CORS anywhere, so no response is readable cross-origin and no preflight passes; the API authenticates by header only and ignores cookies; the session cookie is SameSite=Lax, httpOnly, Secure; every dashboard handler that changes data requires the dashboard's own Origin (C42) | Low. No request-verification token on top of the Origin check: every changing request is JSON from the dashboard's own pages, and a request with no Origin is refused |
| T21 | A key given to an AI client or a reporting job is used to administer the account | The key sits in a plain-text client config, a laptop or a CI secret, and leaks | Keys are `read` or `admin`, stated where each is made; new keys are read-only unless admin is ticked or named; MCP setup only issues read keys; a read key is refused with 403 at every administration route, found from the app itself, so it cannot add a merchant, issue or revoke a key, replace a saved credential, delete an import or open support (C44) | Low for administration; a read key still reads every connector (G4). Keys from before this change keep admin until the customer replaces them |
| T15 | Denial of service | Floods of requests | 1 MiB body cap; rate limits on signup, sign-in, verification and the connection test; monthly quotas | Medium: one instance, one worker, limits in memory per instance; no edge firewall in front of Render; G6, G7 |

## Assumptions

- Render, Neon, Vercel, Resend and GitHub operate their own controls as
  documented. Their compromise is out of scope except where noted (T8, T13).
- The rails authenticate and authorize their own APIs: a tenant's
  credential reads only what that rail lets it read.
- No surface accepts SQL against the vault database. Row-level security
  defends against a query bug in Okwan's code. It does not stop
  arbitrary SQL running as the login role, which could switch back from
  the restricted role. Nothing accepts such SQL today, and the
  restricted role cannot read the identity tables.
