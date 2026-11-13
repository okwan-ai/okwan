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
| Okwan's own billing (who may pay or manage it, the signed webhook that sets the plan, the record of what a customer was shown) | `apps/api/okwan_api/billing.py`, `apps/api/okwan_api/policy.py` |
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
| 2026-11-11 | An attacker's pass on the daily check and its mail, before they were merged (fresh agent, attacker's brief, eight findings each proven by a running exploit, all reproduced by the author) | **High:** a run the database could not store was run, charged and mailed every hour; one account's failing merchants took every slot of every round; a merchant's name wrote lines, a link and a would-be header into the account's mail. **Medium:** a tenant nested nine levels down was checked past its account's plan and mailed about, though the account could not open it (the plan gap is older than the daily check); an account could be mailed every hour, and the same findings again after ten failed runs; a read-only key kept a merchant's findings out of the mail; an order's currency wrote a line into the mail; a merchant's own rows make its check take many seconds of the one worker (older than the daily check) | Seven closed before merge by reworking the scheduler around its own record (C46 to C49, T25 to T28), each exploit turned around in `tests/test_daily_check_attacks.py`. The eighth was closed in the pull request after it (C50, T29): a round time budget kept it from holding a round up meanwhile. Held, with tries listed: forging or guessing an unsubscribe token, using one on another account, turning anything on with one, the notify settings across accounts or with a read key, row-level security on the new table, mail to an address the caller chose, an order, customer or secret in the mail |
| 2026-11-11 | A second attacker's pass, on the reworked daily check (fresh agent, the same rules) | Two low findings with exploits: a bare address in a merchant's name ("paypal.com/refund") reached the account's mail as a line most mail clients make a link; zero-width and other invisible characters passed the name rule. It also tried pushing the stored run a waiting finding pointed to out of history, and set it aside as the known one-day delay. The author ran that attack to the end: on an account mailed at an earlier hour each day, fifty runs a day with a read-only key kept the finding out of the mail for as long as they were repeated (medium) | All three closed before merge: what waits for the mail is kept in the scheduler's own record, not as a pointer to a stored run; a mail shows a name with no joined-up address; a name with an invisible character is refused (C47, C48, T26, T27). Each exploit is turned around in `tests/test_daily_check_attacks.py`. Held, with tries listed: writing the scheduler's record or the time of the last mail from any key or route, row-level security on both new tables from another tenant's scope, header and recipient injection, the currency and the figures in the mail, fingerprints of fresh rows against stored ones, another way to create or rename a tenant, making a whole round fail, the plan gate, rows left after a tenant is deleted, the unsubscribe route |
| 2026-11-11 | The author, fixing the first pass's eighth finding (not an independent review: recorded because it found more of the same) | The matcher's cost on rows a merchant shapes was wider than the exploit showed. Beside the fuzzy rule (two thousand rows a side, fourteen seconds): the group-sum rule offered every deposit every batch (the same fourteen seconds); its search for a sum walked every combination of records that prune nothing (a third of a second a deposit for twenty-four batches of zero, so most of half an hour for five thousand); the period rule looked at every record for every period; and a lag was counted a day at a time between two records' own dates (3.6 million steps for one pair at the calendar's ends) | All closed together (C50, T29, engine E7): the other side is filed by currency, amount and date, the comparing is counted and bounded, and matching runs off the event loop. Each case is a test in `tests/test_engine_bounds.py`, with the plain rules kept there as the definition the filed ones must agree with on random rows. The worst of them now takes about a second |
| 2026-11-11 | An attacker's pass on the matcher's bound, before it was merged (fresh agent, attacker's brief, every finding proven by a running exploit and reproduced by the author) | **High:** the bound did not cover everything it claimed to. Five thousand payments with no card against five thousand settlement lines each with a different one made a place to look for every card, for every payment: twenty-five million, none counted, nineteen seconds. The group-reference rule was not bounded at all: ten thousand records under one payout id, asked for ten thousand times, held a hundred million references, most of a gigabyte. **Medium, and the bound's own fault:** when a rule stopped, the record it had not reached was undecided, but the other side's record it would have paired with was returned as unclaimed and reported unmatched ("a batch never paid"), which nothing had established. **Medium:** a match cut off by a timeout runs on in its thread | The first three closed before merge (C50): a record looks under its own tag or among all the records of its amount, never in a place per tag; a reference's records are claimed whole and looked at once, and listed fifty at a time; a stopped rule reports the other side's records its unreached ones might have paired with as undecided too. Each exploit is turned around in `tests/test_engine_bounds.py`. The fourth stands as it must (a thread cannot be stopped) and is bounded by the rest: a match now ends in a second or two whatever its rows (T29). Held, with tries listed: two hundred rounds of hostile values against the plain rules for seven reconciliations without one difference (negatives, booleans, 2^63, NaN, empty currencies, equal timestamps, daylight-saving edges, undated rows, the same record on both sides); the lag's days and its capped count; hiding a double collection behind fifty-one decoys (the old rules already called that ambiguous); the exact-reference rule; hostile date strings |
| 2026-11-13 | An attacker's pass on the refund policy and the record of what a checkout showed, before they were merged (fresh agent, attacker's brief, four findings each proven by a running exploit, all reproduced by the author) | **High:** an API key with a made-up session header beside it opened a checkout and wrote the record of what "the customer saw", and opened a paying account's Stripe portal (the portal part older than this change); the record was one per account and every opened checkout overwrote it, so a customer about to dispute a payment could replace its record with one click. **Medium:** the tests that the page and the policy say the same thing read source text and only one way: the page could promise a refund the policy did not, hide a word, or show another date, and pass. **Low, conditional:** a payment could name its account through `client_reference_id`, which a buyer writes on a Stripe payment link | All closed before merge. Billing takes the account from the session and refuses a request that carries a key (C52); each Checkout session has its own row, written once and marked paid once (C51); only Okwan's own metadata names a payment's account; the page renders a file the policy module writes. The exploits became tests in `tests/test_refund_policy.py`. Seventeen other things it tried held |
| 2026-11-13 | A second attacker's pass, on the reworked code (fresh agent, the same rules, every finding reproduced by the author) | **High:** Stripe reports a new subscription as `incomplete` and does not order its events; when the subscription was heard of before the paid session, the guard meant for a late retry took the session's event, so the account paid and had Pro while the record of the session that took the money stayed blank, and the account's next click a week later deleted it. **Medium:** the daily cap on checkouts counted rows, then asked Stripe, then wrote the row, so two hundred requests at once all passed it; a call Stripe refused was never counted; the portal had no limit; and a few such bursts left more unpaid rows than the store's one listing returned, all newer than the paid row, which then was not in it. **Medium:** only the page's words were pinned to the version: the words beside the pay button, beside the tick box, sent to a bank and said of how the policy was shown could change with the version unmoved, and an archived version was whatever was typed into the archive. **Medium:** the check of the built page read its HTML, not what a browser shows: a word in an element browsers never render, a stylesheet hiding the rule and writing another, and a promise outside `<main>` all passed; and the statement sent to a bank said the plan page shows how to cancel, which it did not, with nothing holding that page to the statement. **Medium, older than this change:** two checkout pages open at once could both be paid: two subscriptions and two Stripe customers on one account, the record keeping whichever was heard of last, and the other out of reach of Manage billing. **Low, older:** a signature header with a character outside ASCII drew a 500 from the webhook, unsigned; and four instances starting at once could fail to start ("tuple concurrently updated") | All closed before merge. The record is marked before the guard (C51); an hourly limit per account is taken before Stripe is asked, for checkout and portal alike (C52); paid rows are read apart from unpaid ones, so no number of the second kind hides the first; everything a version says is pinned, and an archived version is checked against its own digest; the built page is opened in a browser in CI and what it shows is compared, with generated content, unreadable text and the page's frame; the plan page shows the policy's own words from the API and the layout check reads them in a browser; opening or paying a checkout ends the account's others at Stripe, and a second subscription never takes over the record; the signature is compared as bytes; a schema run aborted by another beside it is tried again. Each exploit became a test. Its report also listed things it had seen without an exploit; those that were cheap to close were closed and tested all the same: a signed event with a time or a consent of the wrong shape failing half-way; a payment that settles later never marking its session; an old session's event, sent again after the account had moved on, bringing Pro back; an unpaid row never forgotten if its account never returned; a session forgotten on any 200 from Stripe; Stripe's ids in a log line; the page checked at one width only. Left as it is, and written down: a check on a page is a guard against drift and mistakes, not against the repository's own authors (T31); a checkout made free by a promotion code is marked as completed like any other; and a start still takes a brief lock for each index it finds already there, which the schema's own comment understates |

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
