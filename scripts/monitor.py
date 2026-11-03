"""Okwan from the outside, the way a customer meets it.

    python scripts/monitor.py            # human-readable, exit 1 on any failure
    python scripts/monitor.py --json     # one JSON document, for the alerting job

Run every ten minutes by .github/workflows/monitor.yml, which opens an
incident issue when a check fails and closes it when every check passes
again. Each check names the runbook section that fixes it
(docs/ops/RUNBOOK.md), so whoever is on call starts from the fix, not
from the symptom.

Checks:

* the API answers /healthz with the Postgres vault (not the in-memory
  fallback) and the expected number of connectors;
* the dashboard and the site answer;
* the hosted MCP endpoint completes an MCP handshake;
* an authenticated call works end to end, when OKWAN_MONITOR_KEY is set:
  a key on a tenant that holds no rail credentials, so the monitor can
  prove sign-in, the vault and metering work without reading anyone's
  money;
* every TLS certificate has more than CERT_WARN_DAYS left.

Only standard library plus httpx, which the API already depends on. No
secret is printed: the key is sent in a header and never echoed.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import httpx

API = os.environ.get("OKWAN_MONITOR_API", "https://api.okwan.ai")
DASHBOARD = os.environ.get("OKWAN_MONITOR_DASHBOARD", "https://app.okwan.ai")
SITE = os.environ.get("OKWAN_MONITOR_SITE", "https://okwan.ai")
EXPECTED_CONNECTORS = int(os.environ.get("OKWAN_MONITOR_CONNECTORS", "6"))
CERT_WARN_DAYS = 14
SLOW_SECONDS = 5.0
TIMEOUT = httpx.Timeout(20.0)


@dataclass
class Result:
    check: str
    ok: bool
    detail: str
    runbook: str
    seconds: float = 0.0


def _get(client: httpx.Client, url: str, **kw) -> tuple[httpx.Response | None, float, str]:
    started = time.monotonic()
    try:
        r = client.get(url, **kw)
        return r, time.monotonic() - started, ""
    except httpx.HTTPError as exc:
        return None, time.monotonic() - started, f"{type(exc).__name__}: {exc}"


def check_api(client: httpx.Client) -> Result:
    r, s, err = _get(client, f"{API}/healthz")
    if r is None:
        return Result("api", False, f"unreachable: {err}", "#api-down", s)
    if r.status_code == 503:
        return Result("api", False, "vault unreachable (503)", "#vault-unreachable", s)
    if r.status_code != 200:
        return Result("api", False, f"/healthz answered {r.status_code}", "#api-down", s)
    body = r.json()
    if body.get("vault") != "postgres":
        return Result("api", False, f"vault is {body.get('vault')!r}, not postgres", "#in-memory-vault", s)
    if body.get("connectors") != EXPECTED_CONNECTORS:
        return Result("api", False, f"{body.get('connectors')} connectors, expected "
                      f"{EXPECTED_CONNECTORS}", "#wrong-build", s)
    return Result("api", s < SLOW_SECONDS, f"ok in {s:.2f}s" if s < SLOW_SECONDS else f"slow: {s:.2f}s",
                  "#slow", s)


def check_page(client: httpx.Client, name: str, url: str, runbook: str) -> Result:
    r, s, err = _get(client, url, follow_redirects=True)
    if r is None:
        return Result(name, False, f"unreachable: {err}", runbook, s)
    if r.status_code >= 400:
        return Result(name, False, f"answered {r.status_code}", runbook, s)
    return Result(name, True, f"{r.status_code} in {s:.2f}s", runbook, s)


def check_mcp(client: httpx.Client) -> Result:
    """A real MCP handshake: proves the mount and its session manager run.
    Needs no key; tools refuse a call without one."""
    started = time.monotonic()
    try:
        r = client.post(f"{API}/mcp/", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "okwan-monitor", "version": "1"}},
        }, headers={"accept": "application/json, text/event-stream"})
    except httpx.HTTPError as exc:
        return Result("mcp", False, f"unreachable: {exc}", "#mcp", time.monotonic() - started)
    s = time.monotonic() - started
    if r.status_code == 421:
        return Result("mcp", False, "invalid host (421)", "#mcp-host", s)
    if r.status_code != 200 or '"serverInfo"' not in r.text:
        return Result("mcp", False, f"handshake answered {r.status_code}", "#mcp", s)
    return Result("mcp", True, f"handshake ok in {s:.2f}s", "#mcp", s)


def check_authenticated(client: httpx.Client) -> Result | None:
    key = os.environ.get("OKWAN_MONITOR_KEY", "")
    if not key:
        return None
    r, s, err = _get(client, f"{API}/v1/reconciliations", headers={"authorization": f"Bearer {key}"})
    if r is None:
        return Result("authenticated", False, f"unreachable: {err}", "#api-down", s)
    if r.status_code == 401:
        return Result("authenticated", False, "the monitor's key was refused (401)", "#monitor-key", s)
    if r.status_code == 402:
        return Result("authenticated", False, "the monitor tenant is over its plan (402)", "#quota", s)
    if r.status_code != 200:
        return Result("authenticated", False, f"answered {r.status_code}", "#api-down", s)
    return Result("authenticated", True, f"ok in {s:.2f}s", "#api-down", s)


def check_cert(host: str) -> Result:
    started = time.monotonic()
    try:
        ctx = ssl.create_default_context()
        with (socket.create_connection((host, 443), timeout=15) as sock,
              ctx.wrap_socket(sock, server_hostname=host) as tls):
            not_after = tls.getpeercert()["notAfter"]
    except (OSError, ssl.SSLError) as exc:
        return Result(f"tls:{host}", False, f"{type(exc).__name__}: {exc}", "#tls",
                      time.monotonic() - started)
    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), UTC)
    days = (expires - datetime.now(UTC)).days
    return Result(f"tls:{host}", days > CERT_WARN_DAYS, f"expires in {days} days", "#tls",
                  time.monotonic() - started)


def run() -> list[Result]:
    with httpx.Client(timeout=TIMEOUT, headers={"user-agent": "okwan-monitor"}) as client:
        results = [
            check_api(client),
            check_page(client, "dashboard", f"{DASHBOARD}/", "#dashboard-down"),
            check_page(client, "site", SITE, "#site-down"),
            check_mcp(client),
        ]
        if (auth := check_authenticated(client)) is not None:
            results.append(auth)
    for url in (API, DASHBOARD, SITE):
        results.append(check_cert(httpx.URL(url).host))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print one JSON document")
    args = parser.parse_args()
    results = run()
    failed = [r for r in results if not r.ok]
    if args.json:
        print(json.dumps({"ok": not failed, "at": datetime.now(UTC).isoformat(timespec="seconds"),
                          "results": [asdict(r) for r in results]}, indent=2))
    else:
        for r in results:
            print(f"{'ok  ' if r.ok else 'FAIL'}  {r.check:<24} {r.detail}"
                  + ("" if r.ok else f"   → docs/ops/RUNBOOK.md{r.runbook}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
