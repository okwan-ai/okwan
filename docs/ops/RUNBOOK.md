# Okwan runbook

For whoever is on call. It assumes no knowledge of the code. Each failure
the monitor detects has a section here with the same name as the link in
the incident issue. Each section says what the failure looks like, what
to check, and what to do, in that order.

**Ground rules, every time:**

- Okwan is read-only towards every payment rail. Nothing in this runbook
  moves money, and no fix ever needs to.
- Never paste a key, password, master key or connection string into an
  issue, a chat, a ticket or a screenshot. If one was pasted, treat it as
  leaked ([A key or password leaked](#a-key-or-password-leaked)).
- Never read a customer's stored credentials to debug. Nothing here
  needs them.
- Write what you did in the incident issue as you go. The next person
  starts from your notes.

## Who answers

| Tier | Who | Does | Needs access to |
|---|---|---|---|
| 1. First response | **Okwan Support**, the assistant in the dashboard ([Support assistant](#support-assistant)) | Answers in real time from the docs and a read-only look at the customer's own account: connections, tests, runs, keys, plan. Escalates by email anything it cannot settle, and anything about security or money at once | The customer's own account, read-only, through the API's guard |
| 2. On call | Felix Marwuko (`@Marwuko`) until a second engineer is named | Every section here: restarts, rollbacks, operator commands | Render (deploy), Neon (console), Vercel, the Okwan Codespace |
| 3. Owner | Felix Marwuko | Code changes, security incidents, master key, anything this runbook does not cover | Everything |

The monitor assigns each incident issue to the GitHub user in the
repository variable `OKWAN_ONCALL` (default `Marwuko`). Change that
variable when the rota changes; nothing else has to change. Support
escalations go to `OKWAN_SUPPORT_ESCALATION_TO` on the Render `okwan`
service, and every one is listed by `python -m okwan_vault support list`
whether or not the mail went out. Read that list daily: a line marked
`NOT MAILED` is a customer still waiting.

**Severity and proposed response targets.** These are proposals. They
are not in any contract until the owner signs one that states them.

| Severity | Means | Acknowledge | Update the customer |
|---|---|---|---|
| SEV1 | Suspected cross-tenant exposure, a leaked secret, or the API down for everyone | 1 hour, any day | Every 2 hours until resolved |
| SEV2 | One surface down or degraded for everyone (signup, MCP, dashboard, one rail) | 4 business hours | Daily |
| SEV3 | One customer affected (their credentials, their plan, their key) | 1 business day | When resolved |

## Where things run

| Part | Where | Health |
|---|---|---|
| API and hosted MCP (`api.okwan.ai`, `/mcp/`) | Render service `okwan` (Docker, Oregon, one instance) | `https://api.okwan.ai/healthz` |
| Dashboard (`app.okwan.ai`) | Render service `okwan-dashboard` (Node) | `https://app.okwan.ai/` |
| Site (`okwan.ai`) | Vercel project `okwan`, static | `https://okwan.ai/` |
| Vault database | Neon Postgres | Neon console; the API's `/healthz` says `"vault": "postgres"` |
| Verification mail | Resend | Resend dashboard, *Emails* |
| DNS for okwan.ai | Vercel (domain bought there) | Vercel, *Domains* |

Status pages for the dependencies: Render, Neon, Vercel, Resend, and the
rails (Stripe, Shopify, PayPal, Paystack).

Operator commands (`python -m okwan_vault …`) run in the Okwan Codespace,
where `OKWAN_VAULT_DATABASE_URL` and `OKWAN_VAULT_MASTER_KEY` are already
set as Codespaces secrets. Run `python -m okwan_vault` with no arguments
to list them.

## Failures the monitor detects

### API down

*Looks like:* `/healthz` unreachable or not 200. Customers see timeouts or
errors from every route.

1. Render → `okwan` → *Events*. Is a deploy failing or the service
   restarting in a loop? Open *Logs* for the last start.
2. If the last deploy started it: [Roll back a deploy](#roll-back-a-deploy).
3. If the logs show the store refusing to start, go to
   [Startup refused](#startup-refused).
4. If the logs show the master key missing ("OKWAN_VAULT_MASTER_KEY"), the
   environment lost it: Render → `okwan` → *Environment*. Do **not**
   generate a new one: a new key cannot open anything stored. Restore the
   original from where the owner keeps it, or escalate to Tier 3.
5. If Render itself is down (status page), there is nothing to fix on our
   side: post in the incident issue and update customers.

### Vault unreachable

*Looks like:* `/healthz` answers 503, "vault unreachable".

1. Neon console: is the project's compute running? A suspended compute
   wakes on the next query; give it a minute and rerun the monitor
   (*Actions → monitor → Run workflow*).
2. Neon status page. If Neon is down, wait and update customers.
3. If the connection string changed (password reset in Neon, branch
   renamed), update `OKWAN_VAULT_DATABASE_URL` on the Render `okwan`
   service and redeploy. The value is never pasted anywhere else.

### In-memory vault

*Looks like:* `/healthz` answers 200 but `"vault": "memory"`.

The API is running without its database, on an empty in-memory store.
Every customer's key is refused and nothing is saved. Render → `okwan` →
*Environment*: `OKWAN_VAULT_DATABASE_URL` is missing. Restore it and
redeploy. SEV1 while it lasts.

### Wrong build

*Looks like:* `/healthz` reports a different number of connectors than the
monitor expects.

Either a deploy shipped a build that failed to register a connector, or
a connector was added or removed on purpose. In the second case, update
`OKWAN_MONITOR_CONNECTORS` in the monitor workflow. In the first case,
[roll back a deploy](#roll-back-a-deploy).

### Slow

*Looks like:* `/healthz` took more than 5 seconds.

Usually a cold database (see [Vault unreachable](#vault-unreachable),
step 1) or a Render instance under load. If it is still slow after three
runs, check Render → `okwan` → *Metrics* for CPU and memory. One worker
serves every request; a sustained spike there is a Tier 3 question.

### Dashboard down

*Looks like:* `app.okwan.ai` unreachable or answering 4xx/5xx.

1. Render → `okwan-dashboard` → *Events* and *Logs*.
2. If the API is also down, fix that first: the dashboard depends on it.
3. If only the dashboard's last deploy broke it,
   [roll it back](#roll-back-a-deploy).
4. `OKWAN_API_URL` on the dashboard must be `https://api.okwan.ai`, and
   `OKWAN_DASHBOARD_SECRET` must match the API's (Render generates it
   once and shares it; it is never typed).

### Site down

*Looks like:* `okwan.ai` unreachable or answering 4xx/5xx.

Vercel → project `okwan` → *Deployments*. Promote the last good
deployment (*⋯ → Promote*). If the domain shows a configuration error,
Vercel → *Domains*. The site holds no customer data; this is SEV2 at most.

### MCP

*Looks like:* the MCP handshake at `api.okwan.ai/mcp/` fails while
`/healthz` is fine.

The API is up but the hosted MCP's session manager is not answering.
Render → `okwan` → *Logs*, search for `mcp`. Restarting the service from
its Render page usually clears a stuck session manager; if it fails again
after a restart, roll back the last deploy.

### MCP host

*Looks like:* the handshake answers 421, invalid host.

DNS-rebinding protection refused the host name. `OKWAN_ALLOWED_HOSTS` on
the Render `okwan` service must list every host the API answers on:
`api.okwan.ai,okwan.onrender.com`. If clients broke right after it was
set, remove the variable and redeploy (protection off) and tell Tier 3.

### Monitor key

*Looks like:* the authenticated check answers 401.

The monitor's own key (`OKWAN_MONITOR_KEY`, a GitHub Actions secret) was
revoked or the monitor tenant deleted. Issue a new one for the monitor
tenant (see [Monitoring](#monitoring)) and replace the secret. Customers
are not affected by this check failing alone.

### Quota

*Looks like:* 402 "monthly request limit reached". For the monitor, its
tenant is over plan; for a customer, theirs is.

`python -m okwan_vault plan show <tenant_id>` prints plan, limit and
month-to-date use. Raising a customer's plan is a commercial decision
(Tier 3): `python -m okwan_vault plan set <root_tenant_id> <plan>`. Only a
root tenant has a plan; a merchant uses its account's.

### TLS

*Looks like:* a certificate expires in 14 days or fewer, or the TLS
handshake fails.

Render (api, app) and Vercel (site) renew certificates themselves. A
renewal that is stuck almost always means the DNS record moved: Vercel →
*Domains* → `okwan.ai` records must still point `api` and `app` at
Render, and Render → service → *Settings → Custom Domains* must show the
domain verified. Re-verify there.

## Failures customers report

### Startup refused

*Looks like:* the API will not start. The log says `IsolationUnavailable`,
or "cannot create or join the okwan_tenant role".

The API refuses to serve when the database cannot keep tenants apart.
That refusal is deliberate: do not work around it.

- "role okwan_tenant does not exist" or "cannot create or join": the
  database role the API runs tenant queries as is missing. In the Neon
  SQL editor, as the project owner, run the two statements the log's
  HINT prints (`CREATE ROLE okwan_tenant NOLOGIN NOINHERIT;` and a
  `GRANT` to the API's login role), then restart the service.
- "must be NOSUPERUSER NOBYPASSRLS NOLOGIN": someone changed the role.
  Tier 3; treat as a possible security incident.
- "row-level security is off for: …": someone disabled the protection
  on a table. Tier 3, SEV1: restart once (the migration turns it back
  on); whatever the result, escalate.

### Signup answers 503

"signup is not open yet". Either is the cause, on the Render `okwan`
service:

- `OKWAN_SIGNUP_OPEN` is not `true`. That is deliberate until the privacy
  policy and terms are published; opening signup is the owner's call.
- The API has no mailer: `RESEND_API_KEY` or `OKWAN_MAIL_FROM` is missing.

If both are set and mail still does not arrive, Resend → *Domains*: the
sending domain (`mail.okwan.ai`) must be verified, with click and open
tracking off and TLS enforced.

### A customer's connection test fails *(Tier 1)*

The dashboard's *Test* on a connection, or a 401 or 403 from a connector
route whose message comes from the rail rather than Okwan, means the
customer's own credential at that rail is wrong, expired or missing a
permission. Okwan cannot fix it. Ask the customer to create a new
credential at the rail (Stripe restricted key, Shopify token with
`read_orders` and `read_all_orders`, PayPal client id and secret) and
save it in the dashboard under *Settings*. Never ask them to send it to you.

### A rail is down *(Tier 1)*

Errors from one connector only, with the rail's own status (5xx, or
"upstream"). Check the rail's status page. Nothing to fix on our side;
tell the customer and close when the rail recovers.

### Rate limited *(Tier 1)*

429 with `Retry-After`. Sign-in allows 10 tries per address and 20 per IP
every 15 minutes; signup and the connection test have their own limits.
Wait out `Retry-After`. Limits reset when the API restarts, but do not
restart for this.

### A key or password leaked

SEV1 if it is a customer's or an operator secret; SEV2 for a customer's
own API key they leaked themselves.

- A customer's Okwan API key: the customer revokes it in the dashboard
  (*Settings*, keys), or `python -m okwan_vault key revoke <key_id>`. Revocation takes
  effect on the next request. Issue a new one.
- A customer's password: `python -m okwan_vault account reset-password
  <email>` (password from a file, never typed into a chat). Every session
  of that login ends at once.
- A rail credential: the customer rolls it at the rail; then deletes the
  old one in Okwan.
- The master key or the database connection string: Tier 3 immediately.
  Do not rotate anything yourself.

### Suspected cross-tenant exposure

A customer says they saw data that is not theirs. SEV1. Tier 3 at once.

1. Do not ask the customer to send screenshots of the data; ask what they
   saw (which page, which order reference, when) and their tenant id.
2. Record the time window. Render logs are short-lived; export them now
   (*Logs → download*).
3. The owner decides on taking the API down. The suites
   `tests/test_tenant_isolation.py` and `tests/test_tenant_rls.py` and
   `scripts/plant_leaks.py` are the place to start reproducing.
4. Notifying affected customers and any regulator is a legal decision,
   not an engineering one: the owner, with counsel.

### Support assistant

The assistant answers on every signed-in dashboard page (*Ask Okwan*,
bottom right) and at `POST /v1/support/messages`.

- **It answers 503 "not switched on yet".** `ANTHROPIC_API_KEY` is not set
  on the Render `okwan` service. Set it (an Anthropic API key with a
  monthly spend limit set in the Anthropic console) and redeploy.
- **It answers 503 "unavailable" or "busy".** The key was refused (the
  log says so: replace it), Anthropic is rate limiting (wait), or
  Anthropic is down (its status page). Customers can still use
  everything else; nothing waits on the assistant.
- **Answering an escalation.** The mail's subject is the specific issue.
  It carries the assistant's brief (what it checked, the likely cause, the
  steps to resolve) and a draft reply to the customer. *Reply with this
  draft* opens your email to the account's sign-in address with the draft
  filled in: check it against the account, edit, then send. Nothing goes
  to a customer without a person sending it.
- **Escalations do not arrive.** `OKWAN_SUPPORT_ESCALATION_TO` or the
  mailer (`RESEND_API_KEY`, `OKWAN_MAIL_FROM`) is missing, or Resend
  refused the send (the log says which, under `support escalation`).
  The customer is told the same either way. Nothing is lost: the
  ticket is stored, so `python -m okwan_vault support list` shows it with
  `NOT MAILED`. Answer the customer from there.
- **A customer says the assistant gave a wrong answer.** Treat it like any
  report: the conversation is stored for 30 days. Fixing its knowledge is
  a code change (`apps/api/okwan_api/support.py`, `knowledge()` and
  `SYSTEM`), Tier 3.
- **To switch it off at once:** remove `ANTHROPIC_API_KEY` from the Render
  service and redeploy. The panel then says it is not switched on.
- **Plan usage:** a message does not count against the customer's plan,
  but a connection test the assistant runs does, as one request, exactly
  like pressing Test in the dashboard.
- **Cost:** each message is one model call plus at most six tool rounds;
  the spend limit in the Anthropic console is the ceiling. The route is
  rate limited to 40 messages an hour per account and 60 per address.

### Operator console

The console at `/ops` in the dashboard is for the people who run Okwan:
every account's support requests, the assistant's brief, the
conversation, and a reply box. Customers never see it: for anyone else
it is a 404.

- **Turn it on.** Set `OKWAN_OPERATOR_EMAILS` on the Render `okwan`
  service to your dashboard sign-in address and redeploy. Then, in the
  Codespace, run `python -m okwan_vault operator enroll <that address>`.
  It writes `~/okwan-operator-setup.txt`: open it, add the key to an
  authenticator app (Google Authenticator, 1Password, Authy), then delete
  the file. The key is never printed.
- **Open it.** Sign in to the dashboard, choose *Console* in the sidebar,
  and enter the 6-digit code from the app. It stays open for 30 minutes,
  for that browser only. *Lock now* closes it sooner.
- **Answer a ticket.** Open it from the inbox. The reply box holds the
  assistant's draft: check it against the brief, edit, then *Send reply*
  (or *Send and resolve*). It is emailed from Okwan Support to the
  account's sign-in address, and the customer's answer comes to
  `OKWAN_SUPPORT_ESCALATION_TO`. *Resolve without replying* closes a
  ticket that needs no answer.
- **Look up an account.** *Accounts* lists every customer: plan and
  this month's usage, merchants, which connections are saved, active
  keys, the last check and open tickets. An account's page shows each
  merchant's saved connections (by field name), its recent checks with
  their errors, keys by prefix, and its tickets. Nothing secret is ever
  shown: no credential value, no key. Opening an account is logged.
- **Wrong code, or "too many attempts".** Codes change every 30 seconds
  and each works once; check the phone's clock is set automatically.
  Five unlock attempts in 15 minutes is the limit; after that, wait.
- **Lost the phone.** Run `operator enroll` again: the new key replaces
  the old one at once.
- **Who did what.** `python -m okwan_vault operator log` prints every
  console action, newest first. The database refuses to change or delete
  it.

### Roll back a deploy

Render → service → *Events*: pick the last deploy that was live and
healthy → *Rollback*. Then open an issue saying which commit was rolled
back and why. Merging a fix to `main` deploys forward again.

## Monitoring

`.github/workflows/monitor.yml` runs `scripts/monitor.py` every ten
minutes and keeps one issue labelled `incident` open while anything fails.
To run it now: *Actions → monitor → Run workflow*. To run it from a
laptop: `python scripts/monitor.py`.

Set up 2026-11-04, and how to redo it:

1. The monitor tenant is `okwan-monitor` (`python -m okwan_vault tenant
   list` shows its id). It holds no rail credentials, so its key reads
   nothing of value, and the call the monitor makes is unmetered.
2. Its key is the Actions secret `OKWAN_MONITOR_KEY`: an organization
   secret (`okwan-ai` → *Settings → Secrets and variables → Actions*)
   with repository access limited to `okwan`. To replace it, issue a key
   into a file so it never shows on screen, paste it into the secret,
   delete the file, then revoke the old key:

   ```bash
   python -m okwan_vault key issue <tenant_id> | grep -oE 'okw_[A-Za-z0-9_-]{30,}' | tr -d '\n' > ~/okwan-monitor-key.txt
   ```

   ```bash
   rm ~/okwan-monitor-key.txt
   ```

3. Incidents are assigned to `Marwuko` unless the repository variable
   `OKWAN_ONCALL` names someone else. Change it when the rota changes.
4. Optional: secret `OKWAN_ALERT_WEBHOOK`, a Slack-compatible incoming
   webhook posted to when an incident opens and closes.
5. The on-call person keeps GitHub notifications on for issues assigned
   to them (email, and push in the GitHub mobile app).

Limits to know. GitHub runs schedules late under load, usually by
minutes. It also stops a scheduled workflow after 60 days without a
commit to the repository; a commit or a manual run starts it again. The
monitor runs on GitHub, so a GitHub outage silences it. For a second
opinion from outside GitHub, an uptime service pointed at
`https://api.okwan.ai/healthz` costs nothing.

## Branch protection

CI (`.github/workflows/ci.yml`) runs the tests against a real Postgres,
lint on changed Python, and both web builds, on every pull request. Since
2026-11-04 the ruleset `main` (GitHub → *Settings → Rules → Rulesets*)
makes `main` require it:

| Rule | Setting | Why |
|---|---|---|
| Pull request before merging | required, 0 approvals | One maintainer, and GitHub does not let anyone approve their own pull request |
| Status checks | `python`, `web (web)`, `web (dashboard)`; not required to be up to date | The suite against a real database and both builds |
| Force pushes and deletion | blocked | `main` is what Render deploys |
| Bypass list | empty | Nobody merges around a red check |

`plant-leaks` is deliberately not required: it runs only when isolation
code changes, so on any other pull request it never reports, and a
required check that never reports blocks the merge forever. It still
runs on those changes and weekly.

If GitHub Actions itself is down, nothing can merge until it recovers.
For a production fix that cannot wait, an owner can switch the ruleset
to *Disabled*, merge, and switch it back, saying so in the incident
issue.

## Owner decisions

Open, and not this runbook's to make:

- A second on-call engineer, and their access. Tier 1 is the support
  assistant; everything after it is one person today.
- A support address customers write to (for example `support@okwan.ai`,
  which needs a mailbox: Resend only sends).
- The response targets above, before any contract states them.
