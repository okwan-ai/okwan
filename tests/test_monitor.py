"""scripts/monitor.py: each check reads the failure it is for, and every
failure points at a runbook section that exists."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("monitor", ROOT / "scripts" / "monitor.py")
monitor = importlib.util.module_from_spec(spec)
sys.modules["monitor"] = monitor  # dataclasses resolve their module by name
spec.loader.exec_module(monitor)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _health(body: dict, status: int = 200):
    return lambda request: httpx.Response(status, json=body)


@pytest.mark.parametrize("body,status,ok,anchor", [
    ({"status": "ok", "vault": "postgres", "connectors": 6}, 200, True, "#slow"),
    ({"status": "ok", "vault": "memory", "connectors": 6}, 200, False, "#in-memory-vault"),
    ({"status": "ok", "vault": "postgres", "connectors": 5}, 200, False, "#wrong-build"),
    ({"detail": "vault unreachable"}, 503, False, "#vault-unreachable"),
    ({"detail": "boom"}, 500, False, "#api-down"),
])
def test_the_api_check_names_the_failure(body, status, ok, anchor, monkeypatch):
    monkeypatch.setattr(monitor, "EXPECTED_CONNECTORS", 6)
    monkeypatch.setattr(monitor, "commit_age", lambda: None)  # no deploy under way
    r = monitor.check_api(_client(_health(body, status)))
    assert (r.ok, r.runbook) == (ok, anchor)


def test_the_expected_count_is_what_the_api_registers(monkeypatch):
    """Counted in a fresh process: the API's own registry, not this test run's."""
    import subprocess

    monkeypatch.delenv("OKWAN_MONITOR_CONNECTORS", raising=False)
    count = "import okwan_api.main; from okwan_core import all_connectors; print(len(all_connectors()))"
    out = subprocess.run([sys.executable, "-c", count], capture_output=True, text=True, check=True)
    assert monitor.expected_connectors() == int(out.stdout.strip().splitlines()[-1])


def test_a_pinned_count_wins(monkeypatch):
    monkeypatch.setenv("OKWAN_MONITOR_CONNECTORS", "4")
    assert monitor.expected_connectors() == 4


@pytest.mark.parametrize("age,ok", [(5 * 60, True), (45 * 60, False), (None, False)])
def test_a_lower_count_just_after_a_commit_is_that_commit_deploying(age, ok, monkeypatch):
    monkeypatch.setattr(monitor, "EXPECTED_CONNECTORS", 9)
    monkeypatch.setattr(monitor, "commit_age", lambda: age)
    r = monitor.check_api(_client(_health({"status": "ok", "vault": "postgres", "connectors": 8})))
    assert (r.ok, r.runbook) == (ok, "#wrong-build")
    assert "main registers 9" in r.detail and (("deploying" in r.detail) is ok)


def test_an_unreachable_api_is_api_down():
    def refuse(request):
        raise httpx.ConnectError("refused")

    r = monitor.check_api(_client(refuse))
    assert (r.ok, r.runbook) == (False, "#api-down")


@pytest.mark.parametrize("status,text,ok,anchor", [
    (200, 'event: message\ndata: {"result":{"serverInfo":{"name":"okwan"}}}', True, "#mcp"),
    (421, "invalid host", False, "#mcp-host"),
    (500, "error", False, "#mcp"),
    (400, "bad request", False, "#mcp"),
])
def test_the_mcp_check_wants_a_real_handshake(status, text, ok, anchor):
    r = monitor.check_mcp(_client(lambda request: httpx.Response(status, text=text)))
    assert (r.ok, r.runbook) == (ok, anchor)


@pytest.mark.parametrize("status,ok,anchor", [(200, True, "#api-down"), (401, False, "#monitor-key"),
                                              (402, False, "#quota")])
def test_the_authenticated_check(status, ok, anchor, monkeypatch):
    monkeypatch.setenv("OKWAN_MONITOR_KEY", "okw_test")
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(status, json={})

    r = monitor.check_authenticated(_client(handler))
    assert (r.ok, r.runbook) == (ok, anchor)
    assert seen == ["Bearer okw_test"]
    assert "okw_test" not in r.detail


def test_without_a_key_the_authenticated_check_is_skipped(monkeypatch):
    monkeypatch.delenv("OKWAN_MONITOR_KEY", raising=False)
    assert monitor.check_authenticated(_client(lambda r: httpx.Response(500))) is None


def _slug(heading: str) -> str:
    """GitHub's anchor for a Markdown heading."""
    s = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return s.replace(" ", "-")


def test_every_runbook_link_the_monitor_gives_exists():
    runbook = (ROOT / "docs" / "ops" / "RUNBOOK.md").read_text()
    anchors = {_slug(h) for h in re.findall(r"^#{2,4} (.+)$", runbook, re.MULTILINE)}
    sources = (ROOT / "scripts" / "monitor.py").read_text() + (
        ROOT / ".github" / "workflows" / "monitor.yml").read_text()
    linked = set(re.findall(r'"#([a-z0-9-]+)"', sources))
    assert linked, "found no runbook links to check"
    assert linked <= anchors, f"runbook sections missing: {sorted(linked - anchors)}"


def test_the_runbooks_own_links_resolve():
    runbook = (ROOT / "docs" / "ops" / "RUNBOOK.md").read_text()
    anchors = {_slug(h) for h in re.findall(r"^#{2,4} (.+)$", runbook, re.MULTILINE)}
    links = set(re.findall(r"\]\(#([a-z0-9-]+)\)", runbook))
    assert links <= anchors, f"broken links in the runbook: {sorted(links - anchors)}"
