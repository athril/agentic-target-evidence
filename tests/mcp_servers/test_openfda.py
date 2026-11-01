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
    IndicationDrugLandscape,
    TopReaction,
    count_indication_drugs,
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
    # The broad single-word search only fires when the full-phrase search finds nothing.
    if broad and not ind_json.get("results"):
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
async def test_search_drug_labels_skips_broad_search_when_phrase_hits() -> None:
    """A full-phrase hit suppresses the single-word fallback ("focal" → anti-epileptics).

    No broad route is registered, so a broad request would fail as unmocked.
    """
    _mock_labels(moa_resp={"results": []}, ind_resp=_LABELS_RESPONSE)
    results = await search_drug_labels("PTPN1", "type 2 diabetes")

    assert [r.drug_name for r in results] == ["METFORMIN"]


@respx.mock
async def test_search_drug_labels_uses_broad_search_on_phrase_miss() -> None:
    _mock_all_label_routes(broad=httpx.Response(200, json=_LABELS_RESPONSE))
    results = await search_drug_labels("PTPN1", "type 2 diabetes")

    assert [r.drug_name for r in results] == ["METFORMIN"]
    assert respx.routes[2].called


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


# ── count_indication_drugs ───────────────────────────────────────────────────


def _phrase_params(indication: str) -> dict:
    return {
        "search": (
            f'indications_and_usage:"{indication}" AND '
            'openfda.product_type:"HUMAN PRESCRIPTION DRUG"'
        ),
        "limit": "10",
    }


def _broad_params(broad: str) -> dict:
    return {
        "search": (
            f'indications_and_usage:"{broad}" AND openfda.product_type:"HUMAN PRESCRIPTION DRUG"'
        ),
        "limit": "10",
    }


@respx.mock
async def test_count_indication_drugs_phrase_hit() -> None:
    respx.get(_LABELS_URL, params=_phrase_params("type 2 diabetes")).mock(
        return_value=httpx.Response(200, json=_LABELS_RESPONSE)
    )
    bundle = await count_indication_drugs("type 2 diabetes")

    assert isinstance(bundle, IndicationDrugLandscape)
    assert bundle.mapping == "phrase"
    assert bundle.approved_drug_count == 1
    assert bundle.drugs == ["METFORMIN"]
    assert "AMPK" in bundle.moa_examples[0]
    assert "1 approved drug" in bundle.text
    assert "METFORMIN" in bundle.text


@respx.mock
async def test_count_indication_drugs_phrase_miss_falls_back_to_broad() -> None:
    respx.get(_LABELS_URL, params=_phrase_params("type 2 diabetes")).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.get(_LABELS_URL, params=_broad_params("diabetes")).mock(
        return_value=httpx.Response(200, json=_LABELS_RESPONSE)
    )
    bundle = await count_indication_drugs("type 2 diabetes")

    assert bundle.mapping == "broad"
    assert bundle.approved_drug_count == 1
    assert bundle.drugs == ["METFORMIN"]


@respx.mock
async def test_count_indication_drugs_dedup_by_generic_name() -> None:
    duplicate = {**_LABEL_RESULT, "set_id": "dup999"}
    respx.get(_LABELS_URL, params=_phrase_params("type 2 diabetes")).mock(
        return_value=httpx.Response(200, json={"results": [_LABEL_RESULT, duplicate]})
    )
    bundle = await count_indication_drugs("type 2 diabetes")

    assert bundle.approved_drug_count == 1
    assert bundle.drugs == ["METFORMIN"]


@respx.mock
async def test_count_indication_drugs_404_returns_none_mapping() -> None:
    respx.get(_LABELS_URL, params=_phrase_params("type 2 diabetes")).mock(
        return_value=httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
    )
    respx.get(_LABELS_URL, params=_broad_params("diabetes")).mock(
        return_value=httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
    )
    bundle = await count_indication_drugs("type 2 diabetes")

    assert bundle.mapping == "none"
    assert bundle.approved_drug_count == 0
    assert bundle.drugs == []
    assert bundle.text == ""


@respx.mock
async def test_count_indication_drugs_single_word_indication_no_broad_fallback() -> None:
    """A single-word indication has no `_broad_indication` fallback — only the
    phrase route is queried."""
    respx.get(_LABELS_URL, params=_phrase_params("diabetes")).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    bundle = await count_indication_drugs("diabetes")

    assert bundle.mapping == "none"
    assert bundle.approved_drug_count == 0


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


def _label(name: str, indication: str) -> dict:
    return {
        "set_id": f"set-{name}",
        "openfda": {
            "generic_name": [name],
            "brand_name": [name.title()],
            "product_type": ["HUMAN PRESCRIPTION DRUG"],
            "application_number": ["NDA000001"],
        },
        "indications_and_usage": [indication],
        "mechanism_of_action": ["Acts on a pathway."],
    }


@respx.mock
async def test_count_indication_drugs_skips_labels_that_deny_the_indication() -> None:
    """A label naming the disease only to say efficacy is not established is not a
    competitor in that disease (openFDA's phrase search matches it all the same)."""
    respx.get(_LABELS_URL, params=_phrase_params("ulcerative colitis")).mock(
        return_value=httpx.Response(
            200,
            json={
                "meta": {"results": {"total": 2}},
                "results": [
                    _label(
                        "DRUG-ONE", "DRUG-ONE is indicated for mild to moderate ulcerative colitis."
                    ),
                    _label(
                        "DRUG-TWO",
                        "DRUG-TWO is indicated for spasticity. The efficacy in stroke and "
                        "ulcerative colitis has not been established; it is not recommended.",
                    ),
                ],
            },
        )
    )
    bundle = await count_indication_drugs("ulcerative colitis")

    assert bundle.drugs == ["DRUG-ONE"]
    assert bundle.approved_drug_count == 1
    assert not bundle.capped  # filtered, not truncated by the page size


def _app_label(name: str, indication: str, app: str) -> dict:
    return {
        **_label(name, indication),
        "openfda": {**_label(name, indication)["openfda"], "application_number": [app]},
    }


@respx.mock
async def test_a_diagnostic_label_is_not_an_indication_drug() -> None:
    """CETP × coronary artery disease: gadobutrol, an MRI contrast agent, read as a drug
    approved for coronary artery disease."""
    contrast = _label(
        "GADOBUTROL",
        "Gadobutrol is a contrast agent indicated for magnetic resonance imaging to assess coronary artery disease.",
    )
    treatment = _label("SITAGLIPTIN", "Indicated to treat type 2 diabetes.")
    _mock_labels(moa_resp={"results": []}, ind_resp={"results": [contrast, treatment]})
    results = await search_drug_labels("PTPN1", "type 2 diabetes")
    assert [r.drug_name for r in results] == ["SITAGLIPTIN"]


@respx.mock
async def test_the_originators_label_is_preferred_over_a_generics() -> None:
    generic = _app_label("DONEPEZIL", "For dementia of the Alzheimer's type.", "ANDA202782")
    originator = _app_label("DONEPEZIL", "For dementia of the Alzheimer's type.", "NDA020690")
    _mock_labels(moa_resp={"results": []}, ind_resp={"results": [generic, originator]})
    results = await search_drug_labels("PTPN1", "type 2 diabetes")
    assert [r.application_number for r in results] == ["NDA020690"]


@respx.mock
async def test_the_diseases_synonyms_are_searched() -> None:
    """HSD17B13 × non-alcoholic steatohepatitis found no label: approved labels write
    "nonalcoholic steatohepatitis"."""
    from mcp_servers.openfda.tools import _phrase_query, _search_phrases

    phrases = _search_phrases("type 2 diabetes", ["diabetes mellitus type 2"])
    assert phrases[0] == "type 2 diabetes" and "diabetes mellitus type 2" in phrases
    respx.get(_LABELS_URL, params={"search": 'mechanism_of_action:"PTPN1"', "limit": "10"}).mock(
        return_value=_EMPTY
    )
    respx.get(_LABELS_URL, params={"search": _phrase_query(phrases), "limit": "10"}).mock(
        return_value=httpx.Response(200, json=_LABELS_RESPONSE)
    )
    results = await search_drug_labels("PTPN1", "type 2 diabetes", ["diabetes mellitus type 2"])
    assert [r.drug_name for r in results] == ["METFORMIN"]


# An unharmonised label (empty ``openfda`` block), as openFDA served Repatha's.
_UNHARMONISED_LABEL = {
    "set_id": "cd61e902",
    "openfda": {},
    "spl_product_data_elements": [
        "REPATHA Evolocumab EVOLOCUMAB EVOLOCUMAB PROLINE ACETATE ION POLYSORBATE 80 WATER"
    ],
    "description": ["11 DESCRIPTION Evolocumab is a human monoclonal immunoglobulin G2."],
    "mechanism_of_action": ["Evolocumab is a human monoclonal antibody that binds PCSK9."],
    "indications_and_usage": ["To reduce the risk of myocardial infarction and stroke."],
}


@respx.mock
async def test_a_label_without_a_generic_name_is_named_from_its_own_text() -> None:
    """openFDA had not harmonised Repatha's labels, so they had no generic name and every
    search dropped them."""
    _mock_labels(moa_resp={"results": [_UNHARMONISED_LABEL]}, ind_resp={"results": []})
    results = await search_drug_labels("PTPN1", "type 2 diabetes")
    assert [r.drug_name for r in results] == ["EVOLOCUMAB"]


@pytest.mark.parametrize(
    ("product", "prose", "name"),
    [
        # A salt: the ingredient is followed by its moiety.
        (
            "Metoprolol tartrate Metoprolol tartrate METOPROLOL TARTRATE METOPROLOL LACTOSE",
            "Metoprolol tartrate, USP, is a selective beta1-adrenoreceptor blocking agent.",
            "METOPROLOL TARTRATE",
        ),
        # The proprietary name the prose opens with is not an ingredient.
        (
            "WEGOVY semaglutide SEMAGLUTIDE SEMAGLUTIDE SODIUM PHOSPHATE PROPYLENE GLYCOL",
            "WEGOVY (semaglutide) injection contains semaglutide, a GLP-1 receptor agonist.",
            "SEMAGLUTIDE",
        ),
        # Prose and product data that share no ingredient give no name.
        ("Atenolol Atenolol ANHYDROUS CITRIC ACID", "Each tablet is white.", ""),
    ],
)
def test_the_unharmonised_name_is_an_ingredient_the_prose_uses(
    product: str, prose: str, name: str
) -> None:
    from mcp_servers.openfda.tools import _unharmonised_drug_name

    label = {"spl_product_data_elements": [product], "description": [prose]}
    assert _unharmonised_drug_name(label) == name


@respx.mock
async def test_named_drugs_find_labels_without_a_generic_name() -> None:
    from mcp_servers.openfda.tools import search_labels_for_drugs

    query = (
        '(openfda.generic_name:"EVOLOCUMAB" OR '
        '(_missing_:openfda.generic_name AND spl_product_data_elements:"EVOLOCUMAB"))'
    )
    respx.get(_LABELS_URL, params={"search": query}).mock(
        return_value=httpx.Response(200, json={"results": [_UNHARMONISED_LABEL]})
    )
    results = await search_labels_for_drugs(["EVOLOCUMAB"])
    assert [r.drug_name for r in results] == ["EVOLOCUMAB"]


_DRUGSFDA_URL = "https://api.fda.gov/drug/drugsfda.json"


def _application(number: str, day: str, brand: str, *, status: str = "AP") -> dict[str, object]:
    return {
        "application_number": number,
        "sponsor_name": "ACME",
        "submissions": [
            {"submission_type": "SUPPL", "submission_status": "AP",
             "submission_status_date": "20010101"},
            {"submission_type": "ORIG", "submission_status": status,
             "submission_status_date": day},
        ],
        "products": [{"brand_name": brand}],
    }  # fmt: skip


@respx.mock
async def test_fda_approval_reads_the_original_application() -> None:
    from mcp_servers.openfda.tools import search_fda_approval

    respx.get(_DRUGSFDA_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    _application("ANDA090000", "20050101", "GENERIC"),
                    _application("NDA200000", "20140310", "LATER"),
                    _application("NDA100000", "20120715", "FIRST"),
                    _application("NDA300000", "20100101", "TENTATIVE", status="TA"),
                ]
            },
        )
    )
    approval = await search_fda_approval("somedrug")
    assert approval is not None
    assert approval.application_number == "NDA100000"
    assert approval.approval_date == "2012-07-15"
    assert approval.brand_names == ["FIRST", "LATER"]


@respx.mock
async def test_fda_approval_is_none_without_a_us_application() -> None:
    from mcp_servers.openfda.tools import search_fda_approval

    respx.get(_DRUGSFDA_URL).mock(return_value=httpx.Response(404, json={}))
    assert await search_fda_approval("somedrug") is None


@respx.mock
async def test_fda_approval_failure_is_not_read_as_no_approval() -> None:
    from mcp_servers.openfda.tools import search_fda_approval

    respx.get(_DRUGSFDA_URL).mock(return_value=httpx.Response(500, json={}))
    with pytest.raises(MCPToolError):
        await search_fda_approval("somedrug")
