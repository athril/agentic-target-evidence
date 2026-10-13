# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for axis_evidence.py — the axis → evidence map and its floor rules."""

from __future__ import annotations

from pathlib import Path

from services.evidence.axis_evidence import axis_evidence_map, reload_axis_evidence

_CFG = """
exclude: ["0 matched"]
lenses:
  demo:
    legacy_list: [genetics]
    tokens_rule:
      types: [clinical_trial]
      definitive: {tokens: ["phase3"], floor: 0.85}
    numeric_rule:
      types: [clinical_trial]
      include: ["primary outcome"]
      definitive:
        - tokens: ["primary outcome"]
          pattern: 'p\\s*[=<]\\s*(\\d*\\.\\d+)'
          below: 0.05
          floor: 0.8
"""


def _map(tmp_path: Path):
    path = tmp_path / "axis_evidence.yaml"
    path.write_text(_CFG)
    reload_axis_evidence()
    return axis_evidence_map(path=path)


def test_bare_list_axis_still_supported(tmp_path):
    m = _map(tmp_path)
    assert m.types_for("demo", "legacy_list") == frozenset({"genetics"})
    assert m.describe("demo", "legacy_list") == "legacy list"


def test_include_and_exclude(tmp_path):
    m = _map(tmp_path)
    assert m.attaches("demo", "numeric_rule", "clinical_trial", "NCT1 primary outcome — x")
    assert not m.attaches("demo", "numeric_rule", "clinical_trial", "PHASE3 RECRUITING trial")
    assert not m.attaches("demo", "numeric_rule", "clinical_trial", "primary outcome, 0 matched")
    assert not m.attaches("demo", "numeric_rule", "genetics", "primary outcome")


def test_token_and_numeric_floor_rules(tmp_path):
    m = _map(tmp_path)
    assert m.floor_for("demo", "tokens_rule", "PHASE3 RECRUITING") == 0.85
    assert (
        m.floor_for("demo", "numeric_rule", "primary outcome — a vs b: p=0.0024; c vs b: p=0.79")
        == 0.8
    )
    assert m.floor_for("demo", "numeric_rule", "primary outcome — p<0.001") == 0.8
    assert m.floor_for("demo", "numeric_rule", "primary outcome — p=0.21") == 0.0
    assert m.floor_for("demo", "numeric_rule", "trial record p=0.01") == 0.0  # needs the token
    assert m.floor_for("demo", "unlisted", "anything") == 0.0
