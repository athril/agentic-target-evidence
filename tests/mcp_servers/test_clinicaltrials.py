# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ClinicalTrials.gov MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_servers.clinicaltrials.tools import (
    _ACTIVE_STATUSES,
    ConditionTrialLandscape,
    OutcomeResult,
    TrialRecord,
    _parse_primary_outcomes,
    count_condition_trials,
    search_trials,
)

_CT_RESPONSE = {
    "studies": [
        {
            "hasResults": True,
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT00000001",
                    "briefTitle": "BRCA1 in Breast Cancer Phase II",
                },
                "statusModule": {
                    "overallStatus": "COMPLETED",
                    "startDateStruct": {"date": "2018-01"},
                    "studyFirstSubmitDate": "2017-06-01",
                    "studyFirstPostDateStruct": {"date": "2017-06-05"},
                    "lastUpdateSubmitDate": "2023-01-10",
                    "lastUpdatePostDateStruct": {"date": "2023-01-12"},
                },
                "designModule": {
                    "phases": ["PHASE2"],
                    "enrollmentInfo": {"count": 120},
                    "studyType": "INTERVENTIONAL",
                    "numberOfArms": 2,
                    "designInfo": {
                        "allocation": "RANDOMIZED",
                        "interventionModel": "PARALLEL",
                        "primaryPurpose": "TREATMENT",
                        "maskingInfo": {"masking": "DOUBLE"},
                    },
                },
                "conditionsModule": {"conditions": ["Breast Cancer"]},
                "armsInterventionsModule": {"interventions": [{"name": "Drug A"}]},
                "descriptionModule": {
                    "briefSummary": "A study of BRCA1 in breast cancer.",
                    "detailedDescription": "Detailed design description here.",
                },
                "eligibilityModule": {
                    "sex": "ALL",
                    "minimumAge": "18 Years",
                    "maximumAge": "75 Years",
                    "healthyVolunteers": "No",
                    "eligibilityCriteria": "Inclusion Criteria:\n- BRCA1 mutation carrier",
                },
                "sponsorCollaboratorsModule": {
                    "leadSponsor": {"name": "National Cancer Institute"}
                },
            },
        }
    ]
}


@respx.mock
async def test_search_trials_returns_records() -> None:
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=_CT_RESPONSE)
    )
    records = await search_trials("BRCA1", "breast cancer")

    assert len(records) == 1
    r = records[0]
    assert isinstance(r, TrialRecord)
    assert r.nct_id == "NCT00000001"
    assert r.phase == "PHASE2"
    assert r.enrollment == 120
    assert r.brief_summary == "A study of BRCA1 in breast cancer."
    assert r.sponsor == "National Cancer Institute"
    assert r.design_details == "Detailed design description here."
    assert r.participation_criteria.sex == "ALL"
    assert r.participation_criteria.minimum_age == "18 Years"
    assert r.participation_criteria.healthy_volunteers == "No"
    assert r.study_plan.study_type == "INTERVENTIONAL"
    assert r.study_plan.allocation == "RANDOMIZED"
    assert r.study_plan.masking == "DOUBLE"
    assert r.study_plan.number_of_arms == 2
    assert r.record_dates.first_submit_date == "2017-06-01"
    assert r.record_dates.first_post_date == "2017-06-05"
    assert r.record_dates.last_update_post_date == "2023-01-12"


@respx.mock
async def test_trials_with_results_have_full_text_scope() -> None:
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=_CT_RESPONSE)
    )
    records = await search_trials("BRCA1", "breast cancer")
    assert records[0].scope == "full_text"


@respx.mock
async def test_trials_without_results_have_abstract_scope() -> None:
    no_results = dict(_CT_RESPONSE)
    no_results["studies"] = [{**_CT_RESPONSE["studies"][0], "hasResults": False}]

    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=no_results)
    )
    records = await search_trials("BRCA1", "breast cancer")
    assert records[0].scope == "abstract"


@respx.mock
async def test_search_trials_with_population() -> None:
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json={"studies": []})
    )
    records = await search_trials("BRCA1", "breast cancer", population="paediatric")
    assert records == []


@respx.mock
async def test_search_trials_raises_on_api_error() -> None:
    from core.exceptions import MCPToolError

    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(return_value=httpx.Response(500))
    with pytest.raises(MCPToolError):
        await search_trials("BRCA1", "breast cancer")


# ── count_condition_trials ───────────────────────────────────────────────────

_CT_URL = "https://clinicaltrials.gov/api/v2/studies"


def _count_params(condition: str, **extra: str) -> dict:
    return {
        "query.cond": condition,
        "countTotal": "true",
        "pageSize": "1",
        "fields": "NCTId",
        **extra,
    }


def _mock_counts(condition: str, total: int, active: int, recruiting: int, phase3: int) -> None:
    # respx matches `params=` as a subset, so register the most-specific (extra-param)
    # routes first — otherwise the bare-params route would swallow every request.
    respx.get(
        _CT_URL, params=_count_params(condition, **{"filter.overallStatus": _ACTIVE_STATUSES})
    ).mock(return_value=httpx.Response(200, json={"totalCount": active}))
    respx.get(
        _CT_URL, params=_count_params(condition, **{"filter.overallStatus": "RECRUITING"})
    ).mock(return_value=httpx.Response(200, json={"totalCount": recruiting}))
    respx.get(_CT_URL, params=_count_params(condition, aggFilters="phase:3")).mock(
        return_value=httpx.Response(200, json={"totalCount": phase3})
    )
    respx.get(_CT_URL, params=_count_params(condition)).mock(
        return_value=httpx.Response(200, json={"totalCount": total})
    )


@respx.mock
async def test_count_condition_trials_uses_count_only_queries() -> None:
    """countTotal/pageSize=1 — never pages all results for a condition-only query."""
    _mock_counts("breast cancer", total=500, active=120, recruiting=40, phase3=30)
    landscape = await count_condition_trials("breast cancer")

    assert isinstance(landscape, ConditionTrialLandscape)
    assert landscape.mapping == "cond"
    assert landscape.total_count == 500
    assert landscape.active_count == 120
    assert landscape.recruiting_count == 40
    assert landscape.phase3_count == 30
    assert "500 trials" in landscape.text
    assert "120 active" in landscape.text
    assert "30 in Phase 3" in landscape.text


@respx.mock
async def test_count_condition_trials_zero_total_is_mapping_none() -> None:
    _mock_counts("an extremely rare condition", total=0, active=0, recruiting=0, phase3=0)
    landscape = await count_condition_trials("an extremely rare condition")

    assert landscape.mapping == "none"
    assert landscape.total_count == 0
    assert landscape.text == ""


@respx.mock
async def test_count_condition_trials_raises_on_api_error() -> None:
    from core.exceptions import MCPToolError

    respx.get(_CT_URL, params=_count_params("breast cancer")).mock(return_value=httpx.Response(500))
    respx.get(
        _CT_URL,
        params=_count_params("breast cancer", **{"filter.overallStatus": _ACTIVE_STATUSES}),
    ).mock(return_value=httpx.Response(200, json={"totalCount": 0}))
    respx.get(
        _CT_URL, params=_count_params("breast cancer", **{"filter.overallStatus": "RECRUITING"})
    ).mock(return_value=httpx.Response(200, json={"totalCount": 0}))
    respx.get(_CT_URL, params=_count_params("breast cancer", aggFilters="phase:3")).mock(
        return_value=httpx.Response(200, json={"totalCount": 0})
    )
    with pytest.raises(MCPToolError, match="HTTP 500"):
        await count_condition_trials("breast cancer")


# Shape of a v2 resultsSection (NCT05213624, trimmed): a primary outcome with per-arm
# values and an analysis against placebo, plus a secondary outcome that is ignored.
_RESULTS = {
    "resultsSection": {
        "outcomeMeasuresModule": {
            "outcomeMeasures": [
                {
                    "type": "PRIMARY",
                    "title": "Relative change from baseline in UPCR at week 12",
                    "unitOfMeasure": "Percentage of change",
                    "paramType": "LEAST_SQUARES_MEAN",
                    "groups": [
                        {"id": "OG000", "title": "Drug 20 mg"},
                        {"id": "OG001", "title": "Placebo"},
                    ],
                    "classes": [
                        {
                            "categories": [
                                {
                                    "measurements": [
                                        {
                                            "groupId": "OG000",
                                            "value": "-38.44",
                                            "lowerLimit": "-50.41",
                                            "upperLimit": "-23.60",
                                        },
                                        {
                                            "groupId": "OG001",
                                            "value": "2.28",
                                            "lowerLimit": "-20.20",
                                            "upperLimit": "31.09",
                                        },
                                    ]
                                }
                            ]
                        }
                    ],
                    "analyses": [
                        {
                            "groupIds": ["OG000", "OG001"],
                            "paramType": "Relative difference",
                            "paramValue": "-39.82",
                            "ciLowerLimit": "-56.17",
                            "ciUpperLimit": "-17.36",
                            "pValue": "0.0024",
                            "statisticalMethod": "ANCOVA",
                        }
                    ],
                },
                {"type": "SECONDARY", "title": "Ignored"},
            ]
        }
    }
}


def test_parse_primary_outcomes_keeps_values_and_analyses() -> None:
    (outcome,) = _parse_primary_outcomes(_RESULTS)
    assert [v.group for v in outcome.values] == ["Drug 20 mg", "Placebo"]
    assert outcome.analyses[0].groups == ["Drug 20 mg", "Placebo"]
    assert outcome.analyses[0].p_value == "0.0024"
    assert outcome.summary() == (
        "Relative change from baseline in UPCR at week 12 [Percentage of change]: "
        "Drug 20 mg -38.44 (-50.41 to -23.60); Placebo 2.28 (-20.20 to 31.09); "
        "Drug 20 mg vs Placebo: Relative difference -39.82 (-56.17 to -17.36), p=0.0024"
    )


@respx.mock
async def test_search_trials_attaches_posted_results() -> None:
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=_RESULTS)
    )
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=_CT_RESPONSE)
    )
    (record,) = await search_trials("BRCA1", "breast cancer")
    assert [o.title for o in record.results] == ["Relative change from baseline in UPCR at week 12"]
    assert isinstance(record.results[0], OutcomeResult)


@respx.mock
async def test_results_fetch_failure_keeps_the_trial() -> None:
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(500)
    )
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=_CT_RESPONSE)
    )
    (record,) = await search_trials("BRCA1", "breast cancer")
    assert record.results == []


@respx.mock
async def test_rate_limited_results_fetch_is_retried() -> None:
    """A burst of per-study results fetches is rate-limited; a 429 must be retried, not
    read as a trial without results."""
    results = respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=_RESULTS)]
    )
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=_CT_RESPONSE)
    )
    (record,) = await search_trials("BRCA1", "breast cancer")
    assert results.call_count == 2
    assert [o.title for o in record.results] == ["Relative change from baseline in UPCR at week 12"]


# A surrogate primary met, and the survival secondary not — the shape of a phase 3 that
# looked positive on the registry's primary results alone (arms and values synthetic).
_HARD_RESULTS = {
    "resultsSection": {
        "outcomeMeasuresModule": {
            "outcomeMeasures": [
                {
                    "type": "PRIMARY",
                    "title": "Objective response",
                    "groups": [{"id": "A", "title": "Drug"}, {"id": "B", "title": "Placebo"}],
                    "analyses": [
                        {
                            "groupIds": ["A", "B"],
                            "paramType": "Odds Ratio",
                            "paramValue": "1.9",
                            "pValue": "0.002",
                        }
                    ],
                },
                {
                    "type": "SECONDARY",
                    "title": "Overall Survival (OS)",
                    "groups": [{"id": "A", "title": "Drug"}, {"id": "B", "title": "Placebo"}],
                    "analyses": [
                        {
                            "groupIds": ["A", "B"],
                            "paramType": "Hazard Ratio (HR)",
                            "paramValue": "0.97",
                            "pValue": "0.76",
                        }
                    ],
                },
                {"type": "SECONDARY", "title": "Quality of life score"},
            ]
        },
        "adverseEventsModule": {
            "eventGroups": [
                {
                    "title": "Drug",
                    "deathsNumAffected": 138,
                    "deathsNumAtRisk": 439,
                    "seriousNumAffected": 380,
                    "seriousNumAtRisk": 439,
                },
                {
                    "title": "Placebo",
                    "deathsNumAffected": 40,
                    "deathsNumAtRisk": 222,
                    "seriousNumAffected": 163,
                    "seriousNumAtRisk": 222,
                },
            ]
        },
    }
}


def test_hard_endpoint_secondaries_are_kept_beside_the_primary_and_others_are_not() -> None:
    from mcp_servers.clinicaltrials.tools import _parse_outcomes

    outcomes = _parse_outcomes(_HARD_RESULTS)

    assert [(o.kind, o.title) for o in outcomes] == [
        ("primary", "Objective response"),
        ("secondary", "Overall Survival (OS)"),
    ]
    assert outcomes[1].analyses[0].p_value == "0.76"
    assert [o.title for o in _parse_primary_outcomes(_HARD_RESULTS)] == ["Objective response"]


def test_deaths_and_serious_events_are_read_per_arm() -> None:
    from mcp_servers.clinicaltrials.tools import _parse_arm_events

    drug, placebo = _parse_arm_events(_HARD_RESULTS)

    assert (drug.group, drug.deaths, drug.deaths_at_risk) == ("Drug", 138, 439)
    assert (placebo.serious, placebo.serious_at_risk) == (163, 222)


@respx.mock
async def test_search_trials_attaches_secondaries_and_arm_events() -> None:
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=_HARD_RESULTS)
    )
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=_CT_RESPONSE)
    )

    (record,) = await search_trials("BRCA1", "breast cancer")

    assert [o.kind for o in record.results] == ["primary", "secondary"]
    assert [a.group for a in record.arm_events] == ["Drug", "Placebo"]
