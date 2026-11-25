"""The dashboard's Test mode (okwan_api.auth.in_mode).

A signed-in person switches into their account's sandbox: the dashboard
sends its session with `Okwan-Mode: test`. What must hold:

- without the header a session is the live account, as before; with it,
  the same session is the account's sandbox, made first, and nothing else;
- in Test mode a session reaches the sandbox and nothing live, and out of
  it nothing in the sandbox: each side only itself, as with keys;
- what a session makes in Test mode is the sandbox's: a merchant in it,
  with the sample week; a key issued there is a test key;
- the sandbox is managed from the live account, never from inside it, and
  billing is the live account's: Test mode never reaches it;
- its requests count against the sandbox's allowance, not the account's;
- it is the same session: signing out, a password reset or the session
  ending ends Test mode with it;
- a key's own kind decides where it reaches: a key sent with a mode that
  is not its own is refused, never moved, and a mode that is not `test` or
  `live`, or is sent twice, is refused.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from okwan_vault import ADMIN, READ, accounts
from okwan_vault.accounts import SESSION_PREFIX, new_token
from okwan_vault.usage import quota

from tests.test_sandbox import _auth, _sandbox, _test_key, world  # noqa: F401  (a fixture)

TEST = {"Okwan-Mode": "test"}
LIVE = {"Okwan-Mode": "live"}


async def _session(w, tenant_id=None, ttl=timedelta(hours=1)):
    full, token_hash = new_token(SESSION_PREFIX)
    await w.store.create_session(tenant_id or w.acme.id, token_hash, datetime.now(UTC) + ttl)
    return full


def _as(token, mode=None):
    return {**_auth(token), **(mode or {})}


async def _tree(w, token, mode=None):
    r = await w.client.get("/v1/tenants", headers=_as(token, mode))
    assert r.status_code == 200, r.text
    return r.json()


# ── where a session works ──────────────────────────────────────────────


async def test_a_session_is_the_live_account_until_it_names_test_mode(world):  # noqa: F811
    w = world
    token = await _session(w)
    for mode in (None, LIVE):
        tree = await _tree(w, token, mode)
        assert tree["self"]["id"] == w.acme.id
        assert [c["id"] for c in tree["children"]] == [w.shop.id]


async def test_test_mode_needs_the_sandbox_made_first(world):  # noqa: F811
    w = world
    token = await _session(w)
    r = await w.client.get("/v1/tenants", headers=_as(token, TEST))
    assert r.status_code == 409
    assert "no sandbox yet" in r.json()["detail"]


async def test_in_test_mode_the_session_is_the_sandbox(world):  # noqa: F811
    w = world
    token = await _session(w)
    r = await w.client.post("/v1/sandbox", headers=_as(token))
    assert r.status_code == 201, r.text
    sb = r.json()
    tree = await _tree(w, token, TEST)
    assert tree["self"]["id"] == sb["id"] and tree["self"]["name"] == "Sandbox"
    assert tree["children"] == []
    # Its sample week, and the check over it, as a test key has them.
    files = (await w.client.get(f"/v1/tenants/{sb['id']}/imports", headers=_as(token, TEST))).json()["imports"]
    assert sorted(f["filename"] for f in files) == ["sample-bank-statement.csv", "sample-processor-batches.csv"]
    r = await w.client.post(f"/v1/tenants/{sb['id']}/reconciliations/batches_deposits", headers=_as(token, TEST))
    assert r.status_code == 200, r.text
    assert r.json()["summary"]
    # And the answer is in the sandbox's version, which it took from the account.
    assert r.headers["okwan-version"] == sb["api_version"]


async def test_each_side_reaches_only_itself(world):  # noqa: F811
    w = world
    token = await _session(w)
    sb = await _sandbox(w)
    # In Test mode: nothing live, by id, on any admin route.
    for path in (f"/v1/tenants/{w.acme.id}/imports", f"/v1/tenants/{w.shop.id}/runs",
                 f"/v1/tenants/{w.shop.id}/usage", f"/v1/tenants/{w.other.id}/imports"):
        r = await w.client.get(path, headers=_as(token, TEST))
        assert r.status_code in (403, 404), (path, r.status_code)
    r = await w.client.post(f"/v1/tenants/{w.shop.id}/reconciliations/batches_deposits", headers=_as(token, TEST))
    assert r.status_code in (403, 404)
    # Out of it: nothing in the sandbox.
    r = await w.client.get(f"/v1/tenants/{sb['id']}/imports", headers=_as(token))
    assert r.status_code in (403, 404)
    # Another account's session in Test mode reaches its own sandbox, never this one.
    other = await _session(w, w.other.id)
    assert (await w.client.post("/v1/sandbox", headers=_as(other))).status_code == 201
    mine = (await _tree(w, other, TEST))["self"]["id"]
    assert mine != sb["id"]
    r = await w.client.get(f"/v1/tenants/{sb['id']}/imports", headers=_as(other, TEST))
    assert r.status_code in (403, 404)


async def test_what_a_session_makes_in_test_mode_is_the_sandboxs(world):  # noqa: F811
    w = world
    token = await _session(w)
    sb = await _sandbox(w)
    r = await w.client.post("/v1/tenants", headers=_as(token, TEST), json={"name": "Test Café"})
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["parent_id"] == sb["id"]
    # It starts with the sample week, as one made with a test key does.
    files = (await w.client.get(f"/v1/tenants/{made['id']}/imports", headers=_as(token, TEST))).json()["imports"]
    assert len(files) == 2
    # The live account does not have it.
    assert made["id"] not in [c["id"] for c in (await _tree(w, token))["children"]]
    # A key issued in Test mode is a test key; out of it, a live one.
    for tenant_id in (sb["id"], made["id"]):
        r = await w.client.post(f"/v1/tenants/{tenant_id}/keys", headers=_as(token, TEST))
        assert r.status_code == 201, r.text
        assert r.json()["secret"].startswith("okw_test_")
    r = await w.client.post(f"/v1/tenants/{w.shop.id}/keys", headers=_as(token))
    assert r.status_code == 201
    assert r.json()["secret"].startswith("okw_") and not r.json()["secret"].startswith("okw_test_")


async def test_the_sandbox_is_managed_from_the_live_account_only(world):  # noqa: F811
    w = world
    token = await _session(w)
    await _sandbox(w)
    for method, path in (("post", "/v1/sandbox"), ("get", "/v1/sandbox"), ("post", "/v1/sandbox/keys"),
                         ("get", "/v1/sandbox/keys")):
        r = await getattr(w.client, method)(path, headers=_as(token, TEST))
        assert r.status_code == 403, (method, path, r.status_code)
    assert (await w.client.get("/v1/sandbox/keys", headers=_as(token))).status_code == 200


async def test_billing_is_the_live_accounts_and_test_mode_never_reaches_it(world):  # noqa: F811
    w = world
    token = await _session(w)
    await _sandbox(w)
    assert (await w.client.get("/v1/billing", headers=_as(token))).status_code == 200
    for method, path in (("get", "/v1/billing"), ("post", "/v1/billing/checkout"), ("post", "/v1/billing/portal")):
        r = await getattr(w.client, method)(path, headers=_as(token, TEST))
        assert r.status_code == 403, (method, path, r.status_code)
        assert "Test mode" in r.json()["detail"]


async def test_test_mode_counts_against_the_sandbox_not_the_plan(world):  # noqa: F811
    w = world
    token = await _session(w)
    sb = await _sandbox(w)
    before_live = (await quota(w.store, w.acme.id)).used
    before_sb = (await quota(w.store, sb["id"])).used
    r = await w.client.post(f"/v1/tenants/{sb['id']}/reconciliations/batches_deposits", headers=_as(token, TEST))
    assert r.status_code == 200
    assert (await quota(w.store, w.acme.id)).used == before_live
    assert (await quota(w.store, sb["id"])).used > before_sb


# ── the same session ───────────────────────────────────────────────────


async def test_signing_out_in_test_mode_ends_the_session_in_both_modes(world):  # noqa: F811
    w = world
    token = await _session(w)
    await _sandbox(w)
    assert (await w.client.delete("/v1/sessions/current", headers=_as(token, TEST))).status_code == 204
    for mode in (None, TEST):
        assert (await w.client.get("/v1/tenants", headers=_as(token, mode))).status_code == 401


async def test_a_password_reset_ends_test_mode_with_the_session(world):  # noqa: F811
    w = world
    await w.store.add_account(w.acme.id, "owner@acme.test", accounts.hash_password("a long first password"))
    token = await _session(w)
    await _sandbox(w)
    assert (await w.client.get("/v1/tenants", headers=_as(token, TEST))).status_code == 200
    await w.store.reset_password("owner@acme.test", accounts.hash_password("a long second password"))
    assert (await w.client.get("/v1/tenants", headers=_as(token, TEST))).status_code == 401


async def test_an_ended_session_is_not_revived_by_test_mode(world):  # noqa: F811
    w = world
    await _sandbox(w)
    token = await _session(w, ttl=timedelta(seconds=-1))
    assert (await w.client.get("/v1/tenants", headers=_as(token, TEST))).status_code == 401


async def test_a_merchants_session_has_no_test_mode(world):  # noqa: F811
    """A session is an account's; one on a merchant (operator-made, or a
    future one) is refused Test mode rather than given a sandbox."""
    w = world
    await _sandbox(w)
    token = await _session(w, w.shop.id)
    r = await w.client.get("/v1/tenants", headers=_as(token, TEST))
    assert r.status_code == 400


# ── keys and the header ────────────────────────────────────────────────


async def test_a_key_is_never_moved_by_the_header(world):  # noqa: F811
    w = world
    await _sandbox(w)
    test_admin = (await _test_key(w, ADMIN))["secret"]
    test_read = (await _test_key(w, READ))["secret"]
    # A live key that says test is refused, on admin and data routes alike, and does nothing.
    for key in (w.admin, w.read):
        for method, path in (("get", "/v1/exceptions"), ("get", "/v1/tenants"), ("post", "/v1/tenants")):
            r = await w.client.request(method.upper(), path, headers=_as(key, TEST),
                                       json={"name": "x"} if method == "post" else None)
            assert r.status_code == 400, (path, r.status_code)
            assert "live key" in r.json()["detail"]
    assert [c["id"] for c in (await _tree(w, w.admin))["children"]] == [w.shop.id]
    # A test key that says live is refused too.
    for key in (test_admin, test_read):
        r = await w.client.get("/v1/exceptions", headers=_as(key, LIVE))
        assert r.status_code == 400
        assert "test key" in r.json()["detail"]
    # A key that says what it is works as it does without the header.
    assert (await w.client.get("/v1/exceptions", headers=_as(w.read, LIVE))).status_code == 200
    assert (await w.client.get("/v1/exceptions", headers=_as(test_read, TEST))).status_code == 200


@pytest.mark.parametrize("value", ["sandbox", "", "true", "test,live", "live test"])
async def test_a_mode_that_is_not_test_or_live_is_refused(world, value):  # noqa: F811
    w = world
    token = await _session(w)
    await _sandbox(w)
    for credential in (token, w.admin):
        r = await w.client.get("/v1/tenants", headers={**_auth(credential), "Okwan-Mode": value})
        assert r.status_code == 400, (value, r.status_code)


async def test_the_mode_is_read_without_regard_to_case_or_spaces(world):  # noqa: F811
    w = world
    token = await _session(w)
    sb = await _sandbox(w)
    assert (await _tree(w, token, {"Okwan-Mode": " Test "}))["self"]["id"] == sb["id"]


async def test_the_header_sent_twice_is_refused(world):  # noqa: F811
    w = world
    token = await _session(w)
    await _sandbox(w)
    headers = [("Authorization", f"Bearer {token}"), ("Okwan-Mode", "live"), ("Okwan-Mode", "test")]
    r = await w.client.get("/v1/tenants", headers=headers)
    assert r.status_code == 400
