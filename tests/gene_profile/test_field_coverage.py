# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Connector fields that nothing consumes.

Several defects of the gene profile were not reasoning errors: the datum was retrieved and
never shown. Boxed warnings, drug action types, trial eligibility criteria, modes of
inheritance and gnomAD's own quality flags were all parsed by a connector and dropped before
any reader saw them, each found only by someone reading a report closely.

This makes that class visible instead: every field a connector parses must be consumed by the
skill, by the pipeline, or listed in the baseline with a reason. It is a ratchet, not a
verdict — the baseline records what the 2026-11-02 audit found, and the test fails when the
backlog grows or when a listed field starts being used.

The match is a plain substring search over the consumers, which is deliberately generous: a
field reached by dict access (``data.get("mp_term_id")``) counts as consumed. So a field this
test reports is genuinely unreferenced, while a field it passes may still be unused.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_CONNECTORS = sorted((_ROOT / "src" / "mcp_servers").glob("*/tools.py"))
_BASELINE = _ROOT / "tests" / "fixtures" / "field_coverage_baseline.yaml"
# Dataclass/pydantic field declarations: four-space indent, snake_case, annotated, defaulted.
_FIELD = re.compile(r'^\s{4}([a-z][a-z0-9_]{4,}):\s*[A-Za-z\[\]|"\' .]+\s*=', re.M)
_CONSUMERS = (
    ".claude/skills/gene-profile/scripts/*.py",
    ".claude/skills/gene-profile/references/*.md",
    "src/services/**/*.py",
    "src/agents/**/*.py",
)


def _consumer_text() -> str:
    parts = []
    for pattern in _CONSUMERS:
        for path in _ROOT.glob(pattern):
            parts.append(path.read_text(encoding="utf8", errors="ignore"))
    return "\n".join(parts)


def unreferenced_fields() -> dict[str, set[str]]:
    """Per connector, the parsed fields no consumer mentions anywhere."""
    consumers = _consumer_text()
    out: dict[str, set[str]] = {}
    for path in _CONNECTORS:
        fields = set(_FIELD.findall(path.read_text(encoding="utf8")))
        if unused := {f for f in fields if f not in consumers}:
            out[path.parent.name] = unused
    return out


def _baseline() -> dict[str, dict[str, str]]:
    return yaml.safe_load(_BASELINE.read_text(encoding="utf8")) or {}


def test_no_new_connector_field_goes_unconsumed() -> None:
    """A new field must be rendered, read by the pipeline, or listed with a reason."""
    baseline, found = _baseline(), unreferenced_fields()
    new = {
        source: sorted(fields - set(baseline.get(source, {})))
        for source, fields in found.items()
        if fields - set(baseline.get(source, {}))
    }
    assert not new, (
        "connector fields nothing consumes, and not in the baseline:\n"
        + "\n".join(f"  {s}: {', '.join(f)}" for s, f in sorted(new.items()))
        + f"\n\nRender them, consume them, or add them to {_BASELINE.name} with a reason."
    )


def test_the_baseline_has_no_stale_entries() -> None:
    """A field that is now consumed leaves the baseline, so the file stays an honest backlog."""
    baseline, found = _baseline(), unreferenced_fields()
    stale = {
        source: sorted(set(fields) - found.get(source, set()))
        for source, fields in baseline.items()
        if set(fields) - found.get(source, set())
    }
    assert not stale, "baseline entries that are now consumed — remove them:\n" + "\n".join(
        f"  {s}: {', '.join(f)}" for s, f in sorted(stale.items())
    )


def test_every_baseline_entry_gives_a_reason() -> None:
    for source, fields in _baseline().items():
        for field, reason in fields.items():
            assert isinstance(reason, str) and reason.strip(), f"{source}.{field} has no reason"
