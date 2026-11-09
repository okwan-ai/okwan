"""Every connector is shown with its owner's original logo, never a letter.

The standing rule (CLAUDE.md, standing workflow): a connector registered in
the SDK ships with an entry in the dashboard's brand-logos.ts, so a new one
cannot reach a tenant's Integrations page as an initial on a blank tile.
The logos are drawn with `dangerouslySetInnerHTML`, so each must also stay
plain, static SVG: no script, no event handler, nothing fetched from
elsewhere.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "apps" / "dashboard" / "app" / "_components" / "ui"

#: Okwan's own connectors: no third party, so no third-party logo.
OKWAN_OWN = {"files"}
#: Logos still to come, each with why. Only ever shrinks: the test below
#: fails once a logo lands and its name is still here.
AWAITING = {
    "paystack": "paystack.com refuses an automated download; the owner adds the file from its brand assets",
}

_ENTRY = re.compile(r'^\s+"?([a-z0-9-]+)"?: \{ viewBox: ("[^"]*"), body: (".*") \},$', re.MULTILINE)


def _logos() -> dict[str, tuple[str, str]]:
    return {k: (json.loads(vb), json.loads(body)) for k, vb, body in _ENTRY.findall((UI / "brand-logos.ts").read_text())}


def _record(name: str) -> dict[str, str]:
    body = (UI / "brand-mark.tsx").read_text()
    block = body[body.index(f"const {name}"):]
    block = block[:block.index("};")]
    return dict(re.findall(r'"?([a-z0-9-]+)"?: "([a-z0-9-]+)"', block))


def _connectors() -> list[str]:
    import okwan_api.main  # noqa: F401  registers every connector
    from okwan_core import all_connectors

    return sorted(c.name for c in all_connectors())


def test_the_logo_file_parses_into_every_entry():
    text = (UI / "brand-logos.ts").read_text()
    declared = text[text.index("export const LOGOS"):].count("viewBox:")
    assert declared == len(_logos()) >= 11


def test_every_connector_has_its_original_logo():
    logos, alias = _logos(), _record("ALIAS")
    names = _connectors()
    assert len(names) >= 11, "found too few connectors for the check to mean anything"
    missing = [n for n in names if n not in OKWAN_OWN and n not in AWAITING and alias.get(n, n) not in logos]
    assert not missing, (
        f"connectors without their logo: {missing}. Add each owner's original logo to "
        "apps/dashboard/app/_components/ui/brand-logos.ts (CLAUDE.md, standing workflow)")


def test_a_logo_that_arrives_leaves_the_waiting_list():
    logos, alias = _logos(), _record("ALIAS")
    arrived = [n for n in AWAITING if alias.get(n, n) in logos]
    assert not arrived, f"{arrived} have a logo now; take them out of AWAITING"
    unknown = set(AWAITING) - set(_connectors())
    assert not unknown, f"AWAITING names no connector: {unknown}"


def test_every_mcp_client_has_its_logo():
    logos = _logos()
    clients = _record("CLIENT_MARK")
    assert clients, "found no client marks"
    assert not [k for k, v in clients.items() if v not in logos]


@pytest.mark.parametrize("name", sorted(_logos()))
def test_each_logo_is_plain_static_svg(name):
    viewbox, body = _logos()[name]
    numbers = viewbox.split()
    assert len(numbers) == 4 and all(re.fullmatch(r"-?\d+(\.\d+)?", n) for n in numbers)
    assert float(numbers[2]) > 0 and float(numbers[3]) > 0
    lowered = body.lower()
    for banned in ("<script", "<foreignobject", "<image", "<use", "javascript:", "data:", "http:", "https:", "<style"):
        assert banned not in lowered, f"{name}'s logo carries {banned}"
    assert not re.search(r"\son[a-z]+\s*=", lowered), f"{name}'s logo carries an event handler"
    assert not re.search(r'\sid="(?!__ID\d+__")', body), f"{name}'s logo has a fixed id; use __ID<n>__"
