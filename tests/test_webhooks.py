"""Webhooks: signed, retried events on the open items (okwan_api.webhooks).

What must hold:

- an event is signed as Standard Webhooks signs, checked here against the
  specification's own test vector and by code that knows nothing of Okwan's;
- its `data` is the open item as `OpenItem` defines it, the same row
  `GET /v1/exceptions` answers with;
- a run says what opened, aged and cleared since the check's previous good
  result, and a person's decision what closed or opened again; an item a
  person closed is not announced while the decision holds, and nothing is
  said cleared from a result cut at its row limit;
- events go to the accounts that may open the merchant, and to no other;
  with no address, nothing is stored;
- Okwan connects only to the address it checked, public and on 443, follows
  no redirect, reads no answer, retries what failed for three days, holds an
  address's queue while the address is down but not for one event's 4xx, and
  turns off an address that failed every delivery for three days;
- `GET /v1/events` lists an account's own events, from a cursor, to any key
  of it, logged as a read; events about a check that reads files go with the
  files, and everything goes with the tenant.

The receiving end is a real HTTPS server on this machine with its own
certificate authority; DNS is the one thing stood in for, to name it.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import ssl
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import okwan_api.main  # noqa: F401  (registers the connectors and declarations)
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from okwan_api import runs
from okwan_api import webhooks as wh
from okwan_api.auth import set_store
from okwan_recon.exceptions import OpenItem, items_by_key
from okwan_recon.registry import get as get_reconciliation
from okwan_vault import ADMIN, READ, EnvMasterKey, MemoryStore, PostgresStore, new_key

DSN = os.environ.get("OKWAN_VAULT_DATABASE_URL", "")
HOST = "hooks.okwan.test"
needs_db = pytest.mark.skipif(not DSN, reason="no vault database configured")


# ── a receiving end ────────────────────────────────────────────────────

def _certificates(tmp_path):
    """A certificate authority and a server certificate for HOST, signed by it."""
    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    start = datetime.now(UTC) - timedelta(days=1)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(name("Test CA")).issuer_name(name("Test CA"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(start).not_valid_after(start + timedelta(days=3))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(name(HOST)).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(start + timedelta(days=3))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    paths = {n: tmp_path / f"{n}.pem" for n in ("ca", "cert", "key")}
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths["cert"].write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    paths["key"].write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    return paths


class Receiver:
    """An HTTPS server that answers each request with the next status it is
    given (200 once they run out), and keeps what it was sent."""

    def __init__(self, paths):
        self.paths, self.requests, self.statuses, self.names = paths, [], [], []
        #: Seconds to wait before answering a request whose path starts so.
        self.slow: dict[str, float] = {}

    async def start(self):
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(self.paths["cert"], self.paths["key"])
        ctx.sni_callback = lambda _sock, server_name, _ctx: self.names.append(server_name)
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=ctx)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _handle(self, reader, writer):
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode()
            lines = head.split("\r\n")
            method, path, _ = lines[0].split(" ", 2)
            headers = {k.lower(): v for k, v in (line.split(": ", 1) for line in lines[1:] if line)}
            body = (await reader.readexactly(int(headers.get("content-length", "0")))).decode()
            for prefix, seconds in self.slow.items():
                if path.startswith(prefix):
                    await asyncio.sleep(seconds)
            status = self.statuses.pop(0) if self.statuses else 200
            self.requests.append({"method": method, "path": path, "headers": headers, "body": body,
                                  "status": status})
            extra = "Location: https://elsewhere.okwan.test/steal\r\n" if 300 <= status < 400 else ""
            writer.write(f"HTTP/1.1 {status} X\r\nContent-Length: 5\r\nConnection: close\r\n{extra}\r\nhello"
                         .encode())
            await writer.drain()
        except Exception:  # noqa: BLE001, S110 — a probe that hangs up is not a test failure here
            pass
        finally:
            writer.close()

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


@pytest.fixture
async def receiver(tmp_path, monkeypatch):
    paths = _certificates(tmp_path)
    r = await Receiver(paths).start()
    trust = ssl.create_default_context(cafile=str(paths["ca"]))
    monkeypatch.setattr(wh, "PORT", r.port)
    monkeypatch.setattr(wh, "_tls", lambda: trust)
    real = wh.resolve_public

    async def dns(host, port):
        # The one stand-in: HOST is a name only this test knows. Every other
        # name goes through the real check.
        return "127.0.0.1" if host == HOST else await real(host, port)

    monkeypatch.setattr(wh, "resolve_public", dns)
    yield r
    await r.stop()


def _signed_by(secret: str, request: dict) -> bool:
    h = request["headers"]
    key = base64.b64decode(secret.split("_", 1)[1])
    expected = hmac.new(key, f"{h['webhook-id']}.{h['webhook-timestamp']}.{request['body']}".encode(),
                        hashlib.sha256).digest()
    version, sig = h["webhook-signature"].split(",", 1)
    return version == "v1" and hmac.compare_digest(expected, base64.b64decode(sig))


def _verify(secret: str, request: dict) -> dict:
    """Standard Webhooks verification, written from the specification:
    HMAC-SHA256 under the secret's base64-decoded bytes, over
    `id.timestamp.body`, compared in constant time."""
    assert _signed_by(secret, request), "signature does not verify"
    return json.loads(request["body"])


# ── the world ──────────────────────────────────────────────────────────

class World:
    pass


@pytest.fixture(params=["memory", pytest.param("postgres", marks=needs_db)])
async def world(request):
    if request.param == "memory":
        store = MemoryStore(EnvMasterKey(new_key()))
    else:
        store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    set_store(store)
    w = World()
    w.store = store
    w.acme = await store.create_tenant("Acme ISV")
    w.kofi = await store.create_tenant("Kofi's Store", parent_id=w.acme.id)
    w.accra = await store.create_tenant("Accra Coffee", parent_id=w.acme.id)
    w.other = await store.create_tenant("Other ISV")
    w.theirs = await store.create_tenant("Their Store", parent_id=w.other.id)
    w.admin = {name: (await store.issue_key(getattr(w, name).id, scope=ADMIN))[0]
               for name in ("acme", "kofi", "other")}
    w.read = {name: (await store.issue_key(getattr(w, name).id, scope=READ))[0] for name in ("acme", "other")}
    from okwan_api.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://okwan.test") as c:
        w.client = c
        yield w
    if request.param == "postgres":
        for t in (w.acme, w.other):
            await store.pool.execute("DELETE FROM tenants WHERE id = $1", t.id)
        await store.close()


def _auth(key):
    return {"Authorization": f"Bearer {key}"}


def _unpaid(ref, amount, when):
    return {"status": "unmatched_left", "left_ref": ref, "right_ref": None,
            "evidence": {"record": {"amount_minor": amount, "currency": "USD", "when": when}}}


async def _run(shop, refs, at, *, found=None, check="batches_deposits"):
    """A run stored the way every surface stores one (runs.record_run): its
    findings are these unpaid batches; `found` is how many its own counts
    say it found (more than its rows when the result was cut)."""
    rows = [_unpaid(*r) for r in refs]
    counts = {"matched": 0, "unmatched_left": len(rows) if found is None else found}

    class Result:
        summary = counts

        def trimmed_rows(self, *_a, **_k):
            return rows

        def records(self, _side):
            return []

    run_id = await runs.record_run(shop, get_reconciliation(check), "rest", at, result=Result())
    assert run_id is not None
    return run_id


async def _add(w, who="acme", url=f"https://{HOST}/okwan/events?team=7"):
    r = await w.client.post("/v1/webhooks", headers=_auth(w.admin[who]), json={"url": url})
    assert r.status_code == 201, r.text
    return r.json()


async def _events(w, who="acme", key=None, **params):
    r = await w.client.get("/v1/events", headers=_auth(key or w.read[who]), params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _key(shop, ref, rows):
    found = items_by_key(shop.id, "batches_deposits", "pair", rows)
    return next(k for k, i in found.items() if i.get("left_ref") == ref)


T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


# ── signing ────────────────────────────────────────────────────────────

def test_signature_matches_the_standard_webhooks_test_vector():
    """The vector the Standard Webhooks specification publishes."""
    assert wh.sign("whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw", "msg_p5jXN8AQM9LWM0D4loKWxJek", 1614265330,
                   '{"test": 2432232314}') == "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="
    secret = wh.new_secret()
    assert secret.startswith("whsec_") and len(base64.b64decode(secret[6:])) == 24
    assert wh.new_secret() != secret


# ── addresses ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("url, why", [
    ("http://hooks.example.com/x", "https://"),
    ("https://hooks.example.com:8443/x", "port 443"),
    ("https://user:pw@hooks.example.com/x", "user name"),
    ("https://hooks.example.com/x#frag", "fragment"),
    ("https://hooks.example.com/a b", "spaces"),
    ("https://hooks.example.com/\u200bx", "cannot be seen"),
    ("https:///nohost", "names a host"),
    ("https://hooks.example.com/" + "x" * 2100, "at most"),
])
def test_an_address_must_be_https_on_443_with_nothing_hidden(url, why):
    with pytest.raises(wh.BadAddress, match=why):
        wh.check_url(url)


def test_an_address_is_kept_as_a_name_okwan_can_look_up():
    assert wh.check_url("https://Hooks.Example.COM./x?y=1") == ("https://hooks.example.com/x?y=1",
                                                               "hooks.example.com")
    assert wh.check_url("https://bücher.example/") == ("https://xn--bcher-kva.example/", "xn--bcher-kva.example")
    assert wh.check_url("https://[2606:4700::1111]/h")[1] == "2606:4700::1111"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "10.0.0.5", "169.254.169.254", "[::1]",
                                  "metadata.google.internal"])
async def test_an_address_that_is_not_public_is_refused(world, host):
    r = await world.client.post("/v1/webhooks", headers=_auth(world.admin["acme"]),
                                json={"url": f"https://{host}/hook"})
    assert r.status_code == 422 and "public" in r.text
    assert await world.store.webhook_endpoints([world.acme.id]) == []


async def test_the_secret_is_shown_once_and_a_read_key_manages_nothing(world, receiver):
    made = await _add(world)
    assert made["secret"].startswith("whsec_") and made["url"] == f"https://{HOST}:{receiver.port}/okwan/events?team=7"
    listed = (await world.client.get("/v1/webhooks", headers=_auth(world.admin["acme"]))).json()["data"]
    assert [e["id"] for e in listed] == [made["id"]]
    assert made["secret"] not in json.dumps(listed) and listed[0]["secret_hint"] == made["secret"][:10] + "…"
    for method, path in (("get", "/v1/webhooks"), ("post", "/v1/webhooks"),
                         ("delete", f"/v1/webhooks/{made['id']}"), ("post", f"/v1/webhooks/{made['id']}/test"),
                         ("post", f"/v1/webhooks/{made['id']}/enable")):
        r = await world.client.request(method, path, headers=_auth(world.read["acme"]),
                                       json={"url": f"https://{HOST}/x"} if method == "post" else None)
        assert r.status_code == 403, (method, path, r.status_code)
    # Another account cannot see, test, turn on or delete it.
    for method, path in (("delete", f"/v1/webhooks/{made['id']}"), ("post", f"/v1/webhooks/{made['id']}/test"),
                         ("post", f"/v1/webhooks/{made['id']}/enable")):
        r = await world.client.request(method, path, headers=_auth(world.admin["other"]))
        assert r.status_code == 404
    assert (await world.client.get("/v1/webhooks", headers=_auth(world.admin["other"]))).json()["data"] == []
    assert receiver.requests == []


async def test_five_addresses_at_most(world, receiver):
    for _ in range(5):
        await _add(world)
    r = await world.client.post("/v1/webhooks", headers=_auth(world.admin["acme"]), json={"url": f"https://{HOST}/"})
    assert r.status_code == 409


async def test_a_ping_is_signed_and_nothing_is_kept(world, receiver):
    made = await _add(world)
    r = await world.client.post(f"/v1/webhooks/{made['id']}/test", headers=_auth(world.admin["acme"]))
    assert r.json()["delivered"] is True and r.json()["status"] == 200
    body = _verify(made["secret"], receiver.requests[0])
    assert body["type"] == "ping" and body["data"] == {"webhook": made["id"]}
    assert await world.store.webhook_events(world.acme.id, after=None, since=T0 - timedelta(days=400),
                                            limit=10) == []


# ── what a run and a decision say ──────────────────────────────────────

async def test_a_run_says_what_opened_aged_and_cleared(world, receiver, monkeypatch):
    w = world
    await w.store.set_notify(w.acme.id, aging_days=7)
    made = await _add(w)
    # Before the first run: B1 (dated 9 Nov) and B2 (dated 12 Nov, 6 days open).
    await _run(w.kofi, [("B1", 3884, "2026-11-09"), ("B2", 120000, "2026-11-12")], T0)
    first = await _events(w)
    assert sorted(e["type"] for e in first) == ["open_item.opened"] * 2
    # Two days on: B1 was paid, B2 has been open 8 days, B3 is new.
    await _run(w.kofi, [("B2", 120000, "2026-11-12"), ("B3", 500, "2026-11-19")], T0 + timedelta(days=2))
    second = await _events(w, after=first[-1]["id"])
    said = {(e["type"], e["body"]["data"]["left_ref"]) for e in second}
    assert said == {("open_item.cleared", "B1"), ("open_item.aged", "B2"), ("open_item.opened", "B3")}
    # The same result again says nothing.
    await _run(w.kofi, [("B2", 120000, "2026-11-12"), ("B3", 500, "2026-11-19")], T0 + timedelta(days=2, hours=1))
    assert await _events(w, after=second[-1]["id"]) == []

    # Each is delivered, signed, and carries the open item as every surface has it.
    assert await wh.deliver_due() == 5
    bodies = [_verify(made["secret"], r) for r in receiver.requests]
    assert {b["id"] for b in bodies} == {e["id"] for e in first + second}
    assert all(r["headers"]["webhook-id"] == json.loads(r["body"])["id"] for r in receiver.requests)
    assert all(r["path"] == "/okwan/events?team=7" and r["method"] == "POST" for r in receiver.requests)
    b3 = next(b for b in bodies if b["data"]["left_ref"] == "B3")
    assert set(b3["data"]) == set(OpenItem.model_fields) and b3["account"] == w.acme.id
    rest = (await w.client.get("/v1/exceptions", headers=_auth(w.read["acme"]))).json()["data"]
    listed = next(r for r in rest if r["left_ref"] == "B3")
    # The list reads the newest result (the third run); the event was made from the second.
    same = set(OpenItem.model_fields) - {"open_days", "run_id", "run_at"}
    assert {k: b3["data"][k] for k in same} == {k: listed[k] for k in same}
    # Delivered once each: a second round sends nothing.
    assert await wh.deliver_due() == 0 and len(receiver.requests) == 5


async def test_a_decision_closes_and_opens_again_and_a_closed_item_is_not_announced(world, receiver):
    w = world
    await _add(w)
    refs = [("B1", 3884, "2026-11-09")]
    await _run(w.kofi, refs, T0)
    key = _key(w.kofi, "B1", [_unpaid(*r) for r in refs])
    decide = f"/v1/tenants/{w.kofi.id}/exceptions/pair/batches_deposits/{key}/decisions"
    r = await w.client.post(decide, headers={**_auth(w.admin["acme"]), "Idempotency-Key": "d1"},
                            json={"status": "explained", "note": "Held by the processor"})
    assert r.status_code == 201, r.text
    # A retry of the same decision makes no second event.
    await w.client.post(decide, headers={**_auth(w.admin["acme"]), "Idempotency-Key": "d1"},
                        json={"status": "explained", "note": "Held by the processor"})
    types = [e["type"] for e in await _events(w)]
    assert types == ["open_item.opened", "open_item.closed"]
    closed = (await _events(w))[-1]["body"]["data"]
    assert closed["state"] == "closed" and closed["decision_note"] == "Held by the processor"
    # Found again on the same terms: closed, so nothing is said.
    await _run(w.kofi, refs, T0 + timedelta(days=1))
    assert len(await _events(w)) == 2
    # Found on other terms: the decision no longer holds.
    await _run(w.kofi, [("B1", 9999, "2026-11-09")], T0 + timedelta(days=2))
    assert [e["type"] for e in await _events(w)][-1] == "open_item.reopened"
    key2 = _key(w.kofi, "B1", [_unpaid("B1", 9999, "2026-11-09")])
    assert key2 == key
    # Explained again, then opened again by a person.
    for status, note, idem in (("explained", "Fee", "d2"), ("open", "Not the fee after all", "d3")):
        r = await w.client.post(decide, headers={**_auth(w.admin["acme"]), "Idempotency-Key": idem},
                                json={"status": status, "note": note})
        assert r.status_code == 201, r.text
    assert [e["type"] for e in await _events(w)][-2:] == ["open_item.closed", "open_item.reopened"]
    # Given to someone: it stays open, nothing is said.
    r = await w.client.post(decide, headers={**_auth(w.admin["acme"]), "Idempotency-Key": "d4"},
                            json={"status": "assigned", "owner": "Ama"})
    assert r.status_code == 201 and len(await _events(w)) == 5


async def test_a_cut_result_clears_nothing(world, receiver):
    w = world
    await _add(w)
    await _run(w.kofi, [("B1", 1, "2026-11-09"), ("B2", 2, "2026-11-09")], T0)
    # The next result found 5000 but kept two others: B1 and B2 may be among the rest.
    await _run(w.kofi, [("B8", 1, "2026-11-10"), ("B9", 2, "2026-11-10")], T0 + timedelta(hours=1), found=5000)
    said = {(e["type"], e["body"]["data"]["left_ref"]) for e in await _events(w)}
    assert ("open_item.cleared", "B1") not in said and ("open_item.opened", "B9") in said
    cut = next(e for e in await _events(w) if e["body"]["data"]["left_ref"] == "B9")
    assert cut["body"]["data"]["result_cut"] is True


async def test_without_an_address_nothing_is_kept(world):
    await _run(world.kofi, [("B1", 1, "2026-11-09")], T0)
    assert await world.store.webhook_events(world.acme.id, after=None, since=T0 - timedelta(days=400),
                                            limit=10) == []


async def test_only_the_accounts_that_may_open_the_merchant_hear_of_it(world, receiver):
    w = world
    acme = await _add(w, "acme")
    kofi = await _add(w, "kofi")
    other = await _add(w, "other")
    await _run(w.kofi, [("B1", 1, "2026-11-09")], T0)
    await _run(w.accra, [("C1", 2, "2026-11-09")], T0)
    assert {e["body"]["data"]["left_ref"] for e in await _events(w, "acme")} == {"B1", "C1"}
    kofi_feed = await _events(w, key=w.admin["kofi"])
    assert {e["body"]["data"]["left_ref"] for e in kofi_feed} == {"B1"}
    assert all(e["body"]["account"] == w.kofi.id for e in kofi_feed)
    assert await _events(w, "other") == []
    await wh.deliver_due()
    by_secret = {s: [] for s in (acme["secret"], kofi["secret"], other["secret"])}
    for req in receiver.requests:
        for s, refs in by_secret.items():
            if _signed_by(s, req):
                refs.append(json.loads(req["body"])["data"]["left_ref"])
    assert sorted(by_secret[acme["secret"]]) == ["B1", "C1"] and by_secret[kofi["secret"]] == ["B1"]
    assert by_secret[other["secret"]] == []


# ── delivery ───────────────────────────────────────────────────────────

async def test_an_address_that_is_down_is_tried_later_and_holds_its_queue(world, receiver, monkeypatch):
    w = world
    clock = Clock(datetime.now(UTC))
    monkeypatch.setattr(wh, "now", clock)
    await _add(w)
    await _run(w.kofi, [("B1", 1, "2026-11-09"), ("B2", 2, "2026-11-09"), ("B3", 3, "2026-11-09")], T0)
    receiver.statuses = [503]
    assert await wh.deliver_due(clock.at) == 0
    assert len(receiver.requests) == 1, "the rest of the queue waits while the address is down"
    listed = (await w.client.get("/v1/webhooks", headers=_auth(w.admin["acme"]))).json()["data"][0]
    assert listed["failing_since"] is not None and listed["pending"] == 3
    # Not before the first retry, about a minute on.
    clock.at += timedelta(seconds=30)
    assert await wh.deliver_due(clock.at) == 0 and len(receiver.requests) == 1
    clock.at += timedelta(seconds=40)
    assert await wh.deliver_due(clock.at) == 3 and len(receiver.requests) == 4
    ids = [json.loads(r["body"])["id"] for r in receiver.requests]
    assert ids[0] in ids[1:], "the failed event was sent again with the same id"
    listed = (await w.client.get("/v1/webhooks", headers=_auth(w.admin["acme"]))).json()["data"][0]
    assert listed["failing_since"] is None and listed["last_ok_at"] is not None and listed["pending"] == 0
    states = {d["state"] for e in await _events(w) for d in e["deliveries"]}
    assert states == {"delivered"}


async def test_a_4xx_on_one_event_holds_back_no_other(world, receiver, monkeypatch):
    w = world
    clock = Clock(datetime.now(UTC))
    monkeypatch.setattr(wh, "now", clock)
    await _add(w)
    await _run(w.kofi, [("B1", 1, "2026-11-09"), ("B2", 2, "2026-11-09")], T0)
    receiver.statuses = [400]
    assert await wh.deliver_due(clock.at) == 1 and len(receiver.requests) == 2


async def test_three_days_of_failure_turn_the_address_off_and_mail_the_account(world, receiver, monkeypatch):
    w = world
    sent = []

    class Mailer:
        async def send_notice(self, to, subject, text, html=None, reply_to=None, headers=None):
            sent.append((to, subject, text))

    monkeypatch.setattr(wh, "get_mailer", lambda: Mailer())
    clock = Clock(datetime.now(UTC))
    monkeypatch.setattr(wh, "now", clock)
    await w.store.add_account(w.acme.id, f"ops-{uuid.uuid4().hex[:8]}@acme.test", "x" * 60)
    made = await _add(w)
    await _run(w.kofi, [("B1", 1, "2026-11-09"), ("B2", 2, "2026-11-09")], T0)
    receiver.statuses = [500] * 100
    start = clock.at
    rounds = 0
    while clock.at - start < timedelta(days=3, hours=12) and rounds < 100:
        await wh.deliver_due(clock.at)
        clock.at += timedelta(hours=1)
        rounds += 1
    listed = (await w.client.get("/v1/webhooks", headers=_auth(w.admin["acme"]))).json()["data"][0]
    assert listed["enabled"] is False and "three days" in listed["disabled_why"] and listed["pending"] == 0
    tried = len(receiver.requests)
    assert 6 <= tried <= 14, f"about every growing interval, not every round: {tried}"
    states = {d["state"] for e in await _events(w) for d in e["deliveries"]}
    assert states == {"failed"}
    # Nothing more is sent to it; turned back on, new events go again.
    clock.at += timedelta(days=1)
    await wh.deliver_due(clock.at)
    assert len(receiver.requests) == tried
    r = await w.client.post(f"/v1/webhooks/{made['id']}/enable", headers=_auth(w.admin["acme"]))
    assert r.json()["enabled"] is True
    receiver.statuses = []
    await _run(w.kofi, [("B3", 3, "2026-11-09")], T0 + timedelta(days=4))
    assert await wh.deliver_due(clock.at) >= 1
    assert len(sent) == 1, "the account is mailed once, when the address is turned off"
    to, subject, text = sent[0]
    assert to.endswith("@acme.test") and HOST in subject and made["id"] in text
    assert made["secret"] not in text and "B1" not in text and "/enable" in text


async def test_no_redirect_is_followed_and_no_answer_is_kept(world, receiver, monkeypatch):
    w = world
    monkeypatch.setattr(wh, "now", Clock(datetime.now(UTC)))
    await _add(w)
    await _run(w.kofi, [("B1", 1, "2026-11-09")], T0)
    receiver.statuses = [302]
    assert await wh.deliver_due(wh.now()) == 0
    assert len(receiver.requests) == 1 and receiver.requests[0]["path"] != "/steal"
    delivery = (await _events(w))[0]["deliveries"][0]
    assert delivery["last_status"] == 302 and delivery["state"] == "pending"
    assert "hello" not in json.dumps(await _events(w))


async def test_the_connection_goes_to_the_address_that_was_checked(receiver):
    """HOST is not in any DNS: the request reaches the receiver only because
    the checked address (127.0.0.1) is dialed, and TLS is checked against
    HOST, which the receiver's certificate names."""
    status, error = await wh.send(f"https://{HOST}:{receiver.port}/x", "{}", {"webhook-id": "evt_x"})
    assert (status, error) == (200, None)
    assert receiver.names == [HOST] and receiver.requests[0]["headers"]["host"] == f"{HOST}:{receiver.port}"


async def test_a_certificate_for_another_name_is_refused(receiver, monkeypatch):
    real = wh.resolve_public

    async def dns(host, port):
        return "127.0.0.1" if host == "other.okwan.test" else await real(host, port)

    monkeypatch.setattr(wh, "resolve_public", dns)
    status, error = await wh.send(f"https://other.okwan.test:{receiver.port}/x", "{}", {})
    assert status is None and error == "TLS failed" and receiver.requests == []


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "169.254.169.254"])
async def test_a_host_that_is_not_public_is_never_dialed(receiver, host):
    status, error = await wh.send(f"https://{host}:{receiver.port}/x", "{}", {})
    assert status is None and error.startswith("refused") and receiver.requests == []


async def test_a_slow_address_holds_up_no_other_account(world, receiver):
    w = world
    await _add(w, "acme", f"https://{HOST}/slow")
    await _add(w, "other", f"https://{HOST}/fast")
    receiver.slow["/slow"] = 3.0
    await _run(w.kofi, [("B1", 1, "2026-11-09"), ("B2", 2, "2026-11-09")], T0)
    await _run(w.theirs, [("Z1", 1, "2026-11-09")], T0)
    await wh.deliver_due(wait=False)
    for _ in range(50):
        if any(r["path"] == "/fast" for r in receiver.requests):
            break
        await asyncio.sleep(0.05)
    assert [r["path"] for r in receiver.requests] == ["/fast"], "the other account waited for the slow address"
    # A second round takes nothing more for the address still being sent to.
    assert await wh.deliver_due() == 0
    await asyncio.gather(*list(wh._sending.values()))
    assert sorted(r["path"] for r in receiver.requests) == ["/fast", "/slow", "/slow"]


async def test_a_round_takes_ten_deliveries_an_address(world, receiver):
    await _add(world)
    await _run(world.kofi, [(f"B{i}", i, "2026-11-09") for i in range(15)], T0)
    assert await wh.deliver_due() == 10
    assert await wh.deliver_due() == 5


def test_the_retries_grow_and_stop_after_three_days():
    at = datetime(2026, 11, 20, tzinfo=UTC)
    waits = [(wh._retry_at(n, at, at) - at).total_seconds() for n in range(1, 9)]
    assert 54 <= waits[0] <= 66 and 270 <= waits[1] <= 330 and waits[4] >= 16200
    assert all(w <= 39600 for w in waits)
    assert wh._retry_at(3, at + timedelta(days=3), at) is None


# ── the events list ────────────────────────────────────────────────────

async def test_the_events_list_is_the_accounts_own_from_a_cursor_and_is_logged(world, receiver):
    w = world
    await _add(w)
    await _add(w, "other")
    await _run(w.kofi, [("B1", 1, "2026-11-09"), ("B2", 2, "2026-11-09"), ("B3", 3, "2026-11-09")], T0)
    await _run(w.theirs, [("Z1", 1, "2026-11-09")], T0)
    mine = await _events(w)
    assert len(mine) == 3 and all(e["merchant_id"] == w.kofi.id for e in mine)
    page = await _events(w, limit=2)
    rest = await _events(w, after=page[-1]["id"])
    assert [e["id"] for e in page + rest] == [e["id"] for e in mine]
    theirs = await _events(w, "other")
    assert [e["body"]["data"]["left_ref"] for e in theirs] == ["Z1"]
    # Another account's event as a cursor lists nothing.
    assert await _events(w, after=theirs[0]["id"]) == []
    assert await _events(w, type="open_item.cleared") == []
    entries = await w.store.access_entries(w.acme.id, T0 - timedelta(days=1))
    assert any(e["surface"] == "rest:events" for e in entries)
    r = await w.client.get("/v1/events")
    assert r.status_code == 401


async def test_events_about_a_check_that_reads_files_go_with_the_files(world, receiver):
    w = world
    await _add(w)
    await _run(w.kofi, [("B1", 1, "2026-11-09")], T0)
    await _run(w.accra, [("C1", 1, "2026-11-09")], T0)
    imp = await w.store.add_import(
        w.kofi.id, kind="bank_deposits", filename="s.csv", format="csv", layout="custom", mapping={},
        sha256=uuid.uuid4().hex, rows=[(2, dt.date(2026, 11, 9), {"fitid": "x"})], skipped=[], card_columns=[],
        first_date=dt.date(2026, 11, 9), last_date=dt.date(2026, 11, 9))
    assert await w.store.delete_import(w.kofi.id, imp.id)
    left = await _events(w)
    assert [e["merchant_id"] for e in left] == [w.accra.id]


async def test_everything_goes_with_the_tenant(world, receiver):
    w = world
    await _add(w)
    await _run(w.kofi, [("B1", 1, "2026-11-09")], T0)
    await _run(w.accra, [("C1", 1, "2026-11-09")], T0)
    await w.store.delete_tenant(w.kofi.id)
    assert [e["merchant_id"] for e in await _events(w)] == [w.accra.id]
    await w.store.delete_tenant(w.acme.id)
    assert await w.store.webhook_endpoints([w.acme.id]) == []


# ── the document ───────────────────────────────────────────────────────

def test_the_openapi_document_names_each_event_and_its_open_item():
    from okwan_api.main import app

    spec = app.openapi()
    assert sorted(spec["webhooks"]) == sorted(wh.EVENT_TYPES)
    schema = spec["components"]["schemas"]["OpenItemEvent"]
    assert schema["properties"]["data"]["$ref"].endswith("/OpenItem")
    assert set(spec["components"]["schemas"]["OpenItem"]["properties"]) == set(OpenItem.model_fields)


# ── Postgres ───────────────────────────────────────────────────────────

@needs_db
async def test_two_rounds_never_take_the_same_delivery():
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    set_store(store)
    acme = await store.create_tenant("Acme ISV")
    try:
        shop = await store.create_tenant("Shop", parent_id=acme.id)
        at = datetime.now(UTC)
        await store.add_webhook_endpoint(acme.id, url=f"https://{HOST}/", secret=wh.new_secret(),
                                         created_by="session", at=at)
        await _run(shop, [(f"B{i}", i, "2026-11-09") for i in range(1, 41)], T0)
        a, b = await asyncio.gather(*(store.claim_webhooks(acme.id, at + timedelta(seconds=1),
                                                           at + timedelta(minutes=1), 30) for _ in range(2)))
        ids_a, ids_b = {c.event_id for c in a}, {c.event_id for c in b}
        assert ids_a.isdisjoint(ids_b) and len(ids_a | ids_b) == 40
        assert await store.claim_webhooks(acme.id, at + timedelta(seconds=2), at + timedelta(minutes=1), 30) == []
    finally:
        await store.pool.execute("DELETE FROM tenants WHERE id = $1", acme.id)
        await store.close()


@needs_db
async def test_a_merchant_scope_sees_and_deletes_events_about_itself_and_writes_none_for_its_account():
    store = await PostgresStore(DSN, EnvMasterKey(new_key())).connect()
    set_store(store)
    acme = await store.create_tenant("Acme ISV")
    try:
        shop = await store.create_tenant("Shop", parent_id=acme.id)
        sister = await store.create_tenant("Sister", parent_id=acme.id)
        await store.add_webhook_endpoint(acme.id, url=f"https://{HOST}/", secret=wh.new_secret(),
                                         created_by="session", at=datetime.now(UTC))
        await _run(shop, [("B1", 1, "2026-11-09")], T0)
        await _run(sister, [("S1", 1, "2026-11-09")], T0)
        async with store._scoped(shop.id) as con:
            seen = {r["merchant_id"] for r in await con.fetch("SELECT merchant_id FROM webhook_events")}
            assert seen == {shop.id}
            assert await con.fetchval("SELECT count(*) FROM webhook_deliveries") == 0
            assert await con.fetchval("SELECT count(*) FROM webhook_endpoints") == 0
        with pytest.raises(Exception, match="row-level security"):
            async with store._scoped(shop.id) as con:
                await con.execute(
                    "INSERT INTO webhook_events (id, tenant_id, merchant_id, type, created_at, body) "
                    "VALUES ('evt_probe', $1, $2, 'open_item.opened', now(), '{}')", acme.id, shop.id)
        async with store._scoped(sister.id) as con:
            assert {r["merchant_id"] for r in await con.fetch("SELECT merchant_id FROM webhook_events")} == {sister.id}
    finally:
        await store.pool.execute("DELETE FROM tenants WHERE id = $1", acme.id)
        await store.close()
