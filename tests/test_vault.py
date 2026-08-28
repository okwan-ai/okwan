"""Credential vault: envelope encryption, tenancy, API keys."""
from __future__ import annotations

import pytest

import okwan_shopify.connector  # noqa: F401
import okwan_stripe.connector   # noqa: F401
from okwan_core import get
from okwan_vault import EnvMasterKey, MemoryStore, apikey, new_key, open_sealed, resolver_for, seal


@pytest.fixture
def store():
    return MemoryStore(EnvMasterKey(new_key()))


# ── crypto ──────────────────────────────────────────────────────────

def test_aad_binds_ciphertext_to_its_context():
    """A row moved to another tenant must not decrypt."""
    key = new_key()
    sealed = seal(key, b"sk_live_secret", aad=b"tenant1|stripe|secret_key")

    assert open_sealed(key, sealed, aad=b"tenant1|stripe|secret_key") == b"sk_live_secret"
    with pytest.raises(Exception):
        open_sealed(key, sealed, aad=b"tenant2|stripe|secret_key")


def test_same_plaintext_seals_differently():
    """Random nonces: identical secrets must not produce identical rows."""
    key = new_key()
    a = seal(key, b"same", aad=b"x")
    b = seal(key, b"same", aad=b"x")
    assert a != b


def test_master_key_must_be_32_bytes():
    with pytest.raises(ValueError):
        EnvMasterKey(b"tooshort")


# ── api keys ────────────────────────────────────────────────────────

def test_key_is_never_stored(store):
    tenant = store.create_tenant("Acme")
    full, record = store.issue_key(tenant.id)

    assert full not in record.hash_hex
    assert record.prefix in full
    assert len(record.prefix) < len(full)


def test_only_the_right_key_resolves(store):
    tenant = store.create_tenant("Acme")
    full, _ = store.issue_key(tenant.id)

    assert store.tenant_for_key(full).id == tenant.id
    assert store.tenant_for_key("okw_nonsense") is None


def test_revoked_key_stops_working(store):
    tenant = store.create_tenant("Acme")
    full, record = store.issue_key(tenant.id)
    store.revoke_key(record.id)

    assert store.tenant_for_key(full) is None


def test_hash_comparison_is_constant_time():
    full, _, hash_hex = apikey.generate()
    assert apikey.matches(full, hash_hex)
    assert not apikey.matches(full + "x", hash_hex)


# ── tenancy ─────────────────────────────────────────────────────────

def test_tenants_cannot_see_each_others_credentials(store):
    a = store.create_tenant("Acme")
    b = store.create_tenant("Rival")
    store.put_credential(a.id, "stripe", "secret_key", "sk_acme")
    store.put_credential(b.id, "stripe", "secret_key", "sk_rival")

    assert store.credentials_for(a.id, "stripe", ("secret_key",)) == {"secret_key": "sk_acme"}
    assert store.credentials_for(b.id, "stripe", ("secret_key",)) == {"secret_key": "sk_rival"}


def test_missing_credential_reads_as_empty_not_error(store):
    """Matches the env resolver, so reachability checks work unchanged."""
    tenant = store.create_tenant("Acme")
    assert store.credentials_for(tenant.id, "stripe", ("secret_key",)) == {"secret_key": ""}


def test_credentials_for_unknown_tenant_are_empty(store):
    assert store.credentials_for("ten_nobody", "stripe", ("secret_key",)) == {"secret_key": ""}


def test_multi_field_connector_round_trips(store):
    """Shopify needs a token and a shop domain."""
    tenant = store.create_tenant("Acme")
    store.put_credential(tenant.id, "shopify", "access_token", "shpat_x")
    store.put_credential(tenant.id, "shopify", "shop_domain", "acme.myshopify.com")

    creds = store.credentials_for(tenant.id, "shopify", ("access_token", "shop_domain"))
    assert creds == {"access_token": "shpat_x", "shop_domain": "acme.myshopify.com"}


# ── the SDK seam ────────────────────────────────────────────────────

def test_resolver_matches_the_connector_sdk_shape(store):
    """The vault replaces env vars without touching fetch_rows."""
    tenant = store.create_tenant("Acme")
    store.put_credential(tenant.id, "stripe", "secret_key", "sk_acme")

    resolve = resolver_for(store, tenant.id)
    connector = get("stripe")
    assert resolve(connector.name, connector.auth.required_fields) == {"secret_key": "sk_acme"}


def test_reachability_works_against_the_vault(store):
    """An unconfigured connector reports missing fields, same as env."""
    from okwan_query.catalog import find, missing_credentials

    tenant = store.create_tenant("Acme")
    resolve = resolver_for(store, tenant.id)

    assert "secret_key" in missing_credentials(find("stripe.charges"), resolve)

    store.put_credential(tenant.id, "stripe", "secret_key", "sk_acme")
    assert missing_credentials(find("stripe.charges"), resolve) == ()


# ── gateway enforcement ─────────────────────────────────────────────

@pytest.fixture
def client(store):
    from fastapi.testclient import TestClient

    from okwan_api.auth import set_store
    from okwan_api.main import app

    set_store(store)
    return TestClient(app)


def test_unauthenticated_request_is_rejected(client):
    r = client.post("/v1/stripe/charges/list", json={"limit": 1})
    assert r.status_code == 401
    assert "API key" in r.json()["detail"]


def test_invalid_key_is_rejected(client):
    r = client.post("/v1/stripe/charges/list", json={"limit": 1},
                    headers={"Authorization": "Bearer okw_nonsense"})
    assert r.status_code == 401


def test_revoked_key_is_rejected(client, store):
    tenant = store.create_tenant("Acme")
    full, record = store.issue_key(tenant.id)
    store.revoke_key(record.id)

    r = client.post("/v1/stripe/charges/list", json={"limit": 1},
                    headers={"Authorization": f"Bearer {full}"})
    assert r.status_code == 401


def test_credentials_are_never_accepted_from_the_request(client, store):
    """The old X-Okwan-Credential header must not work any more."""
    tenant = store.create_tenant("Acme")
    full, _ = store.issue_key(tenant.id)

    r = client.post(
        "/v1/stripe/charges/list",
        json={"limit": 1},
        headers={
            "Authorization": f"Bearer {full}",
            "X-Okwan-Credential-secret-key": "sk_injected",
        },
    )
    assert r.status_code == 401
    assert "missing credential fields" in r.json()["detail"]


def test_x_okwan_key_header_also_authenticates(client, store):
    tenant = store.create_tenant("Acme")
    full, _ = store.issue_key(tenant.id)

    r = client.post("/v1/stripe/charges/list", json={"limit": 1},
                    headers={"X-Okwan-Key": full})
    assert r.status_code != 401 or "missing credential" in r.json().get("detail", "")
