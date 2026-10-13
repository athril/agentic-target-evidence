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
    import mcp_servers.openalex.tools as oa

    monkeypatch.setenv("OPENALEX_ENABLED", "true")
    monkeypatch.delenv("OPENALEX_MAILTO", raising=False)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    # Per-process lookup cache and budget breaker: isolate every test.
    oa._LOOKUP_CACHE.clear()
    monkeypatch.setattr(oa, "_blocked_until", 0.0)


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


async def test_concurrent_lookups_are_capped_and_rate_limits_retried(monkeypatch):
    """A burst of lookups (one per paper) stays under OpenAlex's concurrency limit, and a
    429 is retried instead of dropping the journal's quality signal."""
    import asyncio

    import core.http as http_mod
    import mcp_servers.openalex.tools as oa

    monkeypatch.setattr(oa, "_MAX_CONCURRENCY", 3)

    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(http_mod.asyncio, "sleep", _no_sleep)
    in_flight = peak = 0
    first: set[str] = set()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        title = request.url.params.get("search", "")
        if title not in first:  # every journal is rate-limited once
            first.add(title)
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"results": [_SMALL_SOURCE]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await asyncio.gather(
            *(resolve_journal(journal_title=f"Journal {i}", client=client) for i in range(20))
        )

    assert peak <= 3
    assert all(r.matched for r in results)


@respx.mock
async def test_lookup_failure_log_names_the_status(caplog):
    respx.get(f"{_BASE}/sources").mock(return_value=httpx.Response(500))
    result = await resolve_journal(journal_title="Some Journal")
    assert not result.matched
    assert "HTTP 500" in caplog.text


@respx.mock
async def test_repeated_journal_is_looked_up_once():
    """One journal per paper: a run pays once per journal, not once per paper."""
    route = respx.get(f"{_BASE}/sources").mock(
        return_value=httpx.Response(200, json={"results": [_SMALL_SOURCE]})
    )
    for _ in range(5):
        assert (await resolve_journal(journal_title="Small  Niche journal")).matched
    assert route.call_count == 1


@respx.mock
async def test_spent_budget_opens_the_circuit(caplog):
    """A 429 with a Retry-After of hours is a spent daily budget: warn once, then skip
    further lookups instead of failing (and retrying) each in turn."""
    route = respx.get(f"{_BASE}/sources").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "76078"})
    )
    results = [await resolve_journal(journal_title=f"Journal {i}") for i in range(5)]
    assert not any(r.matched for r in results)
    assert route.call_count == 1
    assert caplog.text.count("budget exhausted") == 1
    assert "OPENALEX_API_KEY" in caplog.text


@respx.mock
async def test_api_key_is_sent_when_configured(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "k-123")
    route = respx.get(f"{_BASE}/sources", params={"api_key": "k-123"}).mock(
        return_value=httpx.Response(200, json={"results": [_SMALL_SOURCE]})
    )
    assert (await resolve_journal(journal_title="Small Niche Journal")).matched
    assert route.called
