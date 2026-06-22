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
from pathlib import Path

import pytest

import mcp_servers.scimago.tools as scimago_tools
from mcp_servers.scimago.tools import SjrRecord, resolve_sjr

_LANCET = {"title": "The Lancet", "sjr": 14.821, "sjr_best_quartile": "Q1", "type": "journal"}
_LANCET_ID = {
    "title": "The Lancet Infectious Diseases",
    "sjr": 5.549,
    "sjr_best_quartile": "Q1",
    "type": "journal",
}
_NATURE = {"title": "Nature", "sjr": 17.0, "sjr_best_quartile": "Q1", "type": "journal"}
_GENERIC_Q1 = {
    "title": "Generic Q1 Journal",
    "sjr": 2.0,
    "sjr_best_quartile": "Q1",
    "type": "journal",
}
_CELL_CALCIUM = {"title": "Cell Calcium", "sjr": 1.545, "sjr_best_quartile": "Q1", "type": "journal"}
_MID_TIER = {"title": "Mid Tier Journal", "sjr": 0.9, "sjr_best_quartile": "Q3", "type": "journal"}
_LOW_TIER = {"title": "Low Tier Journal", "sjr": 0.1, "sjr_best_quartile": "Q4", "type": "journal"}

# `by_title` must carry every distinct journal (mirroring the real bundled index,
# where by_issn/by_title cover the same set), since the top-tier percentile cutoff
# is computed from `by_title`'s distinct SJR values alone — see
# `_load_top_tier_threshold`. With these 7 distinct values, a 3% cutoff keeps only
# the single highest (Nature, 17.0) as top-tier; Lancet's 14.821 sits just below
# it, so it still resolves at the flat Q1 score.
_FIXTURE_INDEX = {
    "year": 2025,
    "by_issn": {
        "01406736": _LANCET,
        "14744547": _LANCET_ID,
        "00280836": _NATURE,
        "11112222": _GENERIC_Q1,
        "12345670": _MID_TIER,
        "76543210": _LOW_TIER,
    },
    "by_title": {
        "lancet": _LANCET,
        "lancet infectious diseases": _LANCET_ID,
        "nature": _NATURE,
        "generic q1 journal": _GENERIC_Q1,
        "cell calcium": _CELL_CALCIUM,
        "mid tier journal": _MID_TIER,
        "low tier journal": _LOW_TIER,
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
    assert result.top_tier is False


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
    assert resolve_sjr(issn="1111-2222").sjr_score == pytest.approx(0.85)  # Q1, below top-tier
    assert resolve_sjr(issn="1234-5670").sjr_score == pytest.approx(0.4)  # Q3
    assert resolve_sjr(issn="7654-3210").sjr_score == pytest.approx(0.2)  # Q4


def test_resolve_sjr_top_tier_overrides_quartile_score():
    # Nature is the single highest raw SJR in the fixture, so it's the only
    # journal inside the 3% top-tier cutoff and scores 1.0 instead of the
    # flat Q1 score — Lancet, just below the cutoff, still gets the Q1 score.
    nature = resolve_sjr(issn="0028-0836")
    assert nature.sjr_quartile == "Q1"
    assert nature.top_tier is True
    assert nature.sjr_score == pytest.approx(1.0)

    lancet = resolve_sjr(issn="0140-6736")
    assert lancet.top_tier is False
    assert lancet.sjr_score == pytest.approx(0.85)


def test_resolve_sjr_ignores_malformed_issn():
    result = resolve_sjr(issn="not-an-issn", journal_title="Cell Calcium")
    assert result.matched is True
    assert result.match_type == "title"
