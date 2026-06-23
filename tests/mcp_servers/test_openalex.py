# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the OpenAlex (CC0) journal-quality lookup — the commercial-safe
SJR fallback. HTTP is mocked with respx; no live OpenAlex calls."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_servers.openalex.tools import (
    OpenAlexJournal,
    _score_from_citedness,
    resolve_journal,
)

_BASE = "https://api.openalex.org"

_LANCET_SOURCE = {
    "id": "https://openalex.org/S49861241",
    "display_name": "The Lancet",
    "issn_l": "0140-6736",
    "works_count": 200_000,
    "is_in_doaj": False,
    "summary_stats": {"2yr_mean_citedness": 12.5, "h_index": 800},
}

_SMALL_SOURCE = {
    "id": "https://openalex.org/S999",
    "display_name": "Small Niche Journal",
    "issn_l": "1111-2222",
    "works_count": 50,
    "is_in_doaj": False,
    "summary_stats": {"2yr_mean_citedness": 1.2, "h_index": 4},
}


@pytest.fixture(autouse=True)
def _enable_openalex(monkeypatch):
    monkeypatch.setenv("OPENALEX_ENABLED", "true")
    monkeypatch.delenv("OPENALEX_MAILTO", raising=False)


@respx.mock
async def test_resolve_journal_matches_by_issn():
    respx.get(f"{_BASE}/sources/issn:0140-6736").mock(
        return_value=httpx.Response(200, json=_LANCET_SOURCE)
    )
    result = await resolve_journal(issn="0140-6736")
    assert isinstance(result, OpenAlexJournal)
    assert result.matched is True
    assert result.match_type == "issn"
    assert result.display_name == "The Lancet"
    assert result.two_yr_mean_citedness == pytest.approx(12.5)
    assert result.quality_score == pytest.approx(0.85)
    assert result.established is True  # high h-index


@respx.mock
async def test_resolve_journal_falls_back_to_title_search():
    respx.get(f"{_BASE}/sources/issn:9999-9999").mock(return_value=httpx.Response(404))
    respx.get(f"{_BASE}/sources").mock(
        return_value=httpx.Response(200, json={"results": [_LANCET_SOURCE]})
    )
    result = await resolve_journal(issn="9999-9999", journal_title="The Lancet")
    assert result.matched is True
    assert result.match_type == "title"
    assert result.display_name == "The Lancet"


@respx.mock
async def test_resolve_journal_unestablished_when_low_h_index_and_no_doaj():
    respx.get(f"{_BASE}/sources/issn:1111-2222").mock(
        return_value=httpx.Response(200, json=_SMALL_SOURCE)
    )
    result = await resolve_journal(issn="1111-2222")
    assert result.matched is True
    assert result.established is False
    assert result.quality_score == pytest.approx(0.2)


@respx.mock
async def test_resolve_journal_doaj_listed_is_established():
    source = {**_SMALL_SOURCE, "is_in_doaj": True}
    respx.get(f"{_BASE}/sources/issn:1111-2222").mock(return_value=httpx.Response(200, json=source))
    result = await resolve_journal(issn="1111-2222")
    assert result.established is True  # DOAJ listing overrides low h-index


@respx.mock
async def test_resolve_journal_no_match_returns_unmatched():
    respx.get(f"{_BASE}/sources/issn:0000-0000").mock(return_value=httpx.Response(404))
    respx.get(f"{_BASE}/sources").mock(return_value=httpx.Response(200, json={"results": []}))
    result = await resolve_journal(issn="0000-0000", journal_title="Nonexistent")
    assert result.matched is False
    assert result.quality_score is None


@respx.mock
async def test_resolve_journal_network_error_degrades_to_unmatched():
    respx.get(f"{_BASE}/sources/issn:0140-6736").mock(side_effect=httpx.ConnectError("down"))
    result = await resolve_journal(issn="0140-6736")
    assert result.matched is False


@respx.mock
async def test_resolve_journal_disabled_makes_no_http_call(monkeypatch):
    monkeypatch.setenv("OPENALEX_ENABLED", "false")
    # No routes registered: if a request were made, respx would raise.
    result = await resolve_journal(issn="0140-6736", journal_title="The Lancet")
    assert result.matched is False


def test_score_from_citedness_tiers():
    assert _score_from_citedness(20.0) == pytest.approx(0.85)
    assert _score_from_citedness(8.0) == pytest.approx(0.85)
    assert _score_from_citedness(5.0) == pytest.approx(0.6)
    assert _score_from_citedness(3.0) == pytest.approx(0.4)
    assert _score_from_citedness(0.5) == pytest.approx(0.2)
    assert _score_from_citedness(None) is None
