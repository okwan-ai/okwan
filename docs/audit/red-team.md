# Red team from a second AI platform

Okwan's code is mostly written by one AI assistant. The same assistant
reviewing its own work shares the blind spots that wrote it, so a review
that matters comes from somewhere else. Three layers do that
(CLAUDE.md, "Independent review"):

1. **CodeQL on every pull request** (`.github/workflows/codeql.yml`): a
   semantic analyser that is not an AI and fails differently.
2. **An attacker's pass** on any change to the critical paths below: a
   fresh agent with an attacker's brief and none of the author's
   conclusions. A finding counts only with an exploit that runs.
3. **A second AI platform**, run by the owner with the prompt below:
   before the first ISV signs, after any change to a critical path, and
   at least once a quarter.

## The critical paths

| Area | Files |
|---|---|
| Who the caller is, and what a key may do | `apps/api/okwan_api/auth.py`, `packages/vault/okwan_vault/apikey.py`, `packages/vault/okwan_vault/accounts.py` |
| Whose data a caller may touch | `apps/api/okwan_api/admin.py` (`_guard`), `packages/vault/okwan_vault/authz.py`, `packages/vault/okwan_vault/schema.sql` (row-level security), `packages/vault/okwan_vault/postgres.py` |
| Secrets at rest | `packages/vault/okwan_vault/crypto.py`, `packages/vault/okwan_vault/keys.py`, `apps/api/okwan_api/scrub.py` |
| Caller SQL | `packages/query/okwan_query/guard.py`, `packages/query/okwan_query/session.py` |
| The hosted MCP server | `packages/query/okwan_query/mcp_http.py` |
| Connecting Gusto (OAuth) | `apps/api/okwan_api/connect.py`, `packages/connectors/gusto/okwan_gusto/auth.py`, `apps/dashboard/app/integrations/[name]/` |
| Files customers upload | `apps/api/okwan_api/imports.py`, `packages/connectors/files/okwan_files/` |
| The dashboard's session and its changes | `apps/dashboard/lib/api.ts`, `apps/dashboard/lib/respond.ts`, `apps/dashboard/app/**/route.ts` |
| The operator console | `apps/api/okwan_api/ops.py` |
| The daily check and its mail (Okwan reads rails and sends mail unasked) | `apps/api/okwan_api/schedule.py`, `apps/api/okwan_api/notify.py`, `apps/dashboard/app/unsubscribe/` |

## How to run it (owner, about ten minutes)

1. Open a different AI service from the one that builds Okwan (for
   example ChatGPT or Gemini), in a new conversation.
2. Paste the prompt below. The repository is public, so it can read the
   code at https://github.com/okwan-ai/okwan; if it cannot browse, paste
   the files of the area you want covered, one area at a time.
3. Paste its answer back to the assistant that builds Okwan. Each finding
   is reproduced as a failing test before anything is fixed; a finding
   that does not reproduce is answered with what was tried. Where the
   two disagree is where to look hardest.

Never paste a key, a token, a password or anything from the private
working pack into either platform.

## The prompt

```text
You are a red team hired to break a multi-tenant SaaS API before it takes
paying customers. Do not review or praise the code. Find every way to
break it, and for each one show exactly how.

The code: https://github.com/okwan-ai/okwan (Python 3.12, FastAPI, Pydantic
v2, DuckDB, Postgres with row-level security; a Next.js 15 dashboard).
Okwan reads a business's payment and commerce systems read-only and serves
the data over REST, SQL and an MCP server. Tenants form a tree: an ISV
account with merchant child tenants. Callers are API keys (each key is
"read" or "admin"; a read key may only read data, an admin key may also
administer its own subtree), dashboard sessions, and allowlisted operators.

The files that matter most are listed in docs/audit/red-team.md. What the
authors already know is weak is in docs/audit/known-gaps.md: do not repeat
those unless the code is worse than they say.

Try to:
1. Make a read-only key administer anything, by any route, header, method,
   casing or MCP tool.
2. Read or change another tenant's data, credentials, files, runs, keys or
   access log, on any surface.
3. Get a stored secret (a saved credential, an API key, a session token, an
   OAuth token, the master key, an environment variable) into a response,
   an error or a log.
4. Escape the SQL guard: read files, the environment or the network, reach
   another tenant's rows, or run anything that is not a read.
5. Connect a Gusto company to the wrong tenant, or replay or forge the
   OAuth state.
6. Make the dashboard change something for a request from another site.
7. Make the server loop, page forever or allocate without bound with a
   small request.
8. Make money come out wrong: a float, a rounding, a currency's decimals.

For each finding give: severity (critical, high, medium, low), the file
and line of the cause, the exact requests or code that exploit it, what
the attacker gets, and a one-line fix. Then list what you tried that held,
one line each, so the coverage is visible. If you are not sure a finding
is real, say so and say what would prove it.
```

## Record

| Date | Reviewer | What it found | What became of it |
|---|---|---|---|
| 2026-11-10 | An attacker's pass (fresh agent, attacker's brief, every finding proven by a running exploit) | **High:** a stored rail credential reached a read-only key through the data routes when a rail quoted the key it rejected (connector REST, `/v1/query`, reconciliations, the hosted MCP's query tool); only the connection test and run history scrubbed it. Confirmed again, already known: a query with no memory limit or timeout (G6), and sign-up verification limited per spoofable address (G17) | The high: reproduced by its exploit, then fixed where the error is made (C45, T22) with the exploit turned into `tests/test_upstream_redaction.py`. G6 and G17 stay as ranked in known-gaps.md. Held, with tries listed: a read key administering, cross-tenant reads on every surface, the SQL sandbox, SSRF through a Postgres DSN, Gusto state replay, dashboard cross-site changes, billing webhook forgery |
| 2026-11-10 | Bandit 1.9 (static analyser, local) | 33 items: 20 SQL built from strings, 2 XML parsing of uploads, 4 "hardcoded passwords", 7 low notes on running subprocesses | None real. The SQL interpolates only fixed column lists or quoted identifiers, every value is a bound parameter, and the Postgres connector's own query runs the customer's SQL on their own database in a read-only transaction by design; the XML parser already refuses a DOCTYPE or ENTITY and caps the unpacked size; the "passwords" are an empty default and the local test server's dummy values |
| 2026-11-10 | CodeQL, security-extended (first run, on PR 60) | No alert on the code that PR changed | Its first full scan of `main` runs when PR 60 merges; alerts then show in the repository's Security tab and are triaged here |
| 2026-11-11 | An attacker's pass on the daily check and its mail, before they were merged (fresh agent, attacker's brief, eight findings each proven by a running exploit, all reproduced by the author) | **High:** a run the database could not store was run, charged and mailed every hour; one account's failing merchants took every slot of every round; a merchant's name wrote lines, a link and a would-be header into the account's mail. **Medium:** a tenant nested nine levels down was checked past its account's plan and mailed about, though the account could not open it (the plan gap is older than the daily check); an account could be mailed every hour, and the same findings again after ten failed runs; a read-only key kept a merchant's findings out of the mail; an order's currency wrote a line into the mail; a merchant's own rows make its check take many seconds of the one worker (older than the daily check) | Seven closed before merge by reworking the scheduler around its own record (C46 to C49, T25 to T28), each exploit turned around in `tests/test_daily_check_attacks.py`. The eighth is open as G19, ranked in known-gaps.md, with a round time budget so it cannot hold a round up; its fix is the next pull request. Held, with tries listed: forging or guessing an unsubscribe token, using one on another account, turning anything on with one, the notify settings across accounts or with a read key, row-level security on the new table, mail to an address the caller chose, an order, customer or secret in the mail |
| 2026-11-11 | A second attacker's pass, on the reworked daily check (fresh agent, the same rules) | Two low findings with exploits: a bare address in a merchant's name ("paypal.com/refund") reached the account's mail as a line most mail clients make a link; zero-width and other invisible characters passed the name rule. It also tried pushing the stored run a waiting finding pointed to out of history, and set it aside as the known one-day delay. The author ran that attack to the end: on an account mailed at an earlier hour each day, fifty runs a day with a read-only key kept the finding out of the mail for as long as they were repeated (medium) | All three closed before merge: what waits for the mail is kept in the scheduler's own record, not as a pointer to a stored run; a mail shows a name with no joined-up address; a name with an invisible character is refused (C47, C48, T26, T27). Each exploit is turned around in `tests/test_daily_check_attacks.py`. Held, with tries listed: writing the scheduler's record or the time of the last mail from any key or route, row-level security on both new tables from another tenant's scope, header and recipient injection, the currency and the figures in the mail, fingerprints of fresh rows against stored ones, another way to create or rename a tenant, making a whole round fail, the plan gate, rows left after a tenant is deleted, the unsubscribe route |

## What happens to a finding

- It is reproduced first: a test that passes when the attack works, run
  against the code as it is.
- A reproduced finding is fixed in its own pull request, and its test is
  turned around to assert the attack fails, so it stays fixed. A finding
  against a change that is not merged yet is fixed in that change, before
  it merges.
- A finding that does not reproduce is answered with the test that was
  tried. It is never dismissed on the author's word.
- Each pass is recorded in OKWAN_PROJECT.md §9: the platform, the date,
  what it found and what became of each finding.
