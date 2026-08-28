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
