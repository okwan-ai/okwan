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
code on `main` registers.

The monitor counts the `import okwan_<name>.connector` lines in
`apps/api/okwan_api/main.py`, which is how the API registers a connector,
so adding or removing one needs no monitor change. For 20 minutes after a
commit to `main`, a different count reads as that commit still deploying
and passes. Past that, the running build is not `main`: check the Render
deploy log for a failed or stuck deploy and redeploy, or, if the build
itself fails to register a connector, [roll back a deploy](#roll-back-a-deploy).
`OKWAN_MONITOR_CONNECTORS` in the monitor workflow pins a number when one
must be forced.

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

### File imports *(Tier 1)*

**Imports are open in production** since 7 November 2026: the owner set
`OKWAN_IMPORTS_OPEN=true` on the Render `okwan` service once the privacy
policy described imported files (importer step 6). **An upload answering
503** "file imports are not open yet" therefore means the variable is gone
or not `true` on that service (a new service, a changed environment group):
put it back. Listing and deleting files work either way.

**Sample files.** `docs/samples/imports/` holds a week of a café's
processor batches, card transactions and bank statement (CSV and OFX), the
mapping each needs and what the checks find in them. Use them to demo the
importer, to reproduce a customer's question without their file, or to show
a dealer the export shape that works.

**A customer says a file was refused.** The refusal says why, and it is
one of:

- *422, a card number*: some cell holds 12 to 19 digits that a card network
  issues and that pass the Luhn check. The message names the row and the
  column, never the number, and nothing from the file was kept. The
  customer removes the column or masks it to the last four digits. Do not
  ask them to send the file to us.
- *400, the file*: not CSV, XLSX or OFX (an older `.xls` must be saved as
  `.xlsx`), larger than 8 MB or 50,000 rows, or two columns with one name.
- *400, the mapping*: `detail.problems` lists every field that does not fit
  and, for rows that cannot be read, up to ten rows by number. Setting
  `skip_invalid_rows` skips them instead and lists them on the file's record.
- *409*: the same file is already imported for that kind; `existing_id`
  names it. Delete it first to import it again.

**In the dashboard.** A merchant's *Files* tab lists its imported files
and saved mappings, each deletable; *Import a file* reads the file in the
browser, shows its columns and first rows, starts the mapping from the
column names (or a saved mapping), and imports it. A 503 there reads
"File imports aren't open for this workspace yet".

**Reading them back.** Imported rows are the `files` connector:
`POST /v1/files/{settlement_batches|settlement_transactions|bank_deposits|delivery_lines|delivery_payouts}/list`,
the SQL tables `files.*`, and the reconciliations that read them:
`batches_deposits` (batches against deposits), `square_payouts_deposits`,
`clover_payments_settlements`, `delivery_payout_lines`,
`delivery_payouts_deposits` and `avatax_collected_filed` (REST,
`recon.<name>`, `okwan_reconcile`).

**Delivery platforms.** DoorDash, Uber Eats and Grubhub reports import as
*Delivery orders* (the transaction or payment-details report) and
*Delivery payouts* (the payout report); `docs/samples/imports/delivery/`
has a mapping for DoorDash and Uber Eats. A customer asking about one:

- *A payout "is not its lines"*: the payout differs from the sum of the
  lines carrying its payout id. An instant (Fast Pay) fee explains its own
  gap when the payout report's fee column is mapped. Otherwise the lines
  imported may not be the whole week (a transactions report cut at a date),
  or the platform withheld something its report does not itemise: the
  customer asks the platform, quoting the payout id.
- *A payout "never reached the bank"*: no deposit naming DoorDash, Uber or
  Grubhub (or Seamless) with its amount, on its payout date or up to three
  business days after. Check that the statement covers those days and that
  the bank's text names the platform. A deposit from a platform with no
  payout means a payout report not imported yet.
- *Commission or error charges look high*: `files.delivery_lines` keeps every
  deduction apart, with `commission_rate_bps` and `withheld_minor` on each
  line. The query in the samples README lists orders above a plan's rate.
  The contracted rate itself is not stored yet (§10 item 35).

**Sales tax.** A merchant imports its returns as *Tax returns* (one row per
jurisdiction per period; `docs/samples/imports/tax/` has a mapping) and
connects Avalara AvaTax. `avatax_collected_filed` compares each return with
the tax AvaTax committed for that state over the return's days. A customer
asking about one:

- *A return "does not match"*: the return reported more or less tax than
  AvaTax committed for its state and days. A correction to an earlier
  period, when the return's adjustment column is mapped, explains its own
  gap. Otherwise the customer's filing service or accountant compares the
  return with AvaTax's liability report for the period; Okwan reports the
  difference and never says what is owed.
- *Sales "in no return's period"*: a month between two imported returns had
  none imported, or a state has committed sales and no return at all. Sales
  after the latest return are waiting, not findings.
- *A return "with nothing behind it"*: it reported tax and AvaTax committed
  none for that state and period, often sales taxed outside AvaTax. A listing
(`GET /v1/reconciliations`, `okwan_list_reconciliations`) says, per source,
how many files cover which days, so "not runnable" with files of one kind
only means the other kind has not been uploaded yet.

**Retention.** Each file and its rows expire 13 months after upload. Reads
skip an expired file at once; the API deletes every tenant's expired
files hourly (a warning "import sweep failed" in the logs means it will
retry next hour). To delete them now:

```bash
python -m okwan_vault imports sweep
```

### A customer's connection test fails *(Tier 1)*

The dashboard's *Test* on a connection, or a 401 or 403 from a connector
route whose message comes from the rail rather than Okwan, means the
customer's own credential at that rail is wrong, expired or missing a
permission. Okwan cannot fix it. Ask the customer to create a new
credential at the rail (Stripe restricted key, Shopify token with
`read_orders` and `read_all_orders`, PayPal client id and secret, Square
access token with `PAYMENTS_READ`, `PAYOUTS_READ`, `ORDERS_READ` and
`MERCHANT_PROFILE_READ`, Clover merchant API token with read permission
on Merchant, Orders and Payments, Avalara account ID and license key) and
save it in the dashboard under
*Settings*. Never ask them to send it to you. A Square 401 with a token
that looks right is most often `environment` set to the other one: a
production token needs `production`, a sandbox token `sandbox`. The same
holds for Clover, plus two Clover-only causes: a `merchant_id` that is not
the merchant the token was made for (Clover answers both as a bare 401),
and an *emulator* token from the developer dashboard's Test Merchants key
icon, which is for Clover's Android emulator and never works on the REST
API. The right token comes from the merchant dashboard: *Settings*, *View
all settings*, *API tokens*, *Create new token*. Avalara takes the account
ID and a license key (*Settings*, *License and API keys* in AvaTax, or
*Integrations*, *License key and client secrets* on app.avalara.com), the
company code and `production` or `sandbox` (a free trial is
`production`). A new license key takes a few minutes to start working, so
a 401 right after creating one clears itself; a trial account allows about
six requests a minute, and Okwan waits out its 429s.

### A rail is down *(Tier 1)*

Errors from one connector only, with the rail's own status (5xx, or
"upstream"). Check the rail's status page. Nothing to fix on our side;
tell the customer and close when the rail recovers.

### Rate limited *(Tier 1)*

429 with `Retry-After`. Sign-in allows 10 tries per address and 20 per IP
every 15 minutes; signup and the connection test have their own limits.
Wait out `Retry-After`. Limits reset when the API restarts, but do not
restart for this.

### An access alert

The API mails the account, with a copy to `OKWAN_SUPPORT_ESCALATION_TO`,
when an API key reads from a network it has not used in 30 days (once the
key has a day of history), or reads at least 100 times in an hour and more
than three times its busiest hour of the week before. Render's log has one
line per alert, `access alert <kind> on <tenant> (<alert id>)`, never the
address. SEV3 while the customer recognises it; SEV2 once they do not,
and then "A key or password leaked" below.

1. What the key read: the customer opens *Settings → Access log* in the
   dashboard (90 days, by merchant), or reads
   `GET /v1/tenants/<tenant>/access?days=90` with their session or a key. Every read is there (time, key prefix,
   address, network, surface, route) with the alerts. The operator copy of
   the mail has the address, network and route of the read that alerted.
2. The customer does not recognise the network: they revoke the key
   (dashboard *Settings*, keys; or `python -m okwan_vault key revoke <key_id>`)
   and issue a new one. The rows from that network are the scope of what
   was read.
3. Expected alerts: a deployment moving to a new host or provider alerts
   once for its new network. A volume alert repeats at most once a day per
   key. Dashboard sessions never alert.
4. Rows are kept 90 days and alerts 400; save what an incident needs
   before then. The address is Cloudflare's header, which a request sent
   straight to `okwan.onrender.com` can set itself
   ([known-gaps.md](../audit/known-gaps.md) G17).

### Gusto (Connect Gusto)

Gusto has no key to type. A business's Gusto admin presses *Connect Gusto*
on a merchant's Connections (or the workspace's own), approves read-only
access in Gusto, and lands on *Gusto is connected* with the test read.

- **Setup:** Okwan's Gusto application lives in the Gusto Developer
  Portal (organization Global Tech Startup LLC). On the `okwan` Render
  service: `OKWAN_GUSTO_CLIENT_ID`, `OKWAN_GUSTO_CLIENT_SECRET`, and
  `OKWAN_GUSTO_ENV` (`demo` until Gusto issues production credentials).
  The application's redirect URI must be
  `https://app.okwan.ai/integrations/gusto/callback`.
- **"Gusto isn't set up on this Okwan server yet":** one of the two
  secrets is missing on the service. The API log names which one, never
  its value: `Connect Gusto refused: OKWAN_GUSTO_CLIENT_ID not set on this
  server`. A name spelt any other way counts as missing (on 2026-11-09 the
  id was saved as `KWAN_GUSTO_CLIENT_ID`).
- **"Gusto refused the token request … connect Gusto again":** the code
  expired (ten minutes), the business revoked access, or a refresh token
  was spent. The admin connects again; nothing else to repair.
- **"wasn't started in this browser":** the approval finished in a
  different browser, or the API restarted in between (connections in
  progress live in memory). Connect again.
- Tokens are renewed every two hours under a lock in the process; a second
  API instance needs that lock in the database first
  ([known-gaps.md](../audit/known-gaps.md) G7).

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
- **Check health.** *Health* shows, in one place: failed checks across
  every account in the last 7 days with their errors, escalations whose
  email to you did not go out, accounts at 80% or more of their monthly
  limit, which switches are on (mail, escalation inbox, assistant,
  sign-up, MCP host allowlist, dashboard client addresses; never their
  values), the deployed commit, the vault's round trip, and recent
  console activity. The outside monitor lives on GitHub; Health links to
  its runs and open incidents.
- **Wrong code, or "too many attempts".** Codes change every 30 seconds
  and each works once; check the phone's clock is set automatically.
  Five unlock attempts in 15 minutes is the limit; after that, wait.
- **Lost the phone.** Run `operator enroll` again: the new key replaces
  the old one at once.
- **Who did what.** `python -m okwan_vault operator log` prints every
  console action, newest first. The database refuses to change or delete
  it.

### Billing (Pro by card)

Pro is $99 a month, paid by card through Stripe Checkout in Okwan's own
Stripe account. The plan follows Stripe's signed webhook: Pro while the
subscription is active, trialing or retrying a failed payment; Free once
it is cancelled at period end, unpaid or expired.

- **Turn it on** (once): see "Owner decisions" for the Stripe setup. Until
  all three `OKWAN_BILLING_*` variables are set on the `okwan` service,
  the Plan tab offers "Talk to us about Pro" instead of a button, and
  Health shows *Card payments (Stripe): off*.
- **A customer paid but is still on Free.** The webhook did not arrive or
  failed its signature. In Stripe: Developers → Webhooks → the endpoint →
  the event, and *Resend*. A signature failure means
  `OKWAN_BILLING_STRIPE_WEBHOOK_SECRET` does not match the endpoint's
  signing secret. As a stopgap, `python -m okwan_vault plan set <tenant_id> pro`.
- **Refunds and invoices** are done in the Stripe dashboard. Refunding
  does not cancel: cancel the subscription there too, and the webhook
  returns the account to Free.
- **Larger plans** (Team, Enterprise) stay agreed in writing and set with
  `plan set`; they do not go through Checkout.

### Delete an account on request

The Privacy Policy (`okwan.ai/privacy`) promises deletion within 30 days
of a request from the account's sign-in address. Requests arrive by email
to info@globaltechstartup.com or as an `account` ticket from the
assistant.

1. Confirm the request came from the account's sign-in address: the
   console's account page shows it.
2. In the Codespace:

   ```bash
   python -m okwan_vault tenant delete <tenant_id>
   ```

   It shows the account, its login and its merchants, then asks for the
   id again. Everything in the account goes in one transaction: merchants,
   login, sessions, saved credentials, keys, check results, usage, support
   conversations, tickets and replies, and any unfinished signup for that
   address. The console's log records that the deletion happened.
3. Reply to the customer that it is done, and resolve the ticket.

Database backups keep deleted rows until they age out on Neon's
schedule; the policy says so.

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
lint on changed Python, both web builds and the dashboard's layout check
(below), on every pull request. Since
2026-11-04 the ruleset `main` (GitHub → *Settings → Rules → Rulesets*)
makes `main` require it:

| Rule | Setting | Why |
|---|---|---|
| Pull request before merging | required, 0 approvals | One maintainer, and GitHub does not let anyone approve their own pull request |
| Status checks | `python`, `web (web)`, `web (dashboard)`; not required to be up to date | The suite against a real database, both builds, and the layout check inside `web (dashboard)` |
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

### Layout check

Part of `web (dashboard)`, so required. The owner's rule (§9 2026-11-06):
nothing on a page may overlap. The job builds the dashboard, starts the
API with an in-memory workspace (`scripts/ui_server.py`: *Acme ISV* with
*Kofi's Store*, every rail connected, one real check over fixture rows,
and two imported files with their mappings saved, and *Accra Coffee Co.*
with only Stripe), and runs `apps/dashboard/scripts/layout-check.mjs`. It
opens the sign-in pages and every signed-in page, a merchant's three tabs,
its import page (also with a file chosen, so the column-matching form is
checked) and a connector included, at 1670, 1366, 1024 and 390px, and
fails if any visible text overlaps other text, a page scrolls sideways, or
anything covers an item of the open account menu.

**When it fails:** the job's summary has a `layout-check` artifact with a
full-page screenshot of each failing page, named by width and path, and
the API and dashboard logs. Fix the page; never loosen the check to pass.
Hidden text (screen-reader only, a closed *How this works*, clamped lines)
is not counted, so a hit is something a person sees.

**Locally:**
```bash
UI_CHECK_PASSWORD=$(openssl rand -hex 16) python scripts/ui_server.py
```
then, in `apps/dashboard` with that same password exported:
```bash
npm run build && OKWAN_API_URL=http://127.0.0.1:8000 npm start
```
```bash
node scripts/layout-check.mjs
```

## Owner decisions

Open, and not this runbook's to make:

- A second on-call engineer, and their access. Tier 1 is the support
  assistant; everything after it is one person today.
- A support address customers write to (for example `support@okwan.ai`,
  which needs a mailbox: Resend only sends).
- The response targets above, before any contract states them.

### Switching on card payments (once)

In Okwan's own Stripe account (Global Tech Startup LLC), live mode:

1. **Product:** Product catalogue → Add product "Okwan Pro", recurring,
   $99.00 USD monthly. Copy its **price id** (`price_...`).
2. **Customer portal:** Settings → Billing → Customer portal: allow
   cancelling (at the end of the billing period) and updating the payment
   method. Save.
3. **Webhook:** Developers → Webhooks → Add endpoint
   `https://api.okwan.ai/v1/billing/webhook`, with events
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`.
   Copy its **signing secret** (`whsec_...`).
4. **Key:** Developers → API keys → Create restricted key, with write on
   *Checkout Sessions* and *Customer portal*, nothing else. Copy it
   (`rk_live_...`). Shown once.
5. On the Render `okwan` service, set `OKWAN_BILLING_PRO_PRICE_ID`,
   `OKWAN_BILLING_STRIPE_WEBHOOK_SECRET` and
   `OKWAN_BILLING_STRIPE_SECRET_KEY` to those three values, and redeploy.
   Health then shows *Card payments (Stripe): on*.

Keys go only into Render, never into chat, code or a screenshot.
