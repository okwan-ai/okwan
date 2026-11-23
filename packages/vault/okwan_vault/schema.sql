CREATE TABLE IF NOT EXISTS tenants (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    -- An ISV's merchants are its children. Cascade is deliberate: a child
    -- exists only because the parent provisioned it.
    parent_id   text REFERENCES tenants(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so on a
-- database created before hierarchy the column arrives here instead.
-- Anything referencing it must come after this line. Checked first:
-- ALTER TABLE takes the table's strongest lock even with nothing to add,
-- and this file runs on every start.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'tenants'
          AND column_name = 'parent_id'
    ) THEN
        ALTER TABLE tenants ADD COLUMN parent_id text REFERENCES tenants(id) ON DELETE CASCADE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS tenants_parent_idx ON tenants (parent_id)
    WHERE parent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS api_keys (
    id          text PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    prefix      text NOT NULL,
    hash_hex    text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz,
    -- What the key may do (models.KEY_SCOPES): `read` reaches data routes
    -- and the hosted MCP only; `admin` also the administration routes. No
    -- default: every insert states it.
    scope       text NOT NULL CHECK (scope IN ('read', 'admin'))
);

-- Keys issued before scopes existed could administer, and keep that power:
-- the column arrives as `admin` for them, then loses its default so no
-- later insert can leave it out.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'api_keys'
          AND column_name = 'scope'
    ) THEN
        ALTER TABLE api_keys ADD COLUMN scope text NOT NULL DEFAULT 'admin'
            CHECK (scope IN ('read', 'admin'));
        ALTER TABLE api_keys ALTER COLUMN scope DROP DEFAULT;
    END IF;
END $$;

-- Lookup is by hash, never by scanning: a key check must not get slower
-- as the tenant count grows.
CREATE INDEX IF NOT EXISTS api_keys_hash_idx ON api_keys (hash_hex)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS credentials (
    tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    connector    text NOT NULL,
    field_name   text NOT NULL,
    ciphertext   bytea NOT NULL,
    wrapped_key  bytea NOT NULL,
    key_id       text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, connector, field_name)
);


CREATE TABLE IF NOT EXISTS usage (
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    hour        timestamptz NOT NULL,
    surface     text NOT NULL,
    requests    bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, hour, surface)
);

-- Billing reads a window across a subtree, so the range scan matters more
-- than the point lookup the primary key already covers.
CREATE INDEX IF NOT EXISTS usage_hour_idx ON usage (hour, tenant_id);

CREATE TABLE IF NOT EXISTS plans (
    tenant_id       text PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    name            text NOT NULL,
    monthly_requests bigint NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ── self-serve accounts ─────────────────────────────────────────────
-- A signup is a claim on an address, not an account: it owns nothing
-- until its token returns. The password hash lives here, per token, so
-- verification can require the password that created this signup and
-- not whichever one was set last.
CREATE TABLE IF NOT EXISTS signups (
    token_hash     text PRIMARY KEY,
    email          text NOT NULL,
    password_hash  text NOT NULL,
    expires_at     timestamptz NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS signups_email_idx ON signups (email);

-- One tenant per address, held by two constraints rather than a check:
-- the email is the key, and a tenant has at most one owning address.
CREATE TABLE IF NOT EXISTS accounts (
    email          text PRIMARY KEY,
    tenant_id      text NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    password_hash  text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  text PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    expires_at  timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ── reconciliation runs ─────────────────────────────────────────────
-- Every run as a tenant, on every surface (dashboard, REST, MCP), so pages
-- read results instead of running them and there is history. `rows` holds
-- only what a page renders (okwan_recon trims them: outcome, order
-- reference, currency, totals, per-rail amounts, reasons); a raw rail
-- record is never written. `error` is scrubbed of stored values and cut
-- to 300 characters. The newest 50 per (tenant, kind, name) are kept;
-- the insert prunes the rest in the same transaction. A run of a check
-- that reads imported files (`reads_files`) holds references, amounts and
-- dates taken from them, so it is deleted with any file of its tenant:
-- when one is deleted and when one expires.
CREATE TABLE IF NOT EXISTS reconciliation_runs (
    id           text PRIMARY KEY,
    tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('across', 'pair')),
    name         text NOT NULL,
    surface      text NOT NULL,
    status       text NOT NULL CHECK (status IN ('ok', 'failed')),
    started_at   timestamptz NOT NULL,
    finished_at  timestamptz NOT NULL,
    summary      jsonb,
    rows         jsonb,
    error        text,
    reads_files  boolean NOT NULL DEFAULT false
);

-- `reads_files` came after the table. A run stored before it, or by a
-- release from before it that is still running beside this one, is not
-- marked: the store deletes such a run by the name of its check, which the
-- API tells it from its registry (PostgresStore.file_checks). No list of
-- names is kept here to fall behind that registry.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'reconciliation_runs'
          AND column_name = 'reads_files'
    ) THEN
        ALTER TABLE reconciliation_runs ADD COLUMN reads_files boolean NOT NULL DEFAULT false;
    END IF;
END $$;

-- The newest run per tenant and name is what every page asks for.
CREATE INDEX IF NOT EXISTS reconciliation_runs_latest_idx
    ON reconciliation_runs (tenant_id, name, finished_at DESC);
-- And the open-items list asks for each check's newest run by when it read,
-- the newest good one apart; pruning keeps each status's newest the same way.
CREATE INDEX IF NOT EXISTS reconciliation_runs_read_idx
    ON reconciliation_runs (tenant_id, kind, name, status, started_at DESC, id DESC);

-- ── decisions on open items ─────────────────────────────────────────
-- What a person decided about one finding of a check (okwan_recon.exceptions
-- keys it): given to someone, explained, resolved, or opened again, and why.
-- Rows are only added, never changed or pruned: the history is the record an
-- accountant asks for, and at most 100 an item. `seq` numbers an item's decisions 1, 2, 3 in the
-- order they were made (the insert holds a lock on the item, and the unique
-- index refuses two with one number); `prior_status` is its
-- status before the row. `run_id` is the stored run the finding was in, with
-- no reference to it: runs are pruned and the decision stays. A decision on
-- a check that reads imported files may quote them in its note, so it goes
-- with any file of its tenant, as such a run does. `credential` is how the
-- access log names who made it: "session", or a key by its public prefix.
CREATE TABLE IF NOT EXISTS exception_decisions (
    id               text PRIMARY KEY,
    seq              integer NOT NULL CHECK (seq > 0),
    tenant_id        text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind             text NOT NULL CHECK (kind IN ('across', 'pair')),
    name             text NOT NULL,
    item_key         text NOT NULL,
    status           text NOT NULL CHECK (status IN ('open', 'assigned', 'explained', 'resolved')),
    prior_status     text CHECK (prior_status IN ('open', 'assigned', 'explained', 'resolved')),
    owner            text,
    note             text,
    actor_tenant_id  text NOT NULL,
    actor_name       text NOT NULL,
    credential       text NOT NULL,
    run_id           text NOT NULL,
    reads_files      boolean NOT NULL DEFAULT false,
    idempotency_key  text NOT NULL,
    request_hash     text NOT NULL,
    created_at       timestamptz NOT NULL,
    -- What the item put at stake when decided (its amount, currency and
    -- day): a decision closes it only while these are the same.
    terms            text NOT NULL,
    -- The decision before this one had closed the item on these terms.
    closed_before    boolean NOT NULL
);
-- One decision per Idempotency-Key in a tenant: a retried request is
-- answered with the decision it already made.
CREATE UNIQUE INDEX IF NOT EXISTS exception_decisions_once
    ON exception_decisions (tenant_id, idempotency_key);
-- An item's newest decision, and its history.
CREATE UNIQUE INDEX IF NOT EXISTS exception_decisions_item
    ON exception_decisions (tenant_id, item_key, seq DESC);
-- The decisions made against a check's newest stored run.
CREATE INDEX IF NOT EXISTS exception_decisions_run
    ON exception_decisions (tenant_id, run_id, item_key, seq);
-- How a check's items were closed, newest first: what a suggestion is shown
-- of the account's own practice.
CREATE INDEX IF NOT EXISTS exception_decisions_check
    ON exception_decisions (tenant_id, kind, name, created_at DESC);

-- What Okwan's model suggested an open item is, and the records it rests on.
-- A suggestion is not a decision and closes nothing: a person decides, and
-- their decision names the suggestion it came from (`suggestion_id` below).
-- `evidence` is [{ref, why, record}], each `record` as Okwan stored it when
-- the suggestion was made. `answer` is set once: `as_suggested` or `edited`
-- by the decision that cites it (`decision_id`), or `rejected` by a person
-- turning it down, with who and when. A suggestion on a check that reads
-- imported files quotes them, so it goes with any file of its tenant.
CREATE TABLE IF NOT EXISTS exception_suggestions (
    id                     text PRIMARY KEY,
    tenant_id              text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind                   text NOT NULL CHECK (kind IN ('across', 'pair')),
    name                   text NOT NULL,
    item_key               text NOT NULL,
    terms                  text NOT NULL,
    run_id                 text NOT NULL,
    reads_files            boolean NOT NULL,
    status                 text NOT NULL CHECK (status IN ('explained', 'none')),
    note                   text NOT NULL,
    evidence               jsonb NOT NULL,
    confidence             text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    model                  text NOT NULL,
    actor_tenant_id        text NOT NULL,
    actor_name             text NOT NULL,
    credential             text NOT NULL,
    created_at             timestamptz NOT NULL,
    answer                 text CHECK (answer IN ('as_suggested', 'edited', 'rejected')),
    answered_by_tenant_id  text,
    answered_by_name       text,
    answered_credential    text,
    answered_at            timestamptz,
    answer_note            text,
    decision_id            text
);
CREATE INDEX IF NOT EXISTS exception_suggestions_item
    ON exception_suggestions (tenant_id, item_key, created_at DESC);

-- A decision made from a suggestion names it, and says whether the person
-- kept its words (`as_suggested`) or changed them (`edited`).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'exception_decisions'
          AND column_name = 'suggestion_id'
    ) THEN
        ALTER TABLE exception_decisions
            ADD COLUMN suggestion_id text,
            ADD COLUMN from_suggestion text CHECK (from_suggestion IN ('as_suggested', 'edited'));
    END IF;
END $$;

-- ── support ─────────────────────────────────────────────────────────
-- The support assistant's conversations, kept for 30 days after their last
-- message so a customer can come back to one and an escalation can be
-- read in full. `messages` is the transcript as the model saw it: customer
-- text after secret redaction, the assistant's turns, the read-only tools'
-- results. A tenant's own data: under row-level security below.
CREATE TABLE IF NOT EXISTS support_conversations (
    id          text PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    messages    jsonb NOT NULL,
    turns       integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS support_conversations_tenant_idx
    ON support_conversations (tenant_id, updated_at DESC);

-- What the assistant handed to a person. `notified` says whether the
-- escalation mail was accepted; the operator lists every ticket with
-- `python -m okwan_vault support list` either way.
CREATE TABLE IF NOT EXISTS support_tickets (
    id               text PRIMARY KEY,
    tenant_id        text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id  text,
    severity         text NOT NULL CHECK (severity IN ('sev1', 'sev2', 'sev3')),
    category         text NOT NULL,
    summary          text NOT NULL,
    notified         boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- The assistant's brief for the engineer: findings, likely cause, steps,
-- and a draft reply to the customer. Checked first, like parent_id above:
-- ALTER TABLE would lock the table on every start.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'support_tickets'
          AND column_name = 'brief'
    ) THEN
        ALTER TABLE support_tickets ADD COLUMN brief jsonb;
    END IF;
END $$;

-- Ticket status for the operator console: open, answered, resolved.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'support_tickets'
          AND column_name = 'status'
    ) THEN
        ALTER TABLE support_tickets
            ADD COLUMN status text NOT NULL DEFAULT 'open',
            ADD COLUMN resolved_at timestamptz;
    END IF;
END $$;

-- A person's replies to a ticket, sent from the operator console.
CREATE TABLE IF NOT EXISTS support_replies (
    id          text PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    ticket_id   text NOT NULL,
    operator    text NOT NULL,
    body        text NOT NULL,
    sent        boolean NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ── operators ───────────────────────────────────────────────────────
-- The operator console sees every customer, so it takes more than a
-- password: an allowlisted login (OKWAN_OPERATOR_EMAILS), an authenticator
-- code that unlocks one session for 30 minutes, and a permanent log.
-- The authenticator secret is sealed like a rail credential.
CREATE TABLE IF NOT EXISTS operator_factors (
    email        text PRIMARY KEY,
    ciphertext   bytea NOT NULL,
    wrapped_key  bytea NOT NULL,
    key_id       text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'sessions'
          AND column_name = 'operator_until'
    ) THEN
        ALTER TABLE sessions ADD COLUMN operator_until timestamptz;
    END IF;
END $$;

-- When a session was last used: idle for SESSION_IDLE, it no longer signs in.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'sessions'
          AND column_name = 'last_seen_at'
    ) THEN
        ALTER TABLE sessions ADD COLUMN last_seen_at timestamptz NOT NULL DEFAULT now();
    END IF;
END $$;

-- Every operator action, append-only: the database refuses to change or
-- remove a row, so the log cannot be quietly edited by the application.
CREATE TABLE IF NOT EXISTS operator_actions (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    operator  text NOT NULL,
    action    text NOT NULL,
    target    text,
    detail    jsonb
);

CREATE OR REPLACE FUNCTION operator_actions_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'operator_actions is append-only';
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'operator_actions_no_change') THEN
        CREATE TRIGGER operator_actions_no_change BEFORE UPDATE OR DELETE ON operator_actions
            FOR EACH ROW EXECUTE FUNCTION operator_actions_append_only();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'operator_actions_no_truncate') THEN
        CREATE TRIGGER operator_actions_no_truncate BEFORE TRUNCATE ON operator_actions
            FOR EACH STATEMENT EXECUTE FUNCTION operator_actions_append_only();
    END IF;
END $$;

-- ── imported files ──────────────────────────────────────────────────
-- Settlement reports and bank statements a tenant uploaded (okwan_files).
-- The file itself is never written: it is read in memory, refused or
-- mapped, and only the mapped fields of each row are kept. A card number
-- never is: a file holding one is refused whole (okwan_files.guard). Each
-- file expires 13 months after upload; reads skip it from then, an upload
-- prunes the tenant's expired files, and the API's hourly sweep deletes
-- every tenant's. Deleting a file deletes its rows. Tenant data, under
-- row-level security below.
CREATE TABLE IF NOT EXISTS import_files (
    id            text PRIMARY KEY,
    tenant_id     text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind          text NOT NULL CONSTRAINT import_files_kind_check
                  CHECK (kind IN ('settlement_batches', 'settlement_transactions', 'bank_deposits',
                                  'delivery_lines', 'delivery_payouts', 'tax_filings')),
    filename      text NOT NULL,
    format        text NOT NULL CHECK (format IN ('csv', 'xlsx', 'ofx')),
    layout        text NOT NULL,
    mapping       jsonb NOT NULL,
    sha256        text NOT NULL,
    row_count     integer NOT NULL,
    skipped       integer NOT NULL DEFAULT 0,
    skipped_rows  jsonb NOT NULL DEFAULT '[]',
    card_columns  jsonb NOT NULL DEFAULT '[]',
    first_date    date,
    last_date     date,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL
);

-- A database made before delivery platforms and tax returns knows fewer
-- kinds; the check grows to all six here. Read first, like parent_id
-- above: ALTER TABLE would lock the table on every start.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'import_files'::regclass AND conname = 'import_files_kind_check'
          AND pg_get_constraintdef(oid) LIKE '%tax_filings%'
    ) THEN
        ALTER TABLE import_files DROP CONSTRAINT IF EXISTS import_files_kind_check,
            ADD CONSTRAINT import_files_kind_check
            CHECK (kind IN ('settlement_batches', 'settlement_transactions', 'bank_deposits',
                            'delivery_lines', 'delivery_payouts', 'tax_filings'));
    END IF;
END $$;

-- The same file twice for the same kind is refused, not doubled.
CREATE UNIQUE INDEX IF NOT EXISTS import_files_once_idx ON import_files (tenant_id, kind, sha256);
CREATE INDEX IF NOT EXISTS import_files_expiry_idx ON import_files (expires_at);

CREATE TABLE IF NOT EXISTS import_rows (
    file_id    text NOT NULL REFERENCES import_files(id) ON DELETE CASCADE,
    tenant_id  text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind       text NOT NULL,
    n          integer NOT NULL,
    row_date   date NOT NULL,
    data       jsonb NOT NULL,
    PRIMARY KEY (file_id, n)
);

-- Reads are one tenant's kind over a span of days, in a stable order.
CREATE INDEX IF NOT EXISTS import_rows_read_idx ON import_rows (tenant_id, kind, row_date, file_id, n);

-- A tenant's saved column mappings, by name: how one processor's report or
-- one bank's statement maps into a kind, picked again at the next upload.
-- Column names and choices only, never a value from a file. Tenant data.
CREATE TABLE IF NOT EXISTS import_layouts (
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        text NOT NULL,
    kind        text NOT NULL,
    mapping     jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- ── access log: who read a tenant's data, from where, and when ──────
-- One row per metered read (a rail or a file through REST, SQL or the
-- hosted MCP server, a reconciliation, a connection test): when, which
-- API key by its public prefix or "session" for the dashboard, the
-- caller's address and its network (/24 for IPv4, /48 for IPv6), the
-- surface, and what was read as a route or a reconciliation's name. Never
-- query text, parameters, rows or results. Kept 90 days; the API's
-- hourly sweep deletes older rows. Tenant data.
CREATE TABLE IF NOT EXISTS access_log (
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    at          timestamptz NOT NULL DEFAULT now(),
    credential  text NOT NULL,
    ip          text NOT NULL,
    network     text NOT NULL,
    surface     text NOT NULL,
    target      text NOT NULL DEFAULT ''
);

-- Reads are one tenant's newest rows; the new-network check asks whether
-- one key has used one network lately; the sweep deletes by age.
CREATE INDEX IF NOT EXISTS access_log_read_idx ON access_log (tenant_id, at);
CREATE INDEX IF NOT EXISTS access_log_network_idx ON access_log (tenant_id, credential, network, at);
CREATE INDEX IF NOT EXISTS access_log_expiry_idx ON access_log (at);

-- What the access log raised: a key used from a network it had not used
-- in 30 days, or a key reading far more in an hour than its busiest hour
-- of the week before. Mailed to the account. Kept 400 days. Tenant data.
CREATE TABLE IF NOT EXISTS access_alerts (
    id          text PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    at          timestamptz NOT NULL DEFAULT now(),
    kind        text NOT NULL CHECK (kind IN ('new_network', 'volume')),
    credential  text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}',
    mailed      boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS access_alerts_read_idx ON access_alerts (tenant_id, at);

-- An account's choices about the daily check (okwan_api.schedule): whether
-- its merchants are checked once a day, whether a new finding is mailed,
-- and the token in that mail's unsubscribe link. A row exists once a choice
-- is made or a mail is sent; no row means both are on. The token can only
-- turn the mail off, so it is kept as it is, behind row-level security.
CREATE TABLE IF NOT EXISTS notify_settings (
    tenant_id          text PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    daily_checks       boolean NOT NULL DEFAULT true,
    finding_emails     boolean NOT NULL DEFAULT true,
    unsubscribe_token  text,
    mailed_at          timestamptz,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- What the daily mail calls aged (days open) and large (whole units of the
-- item's own currency, 0 for none): the account's choice, defaults here.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'notify_settings' AND column_name = 'aging_days'
    ) THEN
        ALTER TABLE notify_settings
            ADD COLUMN aging_days integer NOT NULL DEFAULT 7 CHECK (aging_days BETWEEN 1 AND 365),
            ADD COLUMN large_amount bigint NOT NULL DEFAULT 1000
                CHECK (large_amount BETWEEN 0 AND 1000000000000);
    END IF;
END $$;

-- What the daily mail has told an account of one merchant's open items,
-- written by the scheduler and by nothing a caller can reach: `marks` is
-- "n:<item key>" for an item told as new, "a:<item key>" for one told as
-- past the account's age. An item key is a hash, never a reference. Marks
-- of items no longer open are dropped each time it is written, so an item
-- that closes and opens again is news again. Tenant data.
CREATE TABLE IF NOT EXISTS mail_told (
    tenant_id   text PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    marks       jsonb NOT NULL DEFAULT '[]',
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- What the daily check keeps about one merchant, written by the scheduler
-- and by nothing a caller can reach: when it last tried, so a merchant is
-- due once a day whatever else runs a check. Nothing here is read back from
-- stored runs, which any key can add to and so push out of history.
CREATE TABLE IF NOT EXISTS daily_checks (
    tenant_id   text PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    checked_at  timestamptz NOT NULL
);
-- Until 2026-11-23 the mail was made from the fold's findings alone, kept
-- here as fingerprints (`told`) and as figures waiting to be mailed
-- (`news`). The mail is made from the open items now (`mail_told`): what
-- was kept for the old one goes.
-- Checked first, so a start against a current database takes no lock.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema()
               AND table_name = 'daily_checks' AND column_name IN ('told', 'news')) THEN
        ALTER TABLE daily_checks DROP COLUMN IF EXISTS told, DROP COLUMN IF EXISTS news;
    END IF;
END $$;

-- ── row-level security: the database keeps tenants apart too ────────
-- Every read and write of a tenant's own data (credentials, usage, plans,
-- stored runs, support conversations and tickets, imported files, the access log, notification choices, the daily check's record) runs as okwan_tenant, inside one transaction that names
-- the tenants it may touch (PostgresStore._scoped). The policy below lets
-- that role see, and write, only rows whose tenant_id is in that scope.
-- With no scope it sees nothing and can write nothing. The WHERE
-- tenant_id in every query stays: two walls, each enough on its own.
--
-- The login role owns the tables and is not bound by the policy. It does
-- only what comes before a tenant is known (resolving a key, a session or
-- an address to a tenant), the tenant hierarchy, and migrations. The
-- identity tables (tenants, api_keys, sessions, accounts, signups) hold
-- hashes and names, not a tenant's data, and okwan_tenant cannot read them.
--
-- Creating the role needs CREATEROLE, which Neon's owner role has. Where
-- the login role lacks it, startup fails with what an admin should run:
-- the API never serves tenant data without this wall.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'okwan_tenant') THEN
        CREATE ROLE okwan_tenant NOLOGIN NOINHERIT NOBYPASSRLS;
    END IF;
    -- From PostgreSQL 16 creating a role grants the right to administer it
    -- but not to switch into it; switching is what the store does.
    IF current_setting('server_version_num')::int >= 160000 THEN
        IF NOT pg_has_role(current_user, 'okwan_tenant', 'SET') THEN
            EXECUTE format('GRANT okwan_tenant TO %I WITH SET TRUE', current_user);
        END IF;
    ELSIF NOT pg_has_role(current_user, 'okwan_tenant', 'MEMBER') THEN
        EXECUTE format('GRANT okwan_tenant TO %I', current_user);
    END IF;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE EXCEPTION 'cannot create or join the okwan_tenant role as %', current_user
        USING HINT = format('As a database admin run: CREATE ROLE okwan_tenant NOLOGIN NOINHERIT; '
                            'GRANT okwan_tenant TO %I;', current_user);
END $$;

-- ── billing ──────────────────────────────────────────────────────────
-- A paying account's link to Okwan's own Stripe account: the Stripe ids and
-- the subscription's state, never card details (those stay with Stripe).
-- An identity table: the signed webhook finds the tenant from the customer
-- before any tenant is known, as a key or session lookup does.
CREATE TABLE IF NOT EXISTS billing (
    tenant_id            text PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id          text UNIQUE,
    subscription_id      text,
    status               text NOT NULL,
    cancel_at_period_end boolean NOT NULL DEFAULT false,
    current_period_end   timestamptz,
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- Every Stripe customer that has paid for Pro, or begun to, for an Okwan
-- account: how a payment is known to be Okwan's on a Stripe account that
-- takes other products' payments too. Written when Stripe's signed event
-- names the account through the metadata Okwan put on its own checkout.
-- An account can have more than one (two pay pages opened before either
-- was paid are two customers at Stripe, and `billing` keeps only the
-- first), and the row outlives the account: Stripe may charge that
-- customer again, and that payment is still Okwan's to answer for.
CREATE TABLE IF NOT EXISTS billing_customers (
    customer_id text PRIMARY KEY,
    tenant_id   text REFERENCES tenants(id) ON DELETE SET NULL,
    linked_at   timestamptz NOT NULL DEFAULT now()
);
-- The customers linked before this table was kept.
INSERT INTO billing_customers (customer_id, tenant_id)
    SELECT customer_id, tenant_id FROM billing WHERE customer_id IS NOT NULL
    ON CONFLICT (customer_id) DO NOTHING;

-- One row for each Stripe Checkout session Okwan opened: the version of the
-- refund policy shown beside its pay button, when it was opened, and the
-- address the request came from. When Stripe reports that session paid, its
-- row is marked with when, the subscription it started, and when the terms
-- were ticked, where a tick is required. A row is written once and marked
-- paid once: no later checkout or billing event changes it, so a disputed
-- payment is answered with what was shown for that payment (okwan_api.policy).
-- An identity table, like billing.
CREATE TABLE IF NOT EXISTS billing_checkouts (
    session_id        text PRIMARY KEY,
    tenant_id         text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_version    text NOT NULL,
    opened_at         timestamptz NOT NULL,
    ip                text,
    paid_at           timestamptz,
    subscription_id   text,
    terms_accepted_at timestamptz
);
CREATE INDEX IF NOT EXISTS billing_checkouts_tenant ON billing_checkouts (tenant_id, opened_at DESC);

-- Payments for Pro, as Stripe reports each one succeeding: which customer
-- paid, how much and when. How a disputed charge finds its account, and
-- what Okwan's dispute rate is measured against. The Stripe account takes
-- payments for other products as well, and Stripe sends this endpoint all
-- of them: one whose customer is not in `billing_customers` is kept
-- unmatched for a week, in case its account is linked a moment later, and
-- then forgotten. `okwan` stays true once matched, also after the account
-- is deleted, and a deleted account's customer still makes a payment
-- Okwan's. Amounts are minor units, as Stripe sends them. Never card details.
CREATE TABLE IF NOT EXISTS billing_payments (
    charge_id    text PRIMARY KEY,
    tenant_id    text REFERENCES tenants(id) ON DELETE SET NULL,
    customer_id  text,
    okwan        boolean NOT NULL DEFAULT false,
    amount_minor bigint NOT NULL,
    currency     text NOT NULL,
    paid_at      timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS billing_payments_paid ON billing_payments (paid_at);
CREATE INDEX IF NOT EXISTS billing_payments_customer ON billing_payments (customer_id);

-- A payment a cardholder's bank has questioned: a dispute (or the inquiry
-- that comes before one), or the bank's early warning of fraud. Kept from
-- Stripe's signed events, the newest applied last. `okwan`: its charge is
-- a payment for Pro; one that is not may be another product's on the same
-- Stripe account. `status` is Stripe's, and alone says whether the bank is
-- waiting for an answer. `submitted`: Stripe counts an answer as sent, at
-- this stage or an earlier one, which is not the same as evidence saved in
-- Stripe (`has_evidence`). `disputed_at`: when it became a dispute, which
-- for an inquiry the bank escalated is later than `opened_at`; the rate
-- counts from it. `told`: what the operator was last mailed about it, the
-- stage it was at or how it ended (`usage.dispute_heard`). `claimed_at`:
-- a mail about it is being sent since then; cleared when the send reports
-- back, and taken over by the next round if it never does. The account's
-- row going leaves the dispute: it still has to be answered.
CREATE TABLE IF NOT EXISTS billing_disputes (
    dispute_id   text PRIMARY KEY,
    kind         text NOT NULL,
    charge_id    text NOT NULL,
    tenant_id    text REFERENCES tenants(id) ON DELETE SET NULL,
    okwan        boolean NOT NULL DEFAULT false,
    amount_minor bigint,
    currency     text,
    reason       text,
    status       text NOT NULL,
    due_by       timestamptz,
    has_evidence boolean NOT NULL DEFAULT false,
    submitted    boolean NOT NULL DEFAULT false,
    opened_at    timestamptz NOT NULL,
    disputed_at  timestamptz,
    closed_at    timestamptz,
    event_at     timestamptz NOT NULL,
    told         text,
    reminded_at  timestamptz,
    claimed_at   timestamptz
);
CREATE INDEX IF NOT EXISTS billing_disputes_opened ON billing_disputes (opened_at DESC);

-- Every step checks before it acts. This runs on every start, and ALTER
-- TABLE and GRANT take locks that a running instance's queries, or
-- autovacuum, would wait for or deadlock against; once the database is
-- current, a start takes none of them.
DO $$
DECLARE
    t text;
    scope constant text :=
        $s$tenant_id = ANY (string_to_array(current_setting('okwan.tenant_ids', true), ','))$s$;
BEGIN
    FOREACH t IN ARRAY ARRAY['credentials', 'usage', 'plans', 'reconciliation_runs',
                             'support_conversations', 'support_tickets', 'support_replies',
                             'import_files', 'import_rows', 'import_layouts',
                             'access_log', 'access_alerts', 'notify_settings', 'daily_checks',
                             'exception_decisions', 'exception_suggestions', 'mail_told'] LOOP
        IF NOT (has_table_privilege('okwan_tenant', t, 'SELECT')
                AND has_table_privilege('okwan_tenant', t, 'INSERT')
                AND has_table_privilege('okwan_tenant', t, 'UPDATE')
                AND has_table_privilege('okwan_tenant', t, 'DELETE')) THEN
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO okwan_tenant', t);
        END IF;
        IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(quote_ident(t))) THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = current_schema() AND tablename = t AND policyname = 'tenant_scope'
        ) THEN
            EXECUTE format('CREATE POLICY tenant_scope ON %I TO okwan_tenant USING (%s) WITH CHECK (%s)',
                           t, scope, scope);
        END IF;
    END LOOP;
    -- The identity tables: nothing for okwan_tenant, whatever was granted before.
    FOREACH t IN ARRAY ARRAY['tenants', 'api_keys', 'sessions', 'accounts', 'signups',
                             'operator_factors', 'operator_actions', 'billing',
                             'billing_checkouts', 'billing_customers', 'billing_payments',
                             'billing_disputes'] LOOP
        IF has_table_privilege('okwan_tenant', t,
                               'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER') THEN
            EXECUTE format('REVOKE ALL ON %I FROM okwan_tenant', t);
        END IF;
    END LOOP;
END $$;
