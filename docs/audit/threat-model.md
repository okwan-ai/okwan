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
| T5 | Stolen API key | Leaked by the customer | Stored only as a hash; revocation is immediate | Medium: no scopes, expiry or IP allowlist on keys; G4 |
| T6 | Stolen or guessed session | Cookie theft, password guessing | httpOnly, Secure, SameSite=Lax cookie; scrypt; sign-in limited per address and per IP; reset ends every session | Medium: no second factor, no idle timeout; G3 |
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
