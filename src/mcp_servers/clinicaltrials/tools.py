# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinicalTrials.gov tools using the v2 API.

API docs: https://clinicaltrials.gov/data-api/api
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_CT_BASE = "https://clinicaltrials.gov/api/v2/studies"


class ParticipationCriteria(BaseModel):
    sex: str = ""
    minimum_age: str = ""
    maximum_age: str = ""
    healthy_volunteers: str = ""
    eligibility_criteria: str = ""


class StudyPlan(BaseModel):
    study_type: str = ""
    allocation: str = ""
    intervention_model: str = ""
    intervention_model_description: str = ""
    primary_purpose: str = ""
    masking: str = ""
    number_of_arms: int | None = None


class StudyRecordDates(BaseModel):
    first_submit_date: str = ""
    first_post_date: str = ""
    last_update_submit_date: str = ""
    last_update_post_date: str = ""


class TrialRecord(BaseModel):
    nct_id: str
    title: str
    status: str = ""
    phase: str = ""
    conditions: list[str] = []
    interventions: list[str] = []
    enrollment: int | None = None
    start_date: str = ""
    # scope mirrors Evidence.scope — "abstract" when no results are posted
    scope: str = "abstract"
    brief_summary: str = ""
    sponsor: str = ""
    design_details: str = ""
    participation_criteria: ParticipationCriteria = ParticipationCriteria()
    study_plan: StudyPlan = StudyPlan()
    record_dates: StudyRecordDates = StudyRecordDates()


_MAX_TRIALS = 1000
_PAGE_SIZE = 200
_FIELDS = (
    "NCTId,BriefTitle,OverallStatus,Phase,"
    "Condition,InterventionName,EnrollmentCount,StartDate,HasResults,"
    "BriefSummary,DetailedDescription,LeadSponsorName,"
    "EligibilityCriteria,Sex,MinimumAge,MaximumAge,HealthyVolunteers,"
    "StudyType,DesignAllocation,DesignInterventionModel,"
    "DesignInterventionModelDescription,DesignPrimaryPurpose,"
    "DesignMasking,"
    "StudyFirstSubmitDate,StudyFirstPostDate,"
    "LastUpdateSubmitDate,LastUpdatePostDate"
)


async def search_trials(
    gene: str,
    disease: str,
    population: str | None = None,
) -> list[TrialRecord]:
    """Search ClinicalTrials.gov for studies involving the gene and disease."""
    term_parts = [gene, disease]
    if population:
        term_parts.append(population)
    query_term = " AND ".join(term_parts)

    params: dict[str, Any] = {
        "query.term": query_term,
        "pageSize": _PAGE_SIZE,
        "format": "json",
        "fields": _FIELDS,
    }

    studies: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        while len(studies) < _MAX_TRIALS:
            response = await client.get(_CT_BASE, params=params)
            if response.status_code != 200:
                raise MCPToolError(f"ClinicalTrials.gov API returned HTTP {response.status_code}")
            data = response.json()
            page = data.get("studies") or []
            studies.extend(page)
            next_token = data.get("nextPageToken")
            if not next_token or not page:
                break
            params = {**params, "pageToken": next_token}

    records: list[TrialRecord] = []

    for study in studies:
        proto = study.get("protocolSection", {})
        id_mod = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        design_mod = proto.get("designModule", {})
        cond_mod = proto.get("conditionsModule", {})
        arms_mod = proto.get("armsInterventionsModule", {})
        desc_mod = proto.get("descriptionModule", {})
        elig_mod = proto.get("eligibilityModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        design_info = design_mod.get("designInfo", {})
        masking_info = design_info.get("maskingInfo", {})

        has_results = bool(study.get("hasResults", False))
        scope = "full_text" if has_results else "abstract"

        interventions = [iv.get("name", "") for iv in arms_mod.get("interventions", [])]

        phases = design_mod.get("phases") or []
        records.append(
            TrialRecord(
                nct_id=id_mod.get("nctId", ""),
                title=id_mod.get("briefTitle", ""),
                status=status_mod.get("overallStatus", ""),
                phase=phases[0] if phases else "",
                conditions=cond_mod.get("conditions", []),
                interventions=interventions,
                enrollment=design_mod.get("enrollmentInfo", {}).get("count"),
                start_date=status_mod.get("startDateStruct", {}).get("date", ""),
                scope=scope,
                brief_summary=desc_mod.get("briefSummary", ""),
                sponsor=sponsor_mod.get("leadSponsor", {}).get("name", ""),
                design_details=desc_mod.get("detailedDescription", ""),
                participation_criteria=ParticipationCriteria(
                    sex=elig_mod.get("sex", ""),
                    minimum_age=elig_mod.get("minimumAge", ""),
                    maximum_age=elig_mod.get("maximumAge", ""),
                    healthy_volunteers=str(elig_mod.get("healthyVolunteers", "")),
                    eligibility_criteria=elig_mod.get("eligibilityCriteria", ""),
                ),
                study_plan=StudyPlan(
                    study_type=design_mod.get("studyType", ""),
                    allocation=design_info.get("allocation", ""),
                    intervention_model=design_info.get("interventionModel", ""),
                    intervention_model_description=design_info.get(
                        "interventionModelDescription", ""
                    ),
                    primary_purpose=design_info.get("primaryPurpose", ""),
                    masking=masking_info.get("masking", ""),
                    number_of_arms=design_mod.get("numberOfArms"),
                ),
                record_dates=StudyRecordDates(
                    first_submit_date=status_mod.get("studyFirstSubmitDate", ""),
                    first_post_date=status_mod.get("studyFirstPostDateStruct", {}).get("date", ""),
                    last_update_submit_date=status_mod.get("lastUpdateSubmitDate", ""),
                    last_update_post_date=status_mod.get("lastUpdatePostDateStruct", {}).get(
                        "date", ""
                    ),
                ),
            )
        )
    return records
