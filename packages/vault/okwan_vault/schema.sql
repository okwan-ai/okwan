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
-- Anything referencing it must come after this line.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS parent_id text
    REFERENCES tenants(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS tenants_parent_idx ON tenants (parent_id)
    WHERE parent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS api_keys (
    id          text PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    prefix      text NOT NULL,
    hash_hex    text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz
);

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
-- the insert prunes the rest in the same transaction.
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
    error        text
);

-- The newest run per tenant and name is what every page asks for.
CREATE INDEX IF NOT EXISTS reconciliation_runs_latest_idx
    ON reconciliation_runs (tenant_id, name, finished_at DESC);
