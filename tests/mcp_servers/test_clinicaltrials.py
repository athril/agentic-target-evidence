# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ClinicalTrials.gov MCP tools (MP-27)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_servers.clinicaltrials.tools import TrialRecord, search_trials

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
