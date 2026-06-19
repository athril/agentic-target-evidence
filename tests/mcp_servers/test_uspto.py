# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for USPTO MCP tools (MP-26).

Targets the USPTO Open Data Portal (ODP) API used by mcp_servers/uspto/tools.py:
POST https://api.uspto.gov/api/v1/patent/applications/search (X-API-KEY auth).

Abstract text has no field in the ODP search response — it is filled in via
mcp_servers.uspto.abstract_pdf.fetch_abstract_pdf, which downloads and OCRs
the application's ABST document. That function's own behavior (PDF text
extraction, OCR fallback, cleanup) is covered by test_uspto_abstract_pdf.py;
here we only verify search_patents wires it in correctly.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_servers.uspto.tools import PatentRecord, search_patents
from schemas.evidence import DataClass

_ODP_SEARCH_URL = "https://api.uspto.gov/api/v1/patent/applications/search"

_ODP_RESPONSE = {
    "patentFileWrapperDataBag": [
        {
            "applicationNumberText": "16000001",
            "applicationMetaData": {
                "inventionTitle": "BRCA1 inhibitor composition",
                "filingDate": "2020-03-15",
                "grantDate": "2022-06-01",
                "patentNumber": "US10000001",
                "firstApplicantName": "AcmePharma Inc.",
            },
        }
    ],
    "count": 1,
}

_ODP_RESPONSE_NO_PATENT_NUM = {
    "patentFileWrapperDataBag": [
        {
            "applicationNumberText": "16000002",
            "applicationMetaData": {
                "inventionTitle": "BRCA1 cancer therapy",
                "firstApplicantName": "BetaPharma Inc.",
            },
        }
    ],
    "count": 1,
}


def _make_item(n: str) -> dict:
    return {
        "applicationNumberText": f"1600000{n}",
        "applicationMetaData": {
            "inventionTitle": f"Patent {n}",
            "patentNumber": f"US1000000{n}",
            "firstApplicantName": "TestCo",
        },
    }


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USPTO_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests shouldn't pay the real proactive inter-request delay."""
    import mcp_servers.uspto.tools as tools_mod

    monkeypatch.setattr(
        tools_mod, "_ENDPOINT_REQUESTS_PER_SECOND", {"search": 1e6, "documents": 1e6}
    )
    monkeypatch.setattr(tools_mod, "_last_request_at", {})


@pytest.fixture(autouse=True)
def _stub_abstract_pdf(monkeypatch: pytest.MonkeyPatch):
    """By default, skip the real PDF fetch — most tests here exercise search,
    not abstract extraction. Tests that care about abstract wiring override
    this via monkeypatch.setattr within the test body."""
    import mcp_servers.uspto.tools as tools_mod

    async def _stub(client: httpx.AsyncClient, app_num: str, key: str) -> str:
        return ""

    monkeypatch.setattr(tools_mod, "fetch_abstract_pdf", _stub)
    return _stub


# ---------------------------------------------------------------------------
# Search and pagination
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_patents_returns_records() -> None:
    respx.post(_ODP_SEARCH_URL).mock(return_value=httpx.Response(200, json=_ODP_RESPONSE))
    records = await search_patents("BRCA1", "breast cancer")

    assert len(records) == 1
    r = records[0]
    assert isinstance(r, PatentRecord)
    assert r.patent_id == "US10000001"
    assert r.app_number == "16000001"
    assert r.assignee == "AcmePharma Inc."
    assert r.filing_date == "2020-03-15"


@respx.mock
async def test_search_patents_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_servers.uspto.tools as tools_mod

    monkeypatch.setattr(tools_mod, "_PAGE_SIZE", 2)

    page1 = {"patentFileWrapperDataBag": [_make_item("1"), _make_item("2")], "count": 3}
    page2 = {"patentFileWrapperDataBag": [_make_item("3")], "count": 3}
    pages = iter([page1, page2])

    respx.post(_ODP_SEARCH_URL).mock(side_effect=lambda req: httpx.Response(200, json=next(pages)))
    records = await search_patents("BRCA1", "breast cancer")

    assert len(records) == 3
    assert {r.patent_id for r in records} == {"US10000001", "US10000002", "US10000003"}


@respx.mock
async def test_search_patents_respects_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_servers.uspto.tools as tools_mod

    monkeypatch.setattr(tools_mod, "_PAGE_SIZE", 2)
    monkeypatch.setattr(tools_mod, "_MAX_RESULTS", 2)

    page1 = {"patentFileWrapperDataBag": [_make_item("1"), _make_item("2")], "count": 10}
    respx.post(_ODP_SEARCH_URL).mock(return_value=httpx.Response(200, json=page1))

    records = await search_patents("BRCA1", "breast cancer")
    assert len(records) == 2


@respx.mock
async def test_search_patents_retries_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 on search triggers a single retry after the delay."""
    import mcp_servers.uspto.tools as tools_mod

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(tools_mod.asyncio, "sleep", fake_sleep)

    responses = iter([httpx.Response(429), httpx.Response(200, json=_ODP_RESPONSE)])
    respx.post(_ODP_SEARCH_URL).mock(side_effect=lambda req: next(responses))

    records = await search_patents("BRCA1", "breast cancer")

    assert len(records) == 1
    assert slept == [tools_mod._RETRY_429_DELAY]


# ---------------------------------------------------------------------------
# Abstract wiring (delegates to mcp_servers.uspto.abstract_pdf.fetch_abstract_pdf)
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_patents_fills_abstract_from_pdf_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_patents calls fetch_abstract_pdf per record and stores its result."""
    import mcp_servers.uspto.tools as tools_mod

    seen_app_nums: list[str] = []

    async def fake_fetch_abstract_pdf(client: httpx.AsyncClient, app_num: str, key: str) -> str:
        seen_app_nums.append(app_num)
        return f"abstract for {app_num}"

    monkeypatch.setattr(tools_mod, "fetch_abstract_pdf", fake_fetch_abstract_pdf)
    respx.post(_ODP_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_ODP_RESPONSE_NO_PATENT_NUM)
    )

    records = await search_patents("BRCA1", "breast cancer")

    assert seen_app_nums == ["16000002"]
    assert records[0].abstract == "abstract for 16000002"


@respx.mock
async def test_search_patents_abstract_fetch_failure_leaves_it_empty() -> None:
    """fetch_abstract_pdf returning "" (its own failure contract) does not raise."""
    respx.post(_ODP_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_ODP_RESPONSE_NO_PATENT_NUM)
    )

    records = await search_patents("BRCA1", "breast cancer")

    assert records[0].abstract == ""


# ---------------------------------------------------------------------------
# Google Patents abstract fallback
# ---------------------------------------------------------------------------

_GOOGLE_HTML_WITH_META = (
    '<meta name="description" content="A method for treating pancreatic cancer." />'
    "<title>US9717724B2</title>"
)


@respx.mock
async def test_search_patents_does_not_scrape_google_patents_by_default() -> None:
    """Google Patents abstract scraping is disabled by default (legal/ToS reasons,
    see NOTICE.md); missing abstracts stay empty rather than falling back to it."""
    respx.post(_ODP_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_ODP_RESPONSE_NO_PATENT_NUM)
    )
    google_route = respx.get("https://patents.google.com/patent/US10000002/en").mock(
        return_value=httpx.Response(200, text=_GOOGLE_HTML_WITH_META)
    )

    records = await search_patents("BRCA1", "breast cancer")

    assert records[0].abstract == ""
    assert google_route.call_count == 0


# ---------------------------------------------------------------------------
# Classification and error handling
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_patents_classification_is_non_sensitive() -> None:
    respx.post(_ODP_SEARCH_URL).mock(return_value=httpx.Response(200, json=_ODP_RESPONSE))
    records = await search_patents("BRCA1", "breast cancer")
    assert all(r.classification == DataClass.NON_SENSITIVE for r in records)


@respx.mock
async def test_search_patents_empty_response() -> None:
    respx.post(_ODP_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"patentFileWrapperDataBag": None, "count": 0})
    )
    records = await search_patents("ZZZNOGENE", "ZZZNO_DISEASE")
    assert records == []


@respx.mock
async def test_search_patents_raises_on_api_error() -> None:
    from core.exceptions import MCPToolError

    respx.post(_ODP_SEARCH_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(MCPToolError):
        await search_patents("BRCA1", "breast cancer")


@respx.mock
async def test_search_patents_raises_on_invalid_key() -> None:
    from core.exceptions import MCPToolError

    respx.post(_ODP_SEARCH_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(MCPToolError):
        await search_patents("BRCA1", "breast cancer")
