"""docs/audit cites code and tests for every claim; none of it may go stale.

Every repository path in backticks must exist, every `tests/<file>.py::<name>`
must name a test defined in that file, and every relative link must resolve.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACK = sorted((ROOT / "docs" / "audit").glob("*.md"))

_PATH = re.compile(r"`([\w./-]+\.(?:py|sql|yml|md|ts|toml|json))(?:::(\w+))?`")
_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def test_the_pack_exists():
    names = {p.name for p in PACK}
    assert {"README.md", "controls.md", "threat-model.md", "known-gaps.md"} <= names


@pytest.mark.parametrize("doc", PACK, ids=lambda p: p.name)
def test_every_cited_path_and_test_exists(doc):
    missing = []
    for path, test in _PATH.findall(doc.read_text()):
        target = ROOT / path
        if not target.exists():
            missing.append(path)
        elif test and not re.search(rf"^(?:async )?def {test}\(", target.read_text(), re.MULTILINE):
            missing.append(f"{path}::{test}")
    assert not missing, f"{doc.name} cites what does not exist: {missing}"


@pytest.mark.parametrize("doc", PACK, ids=lambda p: p.name)
def test_every_relative_link_resolves(doc):
    broken = [link for link in _LINK.findall(doc.read_text())
              if not link.startswith("http") and not (doc.parent / link).exists()]
    assert not broken, f"{doc.name} links to missing files: {broken}"


def test_every_control_names_a_proof():
    rows = [r for r in (ROOT / "docs" / "audit" / "controls.md").read_text().splitlines()
            if re.match(r"\| C\d+ \|", r)]
    assert len(rows) >= 20
    unproven = [r.split("|")[1].strip() for r in rows if not r.rstrip("| ").split("|")[-1].strip()]
    assert not unproven, f"controls with no proof: {unproven}"
