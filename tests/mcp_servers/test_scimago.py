# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the SCImago SJR lookup MCP tools.

The bundled index is a static file built by scripts/build_scimago_index.py.
These tests point the module at a small synthetic index (via the `_INDEX_PATH`
boundary) rather than the real ~32K-journal bundle, to stay fast and decoupled
from the actual SCImago data, which is refreshed independently.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from pathlib import Path

import pytest

import mcp_servers.scimago.tools as scimago_tools
from mcp_servers.scimago.tools import SjrRecord, resolve_sjr

_FIXTURE_INDEX = {
    "year": 2025,
    "by_issn": {
        "01406736": {
            "title": "The Lancet",
            "sjr": 14.821,
            "sjr_best_quartile": "Q1",
            "type": "journal",
        },
        "14744547": {
            "title": "The Lancet Infectious Diseases",
            "sjr": 5.549,
            "sjr_best_quartile": "Q1",
            "type": "journal",
        },
        "00280836": {"title": "Nature", "sjr": 17.0, "sjr_best_quartile": "Q1", "type": "journal"},
        "12345670": {
            "title": "Mid Tier Journal",
            "sjr": 0.9,
            "sjr_best_quartile": "Q3",
            "type": "journal",
        },
        "76543210": {
            "title": "Low Tier Journal",
            "sjr": 0.1,
            "sjr_best_quartile": "Q4",
            "type": "journal",
        },
    },
    "by_title": {
        "lancet": {
            "title": "The Lancet",
            "sjr": 14.821,
            "sjr_best_quartile": "Q1",
            "type": "journal",
        },
        "cell calcium": {
            "title": "Cell Calcium",
            "sjr": 1.545,
            "sjr_best_quartile": "Q1",
            "type": "journal",
        },
    },
}


@pytest.fixture(autouse=True)
def _fixture_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The bundled SJR data is non-commercial-licensed and gated off by default
    # (SCIMAGO_SJR_ENABLED); enable it so these resolution tests exercise the
    # lookup path. The disabled path has its own dedicated test below.
    monkeypatch.setenv("SCIMAGO_SJR_ENABLED", "true")

    index_path = tmp_path / "scimago_test.json.gz"
    with gzip.open(index_path, "wt", encoding="utf-8") as f:
        json.dump(_FIXTURE_INDEX, f)

    original_path = scimago_tools._INDEX_PATH
    scimago_tools._INDEX_PATH = index_path
    scimago_tools._index = None
    yield
    scimago_tools._INDEX_PATH = original_path
    scimago_tools._index = None


def test_resolve_sjr_disabled_returns_unmatched(monkeypatch: pytest.MonkeyPatch):
    # Default/commercial posture: flag off -> never resolves, even on an exact ISSN.
    monkeypatch.setenv("SCIMAGO_SJR_ENABLED", "false")
    result = resolve_sjr(issn="0140-6736", journal_title="The Lancet")
    assert result.matched is False
    assert result.sjr_score is None
    assert result.sjr_quartile is None


def test_resolve_sjr_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    # Unset env var must also be treated as disabled.
    monkeypatch.delenv("SCIMAGO_SJR_ENABLED", raising=False)
    assert resolve_sjr(issn="0140-6736").matched is False


def test_resolve_sjr_matches_by_issn():
    result = resolve_sjr(issn="0140-6736")
    assert isinstance(result, SjrRecord)
    assert result.matched is True
    assert result.match_type == "issn"
    assert result.matched_title == "The Lancet"
    assert result.sjr_quartile == "Q1"
    assert result.sjr_score == pytest.approx(0.85)


def test_resolve_sjr_matches_by_essn_when_issn_misses():
    result = resolve_sjr(issn="", essn="1474-4547")
    assert result.matched is True
    assert result.matched_title == "The Lancet Infectious Diseases"


def test_resolve_sjr_falls_back_to_title():
    result = resolve_sjr(issn="", journal_title="The Lancet")
    assert result.matched is True
    assert result.match_type == "title"
    assert result.matched_title == "The Lancet"


def test_resolve_sjr_title_normalizes_leading_the_and_case():
    result = resolve_sjr(journal_title="lancet")
    assert result.matched is True
    assert result.matched_title == "The Lancet"


def test_resolve_sjr_no_match_returns_unmatched():
    result = resolve_sjr(issn="9999-9999", journal_title="Nonexistent Journal")
    assert result.matched is False
    assert result.sjr_score is None
    assert result.sjr_quartile is None


def test_resolve_sjr_quartile_score_mapping():
    assert resolve_sjr(issn="0028-0836").sjr_score == pytest.approx(0.85)  # Q1
    assert resolve_sjr(issn="1234-5670").sjr_score == pytest.approx(0.4)  # Q3
    assert resolve_sjr(issn="7654-3210").sjr_score == pytest.approx(0.2)  # Q4


def test_resolve_sjr_ignores_malformed_issn():
    result = resolve_sjr(issn="not-an-issn", journal_title="Cell Calcium")
    assert result.matched is True
    assert result.match_type == "title"
