# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for OpenFDA MCP tools (drug labels + FAERS adverse events)."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.openfda.tools import (
    AdverseEventBundle,
    DrugLabelRecord,
    TopReaction,
    search_adverse_events,
    search_drug_labels,
)

_LABELS_URL = "https://api.fda.gov/drug/label.json"
_EVENTS_URL = "https://api.fda.gov/drug/event.json"

# ── Sample API payloads ──────────────────────────────────────────────────────

_LABEL_RESULT = {
    "set_id": "abc123",
    "openfda": {
        "generic_name": ["METFORMIN"],
        "brand_name": ["GLUCOPHAGE"],
        "product_type": ["HUMAN PRESCRIPTION DRUG"],
        "application_number": ["NDA020357"],
    },
    "indications_and_usage": ["For treatment of type 2 diabetes mellitus."],
    "mechanism_of_action": ["Decreases hepatic glucose production via AMPK activation."],
    "warnings": ["Lactic acidosis risk in renal impairment."],
    "boxed_warning": ["LACTIC ACIDOSIS: Rare but serious complication."],
    "contraindications": ["eGFR < 30 mL/min/1.73 m2."],
    "adverse_reactions": ["Diarrhea, nausea, vomiting."],
}

_LABELS_RESPONSE = {"results": [_LABEL_RESULT]}

_EVENTS_TOTAL = {"meta": {"results": {"total": 85000}}, "results": []}
_EVENTS_SERIOUS = {"meta": {"results": {"total": 31200}}, "results": []}
_EVENTS_DEATH = {"meta": {"results": {"total": 4100}}, "results": []}
_EVENTS_REACTIONS = {
    "results": [
        {"term": "NAUSEA", "count": 9500},
        {"term": "DIARRHOEA", "count": 7300},
        {"term": "LACTIC ACIDOSIS", "count": 850},
    ]
}

_SEARCH_TERM = 'patient.drug.openfda.generic_name:"METFORMIN"'


def _mock_labels(moa_resp=None, ind_resp=None, gene="PTPN1", indication="type 2 diabetes"):
    """Register respx mocks for all label searches (MoA + full indication + broad fallback)."""
    from mcp_servers.openfda.tools import _broad_indication

    moa_json = moa_resp if moa_resp is not None else _LABELS_RESPONSE
    ind_json = ind_resp if ind_resp is not None else {"results": []}

    respx.get(
        _LABELS_URL,
        params={"search": f'mechanism_of_action:"{gene}"', "limit": "10"},
    ).mock(return_value=httpx.Response(200, json=moa_json))
    respx.get(
        _LABELS_URL,
        params={"search": f'indications_and_usage:"{indication}"', "limit": "10"},
    ).mock(return_value=httpx.Response(200, json=ind_json))
    broad = _broad_indication(indication)
    if broad:
        respx.get(
            _LABELS_URL,
            params={"search": f'indications_and_usage:"{broad}"', "limit": "10"},
        ).mock(return_value=httpx.Response(200, json={"results": []}))


def _mock_events():
    """Register respx mocks for all 4 FAERS sub-queries."""
    respx.get(_EVENTS_URL, params={"search": _SEARCH_TERM, "limit": "1"}).mock(
        return_value=httpx.Response(200, json=_EVENTS_TOTAL)
    )
    respx.get(
        _EVENTS_URL,
        params={"search": f"{_SEARCH_TERM} AND serious:1", "limit": "1"},
    ).mock(return_value=httpx.Response(200, json=_EVENTS_SERIOUS))
    respx.get(
        _EVENTS_URL,
        params={"search": f"{_SEARCH_TERM} AND seriousnessdeath:1", "limit": "1"},
    ).mock(return_value=httpx.Response(200, json=_EVENTS_DEATH))
    respx.get(
        _EVENTS_URL,
        params={
            "search": _SEARCH_TERM,
            "count": "patient.reaction.reactionmeddrapt.exact",
            "limit": "25",
        },
    ).mock(return_value=httpx.Response(200, json=_EVENTS_REACTIONS))


# ── search_drug_labels ───────────────────────────────────────────────────────


@respx.mock
async def test_search_drug_labels_parses_label_fields() -> None:
    _mock_labels()
    results = await search_drug_labels("PTPN1", "type 2 diabetes")

    assert len(results) == 1
    rec = results[0]
    assert isinstance(rec, DrugLabelRecord)
    assert rec.drug_name == "METFORMIN"
    assert rec.brand_names == ["GLUCOPHAGE"]
    assert rec.application_number == "NDA020357"
    assert "lactic acidosis" in rec.boxed_warning.lower()
    assert "AMPK" in rec.mechanism_of_action
    assert "type 2 diabetes" in rec.indications_and_usage.lower()
    assert "NDA020357" in rec.source_link or "abc123" in rec.source_link


@respx.mock
async def test_search_drug_labels_deduplicates_across_searches() -> None:
    """Same drug returned by both MoA and indication searches → one record."""
    _mock_labels(moa_resp=_LABELS_RESPONSE, ind_resp=_LABELS_RESPONSE)
    results = await search_drug_labels("PTPN1", "type 2 diabetes")

    assert len(results) == 1
    assert results[0].drug_name == "METFORMIN"


@respx.mock
async def test_search_drug_labels_merges_distinct_drugs() -> None:
    """Different drugs from each search are both returned."""
    second_label = {
        "set_id": "xyz789",
        "openfda": {
            "generic_name": ["SITAGLIPTIN"],
            "brand_name": ["JANUVIA"],
            "product_type": ["HUMAN PRESCRIPTION DRUG"],
            "application_number": ["NDA021995"],
        },
        "indications_and_usage": ["Type 2 diabetes treatment via DPP-4 inhibition."],
        "mechanism_of_action": ["DPP-4 inhibitor."],
    }
    _mock_labels(moa_resp=_LABELS_RESPONSE, ind_resp={"results": [second_label]})
    results = await search_drug_labels("PTPN1", "type 2 diabetes")

    names = {r.drug_name for r in results}
    assert "METFORMIN" in names
    assert "SITAGLIPTIN" in names


@respx.mock
async def test_search_drug_labels_empty_results() -> None:
    _mock_labels(moa_resp={"results": []}, ind_resp={"results": []})
    results = await search_drug_labels("PTPN1", "type 2 diabetes")
    assert results == []


_NOT_FOUND = httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
_EMPTY = httpx.Response(200, json={"results": []})


def _mock_all_label_routes(moa=None, ind=None, broad=None):
    """Register all three label search routes explicitly."""
    respx.get(_LABELS_URL, params={"search": 'mechanism_of_action:"PTPN1"', "limit": "10"}).mock(
        return_value=moa or _EMPTY
    )
    respx.get(
        _LABELS_URL,
        params={"search": 'indications_and_usage:"type 2 diabetes"', "limit": "10"},
    ).mock(return_value=ind or _EMPTY)
    respx.get(
        _LABELS_URL,
        params={"search": 'indications_and_usage:"diabetes"', "limit": "10"},
    ).mock(return_value=broad or _EMPTY)


@respx.mock
async def test_search_drug_labels_404_treated_as_empty() -> None:
    _mock_all_label_routes(moa=_NOT_FOUND, ind=_NOT_FOUND, broad=_NOT_FOUND)
    results = await search_drug_labels("PTPN1", "type 2 diabetes")
    assert results == []


@respx.mock
async def test_search_drug_labels_raises_on_server_error() -> None:
    _mock_all_label_routes(moa=httpx.Response(500))
    with pytest.raises(MCPToolError, match="HTTP 500"):
        await search_drug_labels("PTPN1", "type 2 diabetes")


# ── search_adverse_events ────────────────────────────────────────────────────


@respx.mock
async def test_search_adverse_events_counts_and_rates() -> None:
    _mock_events()
    bundle = await search_adverse_events("METFORMIN")

    assert isinstance(bundle, AdverseEventBundle)
    assert bundle.drug_name == "METFORMIN"
    assert bundle.total_reports == 85000
    assert bundle.serious_reports == 31200
    assert bundle.death_reports == 4100
    assert bundle.serious_rate == pytest.approx(31200 / 85000, rel=1e-3)
    assert bundle.death_rate == pytest.approx(4100 / 85000, rel=1e-3)


@respx.mock
async def test_search_adverse_events_top_reactions() -> None:
    _mock_events()
    bundle = await search_adverse_events("METFORMIN")

    assert len(bundle.top_reactions) == 3
    assert isinstance(bundle.top_reactions[0], TopReaction)
    assert bundle.top_reactions[0].reaction == "NAUSEA"
    assert bundle.top_reactions[0].count == 9500
    assert bundle.top_reactions[2].reaction == "LACTIC ACIDOSIS"


@respx.mock
async def test_search_adverse_events_text_summary() -> None:
    _mock_events()
    bundle = await search_adverse_events("METFORMIN")

    assert "85,000" in bundle.text
    assert "NAUSEA" in bundle.text
    assert bundle.source_link != ""


@respx.mock
async def test_search_adverse_events_404_returns_empty_bundle() -> None:
    for endpoint in [
        {"search": _SEARCH_TERM, "limit": "1"},
        {"search": f"{_SEARCH_TERM} AND serious:1", "limit": "1"},
        {"search": f"{_SEARCH_TERM} AND seriousnessdeath:1", "limit": "1"},
        {"search": _SEARCH_TERM, "count": "patient.reaction.reactionmeddrapt.exact", "limit": "25"},
    ]:
        respx.get(_EVENTS_URL, params=endpoint).mock(
            return_value=httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        )
    bundle = await search_adverse_events("METFORMIN")
    assert bundle.total_reports == 0
    assert bundle.top_reactions == []
    assert "No FAERS" in bundle.text


@respx.mock
async def test_search_adverse_events_raises_on_server_error() -> None:
    respx.get(_EVENTS_URL, params={"search": _SEARCH_TERM, "limit": "1"}).mock(
        return_value=httpx.Response(500)
    )
    # Other calls can succeed — only total_resp triggers the error check
    for params in [
        {"search": f"{_SEARCH_TERM} AND serious:1", "limit": "1"},
        {"search": f"{_SEARCH_TERM} AND seriousnessdeath:1", "limit": "1"},
        {"search": _SEARCH_TERM, "count": "patient.reaction.reactionmeddrapt.exact", "limit": "25"},
    ]:
        respx.get(_EVENTS_URL, params=params).mock(
            return_value=httpx.Response(200, json={"results": []})
        )
    with pytest.raises(MCPToolError, match="HTTP 500"):
        await search_adverse_events("METFORMIN")


@respx.mock
async def test_search_adverse_events_zero_total_no_rates() -> None:
    """Zero total reports → no serious_rate or death_rate (avoid division by zero)."""
    zero = {"meta": {"results": {"total": 0}}, "results": []}
    respx.get(_EVENTS_URL, params={"search": _SEARCH_TERM, "limit": "1"}).mock(
        return_value=httpx.Response(200, json=zero)
    )
    for params in [
        {"search": f"{_SEARCH_TERM} AND serious:1", "limit": "1"},
        {"search": f"{_SEARCH_TERM} AND seriousnessdeath:1", "limit": "1"},
        {"search": _SEARCH_TERM, "count": "patient.reaction.reactionmeddrapt.exact", "limit": "25"},
    ]:
        respx.get(_EVENTS_URL, params=params).mock(
            return_value=httpx.Response(200, json={"results": []})
        )
    bundle = await search_adverse_events("METFORMIN")
    assert bundle.total_reports == 0
    assert bundle.serious_rate is None
    assert bundle.death_rate is None
