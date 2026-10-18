# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""NIH iCite article metrics (mcp_servers/icite/tools.py), against recorded-shape payloads."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_servers.icite import tools as icite

_URL = "https://icite.od.nih.gov/api/pubs"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    icite.reset_cache()
    monkeypatch.setenv("ICITE_ENABLED", "true")


def _row(pmid: int, rcr: float | None, **kw: object) -> dict[str, object]:
    return {
        "pmid": pmid,
        "year": 2018,
        "relative_citation_ratio": rcr,
        "citation_count": 700,
        "is_research_article": "Yes",
        "is_clinical": False,
        "human": 1.0,
        "animal": 0.0,
        "molecular_cellular": 0.0,
        "provisional": False,
        **kw,
    }


@respx.mock
async def test_metrics_are_parsed_and_a_pmid_icite_lacks_is_unmatched() -> None:
    route = respx.get(_URL).mock(
        return_value=httpx.Response(200, json={"data": [_row(1, 26.1), _row(3, None, year=2026)]})
    )

    found = await icite.article_metrics(["1", "2", "3"])

    assert route.call_count == 1
    assert found["1"].matched and found["1"].relative_citation_ratio == pytest.approx(26.1)
    assert found["1"].is_research_article is True
    assert found["1"].human == 1.0
    assert found["2"].matched is False  # iCite has no record for it
    assert found["3"].relative_citation_ratio is None  # too recent for a ratio


@respx.mock
async def test_lookups_are_cached_per_process_and_non_pmids_ignored() -> None:
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json={"data": [_row(1, 2.0)]}))

    await icite.article_metrics(["1", "not-a-pmid", ""])
    again = await icite.article_metrics(["1"])

    assert route.call_count == 1
    assert list(again) == ["1"]


@respx.mock
async def test_large_requests_are_split_into_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(icite, "_BATCH", 2)
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json={"data": []}))

    found = await icite.article_metrics(["1", "2", "3"])

    assert route.call_count == 2
    assert set(found) == {"1", "2", "3"}


@respx.mock
async def test_a_failed_request_is_not_cached_so_it_can_be_retried() -> None:
    route = respx.get(_URL)
    route.side_effect = [httpx.Response(404), httpx.Response(200, json={"data": [_row(1, 1.5)]})]

    assert await icite.article_metrics(["1"]) == {}
    retried = await icite.article_metrics(["1"])

    assert retried["1"].relative_citation_ratio == pytest.approx(1.5)


async def test_disabled_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICITE_ENABLED", "false")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(_URL)
        assert await icite.article_metrics(["1"]) == {}
    assert not route.called


@respx.mock
async def test_requests_stay_short_enough_for_the_server_to_accept() -> None:
    """587 PMIDs in one query string drew HTTP 413 (URI too long)."""
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json={"data": []}))

    await icite.article_metrics([str(10_000_000 + i) for i in range(587)])

    assert route.call_count == 3
    assert all(len(call.request.url.params["pmids"].split(",")) <= 200 for call in route.calls)
