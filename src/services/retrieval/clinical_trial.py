# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Clinical trial retrieval service — deterministic MCP-backed fetch."""

from __future__ import annotations

import uuid
from uuid import UUID

from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.clinicaltrials.tools import TrialRecord, search_trials
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType
from services._common import make_provenance

_SERVICE = "services/retrieval/clinical_trial"


def _render_markdown(record: TrialRecord) -> str:
    interventions = ", ".join(record.interventions) or "—"
    conditions = ", ".join(record.conditions) or "—"
    pc = record.participation_criteria
    sp = record.study_plan
    rd = record.record_dates

    lines = [
        f"# {record.title}",
        "",
        f"**NCT ID:** {record.nct_id}  ",
        f"**Status:** {record.status or '—'}  ",
        f"**Phase:** {record.phase or '—'}  ",
        f"**Conditions:** {conditions}  ",
        f"**Interventions:** {interventions}  ",
        f"**Enrollment:** {record.enrollment or '—'}  ",
        f"**Start Date:** {record.start_date or '—'}  ",
        f"**Sponsor:** {record.sponsor or '—'}  ",
        f"**Link:** https://clinicaltrials.gov/study/{record.nct_id}",
        "",
    ]
    if record.brief_summary:
        lines += ["## Study Overview", "", record.brief_summary, ""]
    lines += [
        "## Participation Criteria",
        "",
        f"**Sex:** {pc.sex or '—'}  ",
        f"**Minimum Age:** {pc.minimum_age or '—'}  ",
        f"**Maximum Age:** {pc.maximum_age or '—'}  ",
        f"**Healthy Volunteers:** {pc.healthy_volunteers or '—'}  ",
        "",
    ]
    if pc.eligibility_criteria:
        lines += [pc.eligibility_criteria, ""]
    lines += [
        "## Study Plan",
        "",
        f"**Study Type:** {sp.study_type or '—'}  ",
        f"**Allocation:** {sp.allocation or '—'}  ",
        f"**Intervention Model:** {sp.intervention_model or '—'}  ",
        f"**Primary Purpose:** {sp.primary_purpose or '—'}  ",
        f"**Masking:** {sp.masking or '—'}  ",
        f"**Number of Arms:** {sp.number_of_arms if sp.number_of_arms is not None else '—'}  ",
        "",
    ]
    if sp.intervention_model_description:
        lines += [sp.intervention_model_description, ""]
    lines += [
        "## Study Record Dates",
        "",
        f"**First Submitted:** {rd.first_submit_date or '—'}  ",
        f"**First Posted:** {rd.first_post_date or '—'}  ",
        f"**Last Update Submitted:** {rd.last_update_submit_date or '—'}  ",
        f"**Last Update Posted:** {rd.last_update_post_date or '—'}  ",
        "",
    ]
    return "\n".join(lines)


async def fetch_trials(
    gene: str,
    disease: str,
    *,
    gene_id: str = "",
    disease_id: str = "",
    population: str | None = None,
    run_id: UUID,
    trace_id: str,
    direction: str = "unspecified",
) -> list[Evidence]:
    """Fetch clinical trial evidence for a gene/disease pair."""
    async with span(_SERVICE, trace_id=trace_id, input_data=f"{gene}|{disease}"):
        records = await search_trials(gene, disease, population)

    prov = make_provenance(_SERVICE, "search_trials", trace_id)
    direction_enum = (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )
    evidences: list[Evidence] = []
    for r in records:
        uri = archive_raw(
            gene, disease_id, direction_enum.value, "clinical_trials", f"{r.nct_id}.md", _render_markdown(r)
        )
        evidences.append(
            Evidence(
                evidence_id=uuid.uuid4(),
                run_id=run_id,
                gene=gene,
                gene_id=gene_id,
                disease=disease,
                disease_id=disease_id,
                evidence_type=EvidenceType.CLINICAL_TRIAL,
                scope=r.scope,
                source=r.nct_id,
                source_link=f"https://clinicaltrials.gov/study/{r.nct_id}",
                artifact_uri=uri,
                classification=DataClass.NON_SENSITIVE,
                provenance=prov,
                direction=direction_enum,
                extra={
                    "title": r.title,
                    "status": r.status,
                    "phase": r.phase,
                    "enrollment": r.enrollment,
                    "interventions": r.interventions,
                    "conditions": r.conditions,
                    "sponsor": r.sponsor,
                    "brief_summary": r.brief_summary,
                    "participation_criteria": r.participation_criteria.model_dump(),
                    "study_plan": r.study_plan.model_dump(),
                    "design_details": r.study_plan.intervention_model_description,
                    "record_dates": r.record_dates.model_dump(),
                },
            )
        )
    return evidences
