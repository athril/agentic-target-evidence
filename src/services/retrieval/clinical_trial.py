# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Clinical trial retrieval service — deterministic MCP-backed fetch."""

from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

from core.evidence_text import mentions_disease
from core.persistence.artifact_store import archive_raw
from core.telemetry.langfuse import span
from mcp_servers.clinicaltrials.tools import (
    RESULTS_VERSION,
    TrialRecord,
    drug_named_in,
    investigational_drugs,
    search_trials,
    search_trials_for_drugs,
)
from mcp_servers.opentargets.tools import SALT_SUFFIX_RE
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType
from services._common import make_provenance

_SERVICE = "services/retrieval/clinical_trial"
logger = logging.getLogger(__name__)

# How many of the target's drugs get their own trial query (known drugs first).
_MAX_DRUG_QUERIES = 8
_MAX_KNOWN_DRUGS = 6
_SALT_RE = SALT_SUFFIX_RE


def _drug_key(name: str) -> str:
    return _SALT_RE.sub("", " ".join(name.lower().split()))


async def known_drug_names(gene_id: str, disease: str = "", disease_id: str = "") -> list[str]:
    """The target's approved and clinical drugs (Open Targets), most relevant first.

    Ranked by: has an indication row for the run's disease, approved, max phase, number
    of indications. Alphabetical order among equally advanced drugs dropped a target's
    main drugs from the capped list (EGFR: afatinib and its salt ahead of osimertinib).
    """
    if not gene_id:
        return []
    try:
        from mcp_servers.opentargets.tools import get_known_drugs

        bundle = await get_known_drugs(gene_id)
    except Exception as exc:  # optional expansion: never fail trial retrieval over it
        logger.warning("clinical_trial: known-drug lookup failed (%s); gene query only", exc)
        return []
    stats: dict[str, dict[str, Any]] = {}
    for d in bundle.drugs:
        key = _drug_key(d.drug_name)
        st = stats.setdefault(
            key,
            {
                "name": _SALT_RE.sub("", d.drug_name.strip()),
                "in_disease": False,
                "approved": False,
                "phase": 0.0,
                "indications": set(),
            },
        )
        st["in_disease"] = st["in_disease"] or bool(
            (disease_id and d.disease_id == disease_id)
            or (disease and mentions_disease(d.disease_name, disease))
        )
        st["approved"] = st["approved"] or d.is_approved
        st["phase"] = max(st["phase"], d.max_phase)
        st["indications"].add(d.disease_id or d.disease_name)
    ranked = sorted(
        stats.values(),
        key=lambda v: (
            -int(v["in_disease"]),
            -int(v["approved"]),
            -v["phase"],
            -len(v["indications"]),
            v["name"],
        ),
    )
    return [v["name"] for v in ranked][:_MAX_KNOWN_DRUGS]


def drug_query_names(known: list[str], records: list[TrialRecord], gene: str = "") -> list[str]:
    """Drugs to query by name: known drugs, then investigational products of the
    gene-matched trials, de-duplicated across salts and case."""
    names: dict[str, str] = {}
    for name in [*known, *investigational_drugs(records, gene=gene, known=known)]:
        names.setdefault(_drug_key(name), name)
    return list(names.values())[:_MAX_DRUG_QUERIES]


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
    if record.results:
        lines += ["## Posted Results (primary outcomes)", ""]
        lines += [f"- {o.summary()}" for o in record.results]
        lines += [""]
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


async def collect_trial_records(
    gene: str,
    disease: str,
    *,
    gene_id: str = "",
    disease_id: str = "",
    population: str | None = None,
) -> tuple[list[TrialRecord], dict[str, str]]:
    """Gene-matched trials plus the target's drugs' trials; NCT id → matching drug name
    for trials found only by a drug query. No I/O beyond the registry (no archive, no DB),
    so the recall eval (scripts/eval_trial_recall.py) runs exactly this."""
    records = await search_trials(gene, disease, population)
    drugs = drug_query_names(await known_drug_names(gene_id, disease, disease_id), records, gene)
    seen = {r.nct_id for r in records}
    matched_by: dict[str, str] = {}
    dropped = 0
    for r in await search_trials_for_drugs(drugs, disease):
        if r.nct_id in seen:
            continue
        named = next((d for d in drugs if drug_named_in(r, d)), None)
        if named:
            matched_by[r.nct_id] = f"drug:{named}"
        elif r.query_in_disease:
            # Found in this disease by the registry's synonym search ("osimertinib" →
            # "AZD9291") without naming the drug: keep it, but let screening judge it.
            matched_by[r.nct_id] = f"registry:{r.query_drug}"
        else:
            dropped += 1
            continue
        seen.add(r.nct_id)
        records.append(r)
    if drugs:
        logger.info(
            "clinical_trial: +%d trials from drug queries (%s); %d dropped as not naming a drug",
            len(matched_by),
            drugs,
            dropped,
        )
    return records, matched_by


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
        records, matched_by = await collect_trial_records(
            gene, disease, gene_id=gene_id, disease_id=disease_id, population=population
        )

    prov = make_provenance(_SERVICE, "search_trials", trace_id)
    direction_enum = (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )
    evidences: list[Evidence] = []
    for r in records:
        uri = archive_raw(
            gene,
            disease_id,
            direction_enum.value,
            "clinical_trials",
            f"{r.nct_id}.md",
            _render_markdown(r),
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
                    "results": [o.model_dump() for o in r.results],
                    "arm_events": [a.model_dump() for a in r.arm_events],
                    "results_version": RESULTS_VERSION,
                    # Why the trial was retrieved: the gene keyword, or one of the
                    # target's drugs (trial records rarely name the gene).
                    "matched_by": matched_by.get(r.nct_id, "gene"),
                },
            )
        )
    return evidences


_REFETCH_CONCURRENCY = 4


def lacks_current_results(ev: Evidence) -> bool:
    """A cached trial row with posted results fetched before the current results version
    (before hard-endpoint secondaries and per-arm deaths were kept)."""
    extra = ev.extra or {}
    return (
        ev.evidence_type == EvidenceType.CLINICAL_TRIAL
        and bool(extra.get("results"))
        and int(extra.get("results_version") or 1) < RESULTS_VERSION
    )


async def with_current_results(evidences: list[Evidence]) -> tuple[list[Evidence], int]:
    """Refetch the posted results of cached trial rows of an older results version;
    returns the rows and how many changed. A failed fetch leaves a row as it was."""
    import asyncio

    import httpx

    from mcp_servers.clinicaltrials.tools import fetch_results

    stale = [ev for ev in evidences if lacks_current_results(ev)]
    if not stale:
        return evidences, 0
    gate = asyncio.Semaphore(_REFETCH_CONCURRENCY)
    async with httpx.AsyncClient(timeout=30.0) as client:

        async def one(ev: Evidence) -> Evidence | None:
            async with gate:
                fetched = await fetch_results(client, ev.source)
            if fetched is None:
                return None
            extra = {
                **(ev.extra or {}),
                "results": [o.model_dump() for o in fetched.outcomes],
                "arm_events": [a.model_dump() for a in fetched.arm_events],
                "results_version": RESULTS_VERSION,
            }
            return ev.model_copy(update={"extra": extra})

        refreshed = await asyncio.gather(*(one(ev) for ev in stale))
    updated = {ev.evidence_id: new for ev, new in zip(stale, refreshed, strict=True) if new}
    return [updated.get(ev.evidence_id, ev) for ev in evidences], len(updated)
