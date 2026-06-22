# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""LangGraph StateGraph assembly.

build_graph(router, checkpointer) returns a compiled graph that drives the full
gene-target validation pipeline.

Node topology
-----------------------------------------------------
START
  restart_router (entry; jumps to any node on --resume)
  ├─ literature ──────────────────────────────────┐
  ├─ patent (service) ─────────────────────────────┤
  ├─ clinical_trial (service) ─────────────────────┤──> screening_first
  ├─ opentargets (service) ────────────────────────┤        │
  ├─ genetics ─────────────────────────────────────┤  knowledge_extraction
  ├─ omics ────────────────────────────────────────┤        │
  ├─ functional (service) ─────────────────────────┤  screening_second
  ├─ druggability (service) ────────────────────────┤        │
  └─ openfda (service) ────────────────────────────────────  claim_extraction  (model-op service)
                                                           │
                                                    source_quality  (model-op service)
                                                           │
                                            ┌──── hitl_gate  (bounded loop)
                                            │    genetics biology safety clinical commercial regulatory (lenses)
                                            │       experiment
                                            │    critic  reviewer  reconciler
                                            │       gap_detection
                                            │           │ proceed
                                            └──────>  report
                                                        │
                                                       END
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langfuse import Langfuse, get_client, observe, propagate_attributes
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agents.challenge.critic.agent import CriticAgent
from agents.challenge.reviewer.agent import ReviewerAgent
from agents.interpretation.biology_lens.agent import BiologyLensAgent
from agents.interpretation.clinical_lens.agent import ClinicalLensAgent
from agents.interpretation.commercial_lens.agent import CommercialLensAgent
from agents.interpretation.genetics_lens.agent import GeneticsLensAgent
from agents.interpretation.regulatory_lens.agent import RegulatoryLensAgent
from agents.interpretation.safety_lens.agent import SafetyLensAgent
from agents.retrieval.genetics.agent import GeneticsAgent
from agents.retrieval.literature.agent import LiteratureAgent
from agents.retrieval.omics.agent import OmicsAgent
from agents.screening.knowledge_extraction.agent import KnowledgeExtractionAgent
from agents.screening.screening.agent import ScreeningAgent
from agents.screening.source_quality.agent import SourceQualityAgent
from agents.synthesis.experiment.agent import ExperimentAgent
from agents.synthesis.gap_detection.agent import GapDetectionAgent
from agents.synthesis.report.agent import ReportAgent
from agents.synthesis.report.lens_report import write_lens_report
from core.persistence.artifact_store import export_summary_csv
from core.persistence.db import get_session
from core.persistence.repos.evidence import EvidenceRepository
from core.persistence.repos.llm_cache import LlmCacheRepository
from core.persistence.repos.runs import RunRepository
from core.routing.router import Router
from core.telemetry import init_telemetry
from core.telemetry.projects import ensure_langfuse_project
from harness.context import RunContext
from mcp_servers.opentargets.tools import get_disease_descendants
from mcp_servers.pubmed.tools import fetch_pmc_record
from schemas.evidence import (
    Evidence,
    lens_fingerprint,
    source_fingerprint,
    source_quality_fingerprint,
)
from schemas.messages import AgentMessage
from schemas.state import PipelineState
from services.decision.reconciler import reconcile
from services.evidence.claim_extraction import extract_claims
from services.evidence.clinical_trial_interpret import build_trial_facts
from services.evidence.disease_class import DiseaseClass, resolve_disease_class
from services.retrieval.clinical_trial import fetch_trials
from services.retrieval.druggability import fetch_druggability
from services.retrieval.functional import fetch_functional
from services.retrieval.openfda import fetch_openfda
from services.retrieval.opentargets import fetch_opentargets
from services.retrieval.patent import fetch_patents

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers for node functions
# ---------------------------------------------------------------------------


def _trace_title(target_gene: str, gene_id: str, disease: str, disease_id: str) -> str:
    """Human-readable Langfuse trace title, e.g.
    'BRCA1 (ENSG00000012048) | breast cancer (EFO_0000305)'.
    IDs are omitted when not yet resolved (populated downstream by OpenTargets).
    """
    gene = target_gene + (f" ({gene_id})" if gene_id else "")
    dis = disease + (f" ({disease_id})" if disease_id else "")
    return f"{gene} | {dis}"


def _ctx(state: PipelineState, router: Router) -> RunContext:
    return RunContext(
        run_id=state["run_id"],
        trace_id=str(state["run_id"]),
        router=router,
    )


def _task_msg(
    state: PipelineState,
    to_agent: str,
    task_spec: dict[str, Any],
    payload: Any = None,
) -> AgentMessage:
    # The (gene, disease, direction) entity is universal — inject direction into
    # every task_spec centrally so each agent is direction-aware by construction.
    # Every agent contract declares "direction" in its consumes set.
    spec = {"direction": state.get("direction") or "unspecified", **task_spec}
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=state["run_id"],
        from_agent="planner",
        to_agent=to_agent,
        intent="task",
        task_spec=spec,
        payload=payload,
        trace_id=str(state["run_id"]),
    )


def _evidences(result: AgentMessage) -> list[Evidence]:
    return [e for e in (result.payload or []) if isinstance(e, Evidence)]


def _all_raw_evidence(state: PipelineState) -> list[Evidence]:
    """Combine all acquisition-phase evidence buckets."""
    return (
        list(state.get("literature_evidence", []))
        + list(state.get("patent_evidence", []))
        + list(state.get("trial_evidence", []))
        + list(state.get("opentargets_evidence", []))
        + list(state.get("genetics_evidence", []))
        + list(state.get("omics_evidence", []))
        + list(state.get("functional_evidence", []))
        + list(state.get("druggability_evidence", []))
        + list(state.get("openfda_evidence", []))
    )


def _ot_extra(state: PipelineState) -> dict:
    """Extract the opentargets `extra` dict from state (empty dict if not present)."""
    for ev in state.get("opentargets_evidence", []):
        if ev.extra:
            return ev.extra
    return {}


def _depmap_extra(state: PipelineState) -> dict:
    """Extract the DepMap DependencyBundle fields from functional_evidence (empty dict if absent)."""
    gene = state.get("target_gene", "")
    for ev in state.get("functional_evidence", []):
        if str(getattr(ev, "source", "")).startswith(f"depmap:{gene}"):
            return ev.extra or {}
    return {}


def _dedup_screened(state: PipelineState) -> list[Evidence]:
    """Deduplicate screened_evidence keeping the last (most recent) version per ID."""
    seen: dict = {}
    for ev in state.get("screened_evidence", []):
        seen[ev.evidence_id] = ev
    return list(seen.values())


async def _enrich_uncertain_with_full_text(
    evidences: list[Evidence], *, force: bool
) -> list[Evidence]:
    """Fetch PMC Open Access full text for ``uncertain`` PubMed items.

    Pass 1 leaves an item ``uncertain`` precisely when the abstract is not enough
    to decide. For such items with a PMID, this downloads the OA body, stores it
    in ``extra["full_text"]`` and upgrades ``scope`` to ``full_text`` so the
    second screening pass can re-judge on real content (see
    ScreeningAgent pass 2). Items with no PMID or no OA full text are returned
    unchanged and stay ``abstract``-scope (so pass 2 skips them, as before).
    Network/parse failures degrade to the original item.
    """
    out: list[Evidence] = []
    for ev in evidences:
        verdict = ev.extra.get("screening_verdict", {}).get("verdict")
        pmid = ev.source[5:] if ev.source.startswith("PMID:") else None
        already_have = ev.scope == "full_text" and ev.extra.get("full_text")
        if verdict != "uncertain" or not pmid or (already_have and not force):
            out.append(ev)
            continue
        try:
            ft = await fetch_pmc_record(pmid, with_content=True)
        except Exception as exc:  # noqa: BLE001 — degrade to abstract on any fetch error
            logger.warning("[node] screening_second: full-text fetch failed for %s: %s", pmid, exc)
            ft = None
        if ft and ft.full_text:
            out.append(
                ev.model_copy(
                    update={
                        "scope": "full_text",
                        "artifact_uri": ft.full_text_url,
                        "extra": {
                            **ev.extra,
                            "full_text": ft.full_text,
                            "full_text_url": ft.full_text_url,
                        },
                    }
                )
            )
        else:
            out.append(ev)
    return out


def _keep_evidence(state: PipelineState) -> list[Evidence]:
    """Return deduplicated screened evidence marked 'keep', applying HITL overrides."""
    overrides = state.get("hitl_overrides", {})
    result = []
    for ev in _dedup_screened(state):
        ev_id = str(ev.evidence_id)
        if ev_id in overrides:
            if overrides[ev_id]:
                result.append(ev)
        elif ev.extra.get("screening_verdict", {}).get("verdict") == "keep":
            result.append(ev)
    return result


async def _persist_evidence(evidences: list[Evidence], label: str) -> None:
    if not evidences:
        return
    try:
        async with get_session() as session:
            await EvidenceRepository(session).bulk_upsert(evidences)
    except Exception as exc:
        logger.warning("[persist] %s — DB write failed, evidence retained in state: %s", label, exc)


# ---------------------------------------------------------------------------
# Rerun cache helpers
# ---------------------------------------------------------------------------


def _row_to_evidence(row: Any) -> Evidence:
    """Reconstruct an Evidence schema object from a persisted EvidenceRow.

    The row's original run_id is preserved. Cache-hit nodes return these objects
    into state without calling _persist_evidence — rows already exist in the DB.
    """
    from schemas.evidence import DataClass, Direction, EvidenceType, Provenance

    return Evidence(
        evidence_id=row.evidence_id,
        run_id=row.run_id,
        schema_version=row.schema_version,
        gene=row.gene,
        gene_id=row.gene_id or "",
        disease=row.disease,
        disease_id=row.disease_id or "",
        direction=Direction(row.direction),
        availability_date=row.availability_date,
        population=row.population,
        evidence_type=EvidenceType(row.evidence_type),
        scope=row.scope,
        source=row.source,
        source_link=row.source_link,
        query_used=row.query_used,
        artifact_uri=row.artifact_uri,
        extra=row.extra or {},
        classification=DataClass(row.classification),
        provenance=Provenance(
            agent_name=row.prov_agent_name,
            tool_name=row.prov_tool_name,
            timestamp=row.prov_timestamp,
            model_used=row.prov_model_used,
            trace_id=row.prov_trace_id,
        ),
    )


async def _evidence_cache_lookup(
    gene: str,
    disease: str,
    direction: str,
    evidence_type: str | None,
) -> list[Evidence]:
    """Query prior-run evidence by target identity. Returns [] on miss or DB error."""
    try:
        async with get_session() as session:
            rows = await EvidenceRepository(session).find_by_target(
                gene, disease, direction, evidence_type
            )
        return [_row_to_evidence(r) for r in rows]
    except Exception as exc:
        logger.warning("[cache] evidence lookup failed, fetching fresh: %s", exc)
        return []


async def _llm_cache_get(cache_key: str, model_used: str) -> dict | None:
    """Look up a single LLM decision. Returns None on miss or DB error."""
    if not model_used:
        return None
    try:
        async with get_session() as session:
            return await LlmCacheRepository(session).get(cache_key, model_used)
    except Exception as exc:
        logger.warning("[cache] get failed, treating as miss: %s", exc)
        return None


async def _llm_cache_set(
    cache_key: str,
    model_used: str,
    decision_type: str,
    payload: dict,
) -> None:
    """Persist an LLM decision. Swallows errors so cache failures never abort the pipeline."""
    if not model_used:
        return
    try:
        async with get_session() as session:
            await LlmCacheRepository(session).set(cache_key, model_used, decision_type, payload)
    except Exception as exc:
        logger.warning("[cache] set failed: %s", exc)


# ---------------------------------------------------------------------------
# Restart-from-node configuration
# ---------------------------------------------------------------------------

_ACQUISITION_NODE_NAMES = (
    "literature",
    "patent",
    "clinical_trial",
    "opentargets",
    "genetics",
    "omics",
    "functional",
    "druggability",
    "openfda",
)

# Maps user-facing node names / aliases to canonical jump targets (actual node names).
NODE_TO_JUMP_TARGET: dict[str, str] = {
    "report": "report",
    "gap_detection": "gap_detection",
    "experiment": "experiment",
    "challenge": "experiment",  # restarts critic+reviewer+reconciler
    "critic": "experiment",
    "reviewer": "experiment",
    "reconciler": "experiment",
    "hitl_gate": "hitl_gate",
    "lenses": "hitl_gate",  # restarts all 6 lenses
    "genetics_lens": "hitl_gate",
    "biology_lens": "hitl_gate",
    "safety_lens": "hitl_gate",
    "clinical_lens": "hitl_gate",
    "commercial_lens": "hitl_gate",
    "regulatory_lens": "hitl_gate",
    "claim_extraction": "claim_extraction",
    "knowledge_extraction": "knowledge_extraction",
    "screening": "screening_first",
    "screening_first": "screening_first",
}

_REPORT_CLEAR = {"report_uri": None, "full_report_uri": None, "messages": []}
_GAP_CLEAR = {"replan_decision": None, "gap_guidance": "", "replan_count": 0}
_CHALLENGE_CLEAR = {
    "experiment_results": [],
    "critiques": [],
    "review_gaps": [],
    "agreement_map": None,
}
_LENS_CLEAR = {"lens_verdicts": [], "failed_lenses": []}
_HITL_CLEAR = {"hitl_approved": False, "hitl_overrides": {}}
_CLAIMS_CLEAR = {"extracted_claims": []}
_SCREENING_CLEAR = {"screened_evidence": []}
_SOURCE_QUALITY_CLEAR = {"source_quality": {}}

# Fields to zero out per jump target; all other fields are copied from the old checkpoint.
# Because resume_pipeline injects these into a fresh thread's initial_state (not via
# aupdate_state), the _append / replace_last reducers do not run — the value is seeded
# directly.  screened_evidence is intentionally kept for post-screening restart points:
# _dedup_screened() normalises the accumulated list on every read, so seed + new output
# is safe under the existing _append + dedup pattern.
CLEAR_FROM_NODE: dict[str, dict[str, object]] = {
    "report": {
        **_REPORT_CLEAR,
    },
    "gap_detection": {
        **_GAP_CLEAR,
        **_REPORT_CLEAR,
    },
    "experiment": {
        **_CHALLENGE_CLEAR,
        **_GAP_CLEAR,
        **_REPORT_CLEAR,
    },
    "hitl_gate": {
        **_LENS_CLEAR,
        **_HITL_CLEAR,
        **_CHALLENGE_CLEAR,
        **_GAP_CLEAR,
        **_REPORT_CLEAR,
    },
    "claim_extraction": {
        **_CLAIMS_CLEAR,
        **_LENS_CLEAR,
        **_HITL_CLEAR,
        **_CHALLENGE_CLEAR,
        **_GAP_CLEAR,
        **_REPORT_CLEAR,
    },
    "knowledge_extraction": {
        **_CLAIMS_CLEAR,
        **_LENS_CLEAR,
        **_HITL_CLEAR,
        **_CHALLENGE_CLEAR,
        **_GAP_CLEAR,
        **_REPORT_CLEAR,
    },
    "screening_first": {
        **_SCREENING_CLEAR,
        **_CLAIMS_CLEAR,
        **_SOURCE_QUALITY_CLEAR,
        **_LENS_CLEAR,
        **_HITL_CLEAR,
        **_CHALLENGE_CLEAR,
        **_GAP_CLEAR,
        **_REPORT_CLEAR,
    },
}

# Upstream fields that must be non-empty for the restart to produce meaningful output.
# resume_pipeline emits a WARNING (not an error) if any are absent in the old checkpoint.
_REQUIRED_UPSTREAM: dict[str, list[str]] = {
    "report": ["lens_verdicts", "experiment_results", "agreement_map"],
    "gap_detection": ["critiques", "review_gaps", "agreement_map"],
    "experiment": ["lens_verdicts"],
    "hitl_gate": ["screened_evidence", "extracted_claims", "source_quality"],
    "claim_extraction": ["screened_evidence"],
    "knowledge_extraction": ["screened_evidence"],
    "screening_first": [],
}


# ---------------------------------------------------------------------------
# Safety lens structured-evidence helper
# ---------------------------------------------------------------------------


def _safety_structured_summary(rows: list[Evidence]) -> str:
    """Compact text summary of expression/constraint/omics/genetics evidence rows.

    Used by safety_lens_node to feed the LLM with structured data it cannot
    obtain from extracted_claims when knowledge-extraction has produced 0 claims.
    Mirrors the same injection pattern as ot_safety_text / ot_mouse_text.
    """
    from schemas.evidence import EvidenceType

    _TYPES = {
        EvidenceType.OMICS,
        EvidenceType.EXPRESSION,
        EvidenceType.GENETICS,
        EvidenceType.CONSTRAINT,
    }
    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type not in _TYPES:
            continue
        # Granular rows (GTEx tissue, HPA, GWAS, gnomAD text) carry claim_text.
        # Blob/archive rows have empty claim_text but bundle text in extra["text"].
        text = ev.claim_text or (ev.extra or {}).get("text", "")
        if text:
            lines.append(text)
    if not lines:
        return ""
    return "Structured expression / constraint / genetics evidence:\n" + "\n".join(lines)


def _biology_expression_summary(rows: list[Evidence]) -> str:
    """Compact tissue/anatomical expression summary for the biology lens.

    Reads the same GTEx/HPA/SPOKE-anatomy rows as `_safety_structured_summary`
    (OMICS/EXPRESSION evidence types) but frames them for mechanism-of-action /
    disease-tissue-overlap reasoning rather than safety-liability reasoning.
    """
    from schemas.evidence import EvidenceType

    _TYPES = {EvidenceType.OMICS, EvidenceType.EXPRESSION}
    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type not in _TYPES:
            continue
        text = ev.claim_text or (ev.extra or {}).get("text", "")
        if text:
            lines.append(text)
    if not lines:
        return ""
    return (
        "Tissue/anatomical expression evidence (relevant to mechanism-of-action and "
        "disease-tissue overlap):\n" + "\n".join(lines)
    )


def _gtex_bundle_extra(rows: list[Evidence]) -> tuple[list[dict], str]:
    """Pull the full per-tissue GTEx list + HPA specificity off the GTEx/HPA archive blob.

    The blob row (source prefix ``gtex_hpa:``) carries the complete
    ``gtex_expressions`` list and ``hpa_tissue_specificity`` in ``extra`` — the
    granular per-tissue claim rows only cover the top-N + safety-sentinel tissues,
    which may not include the disease-relevant tissue for an arbitrary indication.
    """
    for ev in rows:
        if ev.source.startswith("gtex_hpa:"):
            extra = ev.extra or {}
            return extra.get("gtex_expressions") or [], extra.get("hpa_tissue_specificity") or ""
    return [], ""


def _disease_tissue_context(state: PipelineState, rows: list[Evidence]) -> dict:
    """Resolve disease-tissue TPM/specificity + the deterministic relevance note.

    Shared by biology_lens_node and safety_lens_node so both lenses are grounded
    in the same disease-relevant tissue rather than inferring it from bulk-TPM rank.
    """
    from services.evidence.disease_tissue import (
        build_disease_tissue_expression_note,
        extract_tissue_tpm,
        resolve_disease_tissue,
        top_tpm_tissues,
    )

    gtex_expressions, hpa_specificity = _gtex_bundle_extra(rows)
    info = resolve_disease_tissue(state.get("disease_id"))
    note = build_disease_tissue_expression_note(gtex_expressions, info, state["disease"])

    bulk_tpm: float | None = None
    disease_tissue_label = ""
    if info and info.gtex_tissues:
        bulk_tpm, _rank, _total = extract_tissue_tpm(gtex_expressions, info.gtex_tissues[0])
        disease_tissue_label = info.gtex_tissues[0]

    return {
        "bulk_tpm": bulk_tpm,
        "hpa_specificity": hpa_specificity,
        "disease_tissue": disease_tissue_label,
        "disease_tissue_expression_note": note,
        # Lists the post-LLM tissue-relevance guard needs to detect bulk-rank misuse.
        "top_tpm_tissues": top_tpm_tissues(gtex_expressions),
        "disease_relevant_tissues": list(info.gtex_tissues) if info else [],
    }


def _biology_regulatory_element_summary(rows: list[Evidence]) -> str:
    """Compact ENCODE cis-regulatory assay coverage summary for the biology lens.

    ENCODE's region-search-derived REGULATORY_ELEMENT rows are coarser than true
    cCRE (PLS/ELS/CTCF) classification — see mcp_servers/encode/tools.py — but still
    signal how well-characterized a locus's regulatory landscape is.
    """
    from schemas.evidence import EvidenceType

    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type != EvidenceType.REGULATORY_ELEMENT:
            continue
        text = ev.claim_text or (ev.extra or {}).get("text", "")
        if text:
            lines.append(text)
    if not lines:
        return ""
    return "Cis-regulatory assay coverage at the gene locus (ENCODE):\n" + "\n".join(lines)


def _faers_safety_summary(rows: list[Evidence]) -> str:
    """Compact FAERS adverse-event summary for the safety lens.

    Reads Evidence.extra directly from REGULATORY rows whose source starts with
    'fda:faers:'. Carries the tools.py caveat: FAERS is signal-generating, not
    ground truth. The safety lens must apply biological-plausibility checks before
    letting a death/serious rate shift the toxicity axis.
    """
    from schemas.evidence import EvidenceType

    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type != EvidenceType.REGULATORY:
            continue
        if not ev.source.startswith("fda:faers:"):
            continue
        ex = ev.extra or {}
        drug = ex.get("drug_name", ev.source)
        total = ex.get("total_reports", 0)
        if not total:
            continue
        parts = [f"FAERS — {drug}: {total:,} total reports"]
        serious_rate = ex.get("serious_rate")
        death_rate = ex.get("death_rate")
        if serious_rate is not None:
            parts.append(f"serious_rate={serious_rate:.1%}")
        if death_rate is not None:
            parts.append(f"death_rate={death_rate:.1%}")
        top = ex.get("top_reactions") or []
        if top:
            top3 = ", ".join(f"{r.get('reaction', '?')} ({r.get('count', 0):,})" for r in top[:3])
            parts.append(f"top reactions: {top3}")
        bw = ex.get("boxed_warning", "")
        if bw:
            parts.append(f"BLACK BOX: {bw[:120]}")
        contra = ex.get("contraindications", "")
        if contra:
            parts.append(f"contraindications: {contra[:120]}")
        lines.append(". ".join(parts) + ".")
    if not lines:
        return ""
    return (
        "FAERS adverse-event signal (signal-generating only — not ground truth; "
        "apply biological-plausibility check before drawing safety conclusions):\n"
        + "\n".join(lines)
    )


def _fda_label_summary(rows: list[Evidence]) -> str:
    """Compact FDA-label summary for the commercial and regulatory lenses.

    Reads Evidence.extra from REGULATORY rows whose source starts with
    'fda:label:'. Surfaces approved-drug MoA, indications, and label-level
    safety flags (black-box warnings, contraindications).
    """
    from schemas.evidence import EvidenceType

    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type != EvidenceType.REGULATORY:
            continue
        if not ev.source.startswith("fda:label:"):
            continue
        ex = ev.extra or {}
        drug = ex.get("drug_name", ev.source)
        moa = (ex.get("mechanism_of_action") or "")[:200]
        ind = (ex.get("indications_and_usage") or "")[:200]
        app = ex.get("application_number", "")
        bw = (ex.get("boxed_warning") or "")[:150]
        contra = (ex.get("contraindications") or "")[:150]
        parts = [f"Drug: {drug}"]
        if app:
            parts.append(f"NDA/BLA: {app}")
        if moa:
            parts.append(f"MoA: {moa}")
        if ind:
            parts.append(f"Indication: {ind}")
        if bw:
            parts.append(f"BLACK BOX: {bw}")
        if contra:
            parts.append(f"Contraindications: {contra}")
        lines.append(" | ".join(parts))
    if not lines:
        return ""
    return "FDA-approved drug labels (MoA, indications, label safety flags):\n" + "\n".join(lines)


def _orphanet_prevalence_summary(rows: list[Evidence]) -> str:
    """Compact disease-prevalence summary for the commercial lens's market-size axis.

    Reads Evidence.extra from GENETICS rows whose source starts with
    'orphanet_prevalence:'. This is an addressable-population signal, not a
    genetic-validity one — kept separate from _genetics_source_evidence_text
    so it only reaches the lens that actually sizes a market.
    """
    from schemas.evidence import EvidenceType

    lines: list[str] = []
    for ev in rows:
        if ev.evidence_type != EvidenceType.GENETICS:
            continue
        if not ev.source.startswith("orphanet_prevalence:"):
            continue
        summary = (ev.extra or {}).get("summary", "")
        if summary:
            lines.append(summary)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Genetics lens structured-evidence helpers (B2 grounding)
# ---------------------------------------------------------------------------


def _genetics_source_evidence_text(evidence_rows: list[Evidence]) -> str:
    """Compact structured text of genetics+constraint evidence for the genetics lens prompt.

    Rendered from structured fields (not free-text) so the lens can reason over
    gnomAD, ClinVar, OT scores, and GWAS even when claim extraction yields 0 claims.
    """
    from schemas.evidence import EvidenceType

    _TYPES = {EvidenceType.GENETICS, EvidenceType.CONSTRAINT}
    lines: list[str] = []
    for ev in evidence_rows:
        if ev.evidence_type not in _TYPES:
            continue
        x = ev.extra or {}
        text = x.get("text") or ""
        if not text:
            if "genetic_score" in x:
                text = (
                    f"OT genetic_score={x.get('genetic_score')}, overall={x.get('overall_score')}"
                )
            elif "summary" in x:
                text = str(x["summary"])[:300]
            elif "pvalue" in x:
                text = (
                    f"GWAS p={x.get('pvalue')}, trait={x.get('trait')}, "
                    f"study={x.get('study_accession') or x.get('study_id')}"
                )
                sample_size = x.get("initial_sample_size")
                if sample_size:
                    text += f", cohort={sample_size}"
            elif "pathogenic" in x:
                p = len(x.get("pathogenic") or [])
                lp = len(x.get("likely_pathogenic") or [])
                text = f"ClinVar: {p} Pathogenic, {lp} Likely-Pathogenic variants"
            elif "loeuf" in x or "pli" in x:
                from services.evidence.constraint_interpret import interpret_constraint as _ic

                _r = _ic(
                    gene_symbol=ev.gene or "unknown",
                    loeuf=x.get("loeuf"),
                    pli=x.get("pli"),
                    mis_z=x.get("mis_z"),
                    moeuf=x.get("moeuf"),
                )
                text = _r.summary_text or (
                    f"gnomAD: LOEUF={x.get('loeuf')}, pLI={x.get('pli')}, mis_z={x.get('mis_z')}"
                )
        if text:
            lines.append(f"[{ev.evidence_type.value}|{ev.source}] {text[:300]}")
    if not lines:
        return ""
    return "Source genetics/constraint evidence:\n" + "\n".join(lines)


def _genetics_floor_signals(evidence_rows: list[Evidence]) -> dict:
    """Extract key genetics signals used for the B3 verdict floor check.

    Returns max OT genetic_score, total P/LP ClinVar variant count, count of
    high-star P/LP variants, a pre-computed ConstraintReading (as flags dict),
    the inferred mechanism direction, and a segregation keyword signal.

    All computed values are deterministic — they feed the post-LLM floor checks
    and direction reconciliation in the genetics lens agent.
    """
    import re

    from schemas.evidence import EvidenceType
    from services.evidence.constraint_interpret import (
        infer_mechanism_direction as _imd,
    )
    from services.evidence.constraint_interpret import (
        interpret_constraint as _ic,
    )

    max_genetic_score = 0.0
    plp_count = 0
    high_star_plp = 0
    segregation_signal = False

    # Accumulate across evidence rows
    gene_symbol = "unknown"
    constraint_kwargs: dict = {}
    all_plp: list[dict] = []
    graph_association: dict | None = None
    inheritance_mode: str | None = None
    hpo_phenotype_count = 0
    hpo_specificity_band = "unknown"
    clingen_classification: str | None = None

    _SEG_RE = re.compile(r"segregat|pedigree|de[ -]?novo|co-?segregat|famil", re.IGNORECASE)
    # SPOKE edge sources that corroborate gene-disease causality (vs. pure
    # text-mining co-mention sources, which carry the same DISEASES score key
    # but weaker evidential weight).
    _CORROBORATING_SPOKE_SOURCES = frozenset({"GWAS", "HPO", "DISEASES", "ClinVar"})

    for ev in evidence_rows:
        x = ev.extra or {}
        gene_symbol = ev.gene or gene_symbol

        if ev.evidence_type == EvidenceType.GENETICS:
            if "hpo_specificity_band" in x:
                # Ontology constraint bundle: inheritance mode + HPO breadth.
                if x.get("inheritance_mode"):
                    inheritance_mode = x["inheritance_mode"]
                hpo_phenotype_count = x.get("hpo_phenotype_count") or 0
                hpo_specificity_band = x.get("hpo_specificity_band") or "unknown"
            if "associations" in x and "total" in x:
                # ClinGen gene-disease validity row — strongest classification is first
                # (get_clingen_validity sorts by _CLASSIFICATION_RANK descending).
                assocs = x.get("associations") or []
                if assocs:
                    clingen_classification = assocs[0].get("classification")
            if "edge_sources" in x:
                # SPOKE graph association row (Disease-ASSOCIATES-Gene edge).
                sources = x.get("edge_sources") or []
                score = x.get("diseases_score")
                corroborates = bool(set(sources) & _CORROBORATING_SPOKE_SOURCES)
                if graph_association is None or (
                    score is not None and score > (graph_association.get("diseases_score") or 0.0)
                ):
                    graph_association = {
                        "disease_name": x.get("disease_name", ""),
                        "edge_sources": sources,
                        "gwas_pvalue": x.get("gwas_pvalue"),
                        "diseases_score": score,
                        "corroborates_causality": corroborates,
                    }
            gs = x.get("genetic_score", 0.0)
            if isinstance(gs, (int, float)) and gs > max_genetic_score:
                max_genetic_score = float(gs)
            # Scan text fields for segregation/pedigree keywords (Step 3 bonus)
            for field in ("text", "summary", "assoc_text"):
                val = x.get(field) or ""
                if val and _SEG_RE.search(val):
                    segregation_signal = True
                    break

        elif ev.evidence_type == EvidenceType.CONSTRAINT:
            if "pathogenic" in x:
                p_list = x.get("pathogenic") or []
                lp_list = x.get("likely_pathogenic") or []
                plp_count += len(p_list) + len(lp_list)
                high_star_plp += sum(1 for v in p_list if (v.get("gold_stars") or 0) >= 1)
                all_plp.extend(p_list)
                all_plp.extend(lp_list)
            elif "loeuf" in x or "pli" in x:
                # gnomAD constraint — keep the latest row (one per gene expected)
                constraint_kwargs = {
                    "loeuf": x.get("loeuf"),
                    "pli": x.get("pli"),
                    "mis_z": x.get("mis_z"),
                    "moeuf": x.get("moeuf"),
                }

    # Compute ConstraintReading and mechanism direction
    constraint_reading_flags: dict = {}
    mechanism_direction: dict | None = None

    if constraint_kwargs:
        reading = _ic(gene_symbol=gene_symbol, **constraint_kwargs)
        # Full ConstraintReading.model_dump() — not just the boolean flags — so the
        # lens agent's post-LLM guard (apply_constraint_guards) can reconstruct the
        # reading and annotate hallucinated/inverted constraint claims in the
        # LLM-generated narrative/rationale without re-querying evidence.
        constraint_reading_flags = reading.model_dump()
        if all_plp:
            md = _imd(reading, all_plp, inheritance_mode=inheritance_mode)
            mechanism_direction = {
                "direction": md.direction.value,
                "mechanism": md.mechanism,
                "confidence": md.confidence,
                "rationale": md.rationale,
                "supporting_variant_ids": md.supporting_variant_ids,
            }

    return {
        "max_genetic_score": max_genetic_score,
        "plp_count": plp_count,
        "high_star_plp": high_star_plp,
        "segregation_signal": segregation_signal,
        "constraint_reading": constraint_reading_flags,
        "mechanism_direction": mechanism_direction,
        "graph_association": graph_association,
        "inheritance_mode": inheritance_mode,
        "hpo_phenotype_count": hpo_phenotype_count,
        "hpo_specificity_band": hpo_specificity_band,
        "clingen_classification": clingen_classification,
    }


async def _resolve_disease_classes(state: PipelineState, floor_signals: dict) -> list[str]:
    """Resolve the disease-class set once per lens node and serialise it for
    task_spec (replaces the old `_ONCOLOGY_AREA_IDS` binary — see
    services.evidence.disease_class). ``floor_signals`` should be that node's
    already-computed `_genetics_floor_signals(...)` result so the Mendelian
    floor check reuses signals the node needed anyway.
    """
    disease_id = state.get("disease_id") or ""
    therapeutic_areas: set[str] = set()
    if disease_id:
        onto = await get_disease_descendants(disease_id)
        therapeutic_areas = onto.therapeutic_areas
    classes = resolve_disease_class(disease_id, therapeutic_areas, floor_signals)
    return sorted(c.value for c in classes)


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------


def build_graph(router: Router, checkpointer=None):
    """Build and compile the pipeline StateGraph.

    Pass checkpointer=None for in-memory execution (e.g. tests).
    In production pass AsyncPostgresSaver from core.checkpoint.
    """
    init_telemetry()

    def _c(state: PipelineState) -> RunContext:
        return _ctx(state, router)

    # ── Restart router ─────────────────────────────────────────────────────
    # Entry point for every graph invocation.
    # Fresh run  → returns {} so static edges fan out to all acquisition nodes.
    # Restart run → returns Command(goto=jump_target) to jump directly to any
    #               node, bypassing acquisition entirely.  The jump target is
    #               set in config["configurable"]["restart_from"] by
    #               resume_pipeline() before calling run_pipeline().

    async def restart_router_node(state: PipelineState, config: RunnableConfig) -> Command | dict:
        jump_target = config.get("configurable", {}).get("restart_from")
        if jump_target:
            logger.info("[restart_router] jumping to node: %s", jump_target)
            return Command(goto=jump_target)
        return {}

    # ── Data acquisition nodes ──────────────────────────────────────────────
    # All acquisition nodes swallow exceptions so one failing data source
    # cannot abort the whole pipeline. Errors are logged at WARNING level and
    # appear in Langfuse traces via the enclosing OTel span.

    async def literature_node(state: PipelineState) -> dict:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "article")
            if cached:
                logger.info("[node] literature: %d items from cache (skipping API)", len(cached))
                return {"literature_evidence": cached, "messages": []}
        msg = _task_msg(
            state,
            "literature",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
                "population": state.get("population"),
            },
        )
        try:
            result = await LiteratureAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("literature node failed, continuing without: %s", exc, exc_info=True)
            return {"literature_evidence": [], "failed_sources": ["literature"], "messages": []}
        ev = _evidences(result)
        await _persist_evidence(ev, "literature")
        logger.info("[node] literature: %d items", len(ev))
        return {"literature_evidence": ev, "messages": [result]}

    async def patent_node(state: PipelineState) -> dict:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "patent")
            if cached:
                logger.info("[node] patent: %d items from cache (skipping API)", len(cached))
                return {"patent_evidence": cached}
        try:
            ev = await fetch_patents(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning("patent service failed, continuing without: %s", exc, exc_info=True)
            return {"patent_evidence": [], "failed_sources": ["patent"]}
        await _persist_evidence(ev, "patent")
        logger.info("[node] patent: %d items", len(ev))
        return {"patent_evidence": ev}

    async def clinical_trial_node(state: PipelineState) -> dict:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "clinical_trial")
            if cached:
                logger.info(
                    "[node] clinical_trial: %d items from cache (skipping API)", len(cached)
                )
                return {"trial_evidence": cached}
        try:
            ev = await fetch_trials(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                population=state.get("population"),
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning(
                "clinical_trial service failed, continuing without: %s", exc, exc_info=True
            )
            return {"trial_evidence": [], "failed_sources": ["clinical_trial"]}
        await _persist_evidence(ev, "clinical_trial")
        logger.info("[node] clinical_trial: %d items", len(ev))
        return {"trial_evidence": ev}

    async def opentargets_node(state: PipelineState) -> dict:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            # opentargets produces "genetics" type evidence; pass None to catch all types it may emit
            cached = await _evidence_cache_lookup(gene, disease, direction, None)
            ot_cached = [
                e
                for e in cached
                if e.evidence_type.value in ("genetics",)
                and e.provenance.agent_name == "opentargets"
            ]
            if ot_cached:
                logger.info(
                    "[node] opentargets: %d items from cache (skipping API)", len(ot_cached)
                )
                updates: dict = {"opentargets_evidence": ot_cached}
                if ot_cached[0].gene_id:
                    updates["gene_id"] = ot_cached[0].gene_id
                if ot_cached[0].disease_id:
                    updates["disease_id"] = ot_cached[0].disease_id
                return updates
        try:
            result = await fetch_opentargets(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning("opentargets service failed, continuing without: %s", exc, exc_info=True)
            return {"opentargets_evidence": [], "failed_sources": ["opentargets"]}
        await _persist_evidence(result.evidences, "opentargets")
        logger.info("[node] opentargets: %d items", len(result.evidences))
        updates = {"opentargets_evidence": result.evidences}
        if result.gene_id:
            updates["gene_id"] = result.gene_id
        if result.disease_id:
            updates["disease_id"] = result.disease_id
        return updates

    async def genetics_node(state: PipelineState) -> dict:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            # genetics agent produces GENETICS + CONSTRAINT types; check for either
            cached = await _evidence_cache_lookup(gene, disease, direction, None)
            gen_cached = [e for e in cached if e.evidence_type.value in ("genetics", "constraint")]
            if gen_cached:
                logger.info(
                    "[node] genetics: %d items from cache (skipping agent)", len(gen_cached)
                )
                return {"genetics_evidence": gen_cached, "messages": []}
        msg = _task_msg(
            state,
            "genetics",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
            },
        )
        try:
            result = await GeneticsAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("genetics node failed, continuing without: %s", exc, exc_info=True)
            return {"genetics_evidence": [], "failed_sources": ["genetics"], "messages": []}
        ev = _evidences(result)
        await _persist_evidence(ev, "genetics")
        logger.info("[node] genetics: %d items", len(ev))
        return {"genetics_evidence": ev, "messages": [result]}

    async def omics_node(state: PipelineState) -> dict:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            # omics agent produces OMICS + EXPRESSION types; check for either
            cached = await _evidence_cache_lookup(gene, disease, direction, None)
            omics_cached = [e for e in cached if e.evidence_type.value in ("omics", "expression")]
            if omics_cached:
                logger.info("[node] omics: %d items from cache (skipping agent)", len(omics_cached))
                return {"omics_evidence": omics_cached, "messages": []}
        msg = _task_msg(
            state,
            "omics",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
                "tissue": state.get("tissue"),
            },
        )
        try:
            result = await OmicsAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("omics node failed, continuing without: %s", exc, exc_info=True)
            return {"omics_evidence": [], "failed_sources": ["omics"], "messages": []}
        ev = _evidences(result)
        await _persist_evidence(ev, "omics")
        logger.info("[node] omics: %d items", len(ev))
        return {"omics_evidence": ev, "messages": [result]}

    async def functional_node(state: PipelineState) -> dict:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "functional_genomics")
            if cached:
                logger.info("[node] functional: %d items from cache (skipping API)", len(cached))
                return {"functional_evidence": cached}
        try:
            ev = await fetch_functional(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning("functional service failed, continuing without: %s", exc, exc_info=True)
            return {"functional_evidence": [], "failed_sources": ["functional"]}
        await _persist_evidence(ev, "functional")
        logger.info("[node] functional: %d items", len(ev))
        return {"functional_evidence": ev}

    async def druggability_node(state: PipelineState) -> dict:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "druggability")
            if cached:
                logger.info("[node] druggability: %d items from cache (skipping API)", len(cached))
                return {"druggability_evidence": cached}
        try:
            ev = await fetch_druggability(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning(
                "druggability service failed, continuing without: %s", exc, exc_info=True
            )
            return {"druggability_evidence": [], "failed_sources": ["druggability"]}
        await _persist_evidence(ev, "druggability")
        logger.info("[node] druggability: %d items", len(ev))
        return {"druggability_evidence": ev}

    async def openfda_node(state: PipelineState) -> dict:
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not state.get("force_refresh", False):
            cached = await _evidence_cache_lookup(gene, disease, direction, "regulatory")
            if cached:
                logger.info("[node] openfda: %d items from cache (skipping API)", len(cached))
                return {"openfda_evidence": cached}
        try:
            ev = await fetch_openfda(
                gene=gene,
                disease=disease,
                gene_id=state.get("gene_id") or "",
                disease_id=state.get("disease_id") or "",
                run_id=state["run_id"],
                trace_id=str(state["run_id"]),
                direction=direction,
            )
        except Exception as exc:
            logger.warning("openfda service failed, continuing without: %s", exc, exc_info=True)
            return {"openfda_evidence": [], "failed_sources": ["openfda"]}
        await _persist_evidence(ev, "openfda")
        logger.info("[node] openfda: %d items", len(ev))
        return {"openfda_evidence": ev}

    # ── Processing nodes ────────────────────────────────────────────────────

    async def screening_first_node(state: PipelineState) -> dict:
        model_fp = state.get("model_fingerprint", "")
        force = state.get("force_refresh", False)
        all_ev = _all_raw_evidence(state)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        logger.info("[node] screening_first: %d total evidence items to screen", len(all_ev))

        if not force and model_fp:
            cache_hits, cache_misses = [], []
            for ev in all_ev:
                ck = source_fingerprint(gene, disease, direction, ev.evidence_type.value, ev.source)
                cached_verdict = await _llm_cache_get(ck, model_fp)
                if cached_verdict is not None:
                    cache_hits.append(
                        ev.model_copy(
                            update={"extra": {**ev.extra, "screening_verdict": cached_verdict}}
                        )
                    )
                else:
                    cache_misses.append(ev)
            logger.info(
                "[node] screening_first: %d cache hits, %d misses",
                len(cache_hits),
                len(cache_misses),
            )
            if not cache_misses:
                kept = sum(
                    1
                    for e in cache_hits
                    if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
                )
                logger.info(
                    "[node] screening_first: %d keep of %d (all from cache)", kept, len(cache_hits)
                )
                return {"screened_evidence": cache_hits, "messages": []}
            # Screen only the misses, then write new verdicts to cache
            msg = _task_msg(
                state,
                "screening",
                {"target_gene": gene, "disease": disease, "pass_type": "first"},
                payload=cache_misses,
            )
            result = await ScreeningAgent().run(msg, _c(state))
            freshly_screened = _evidences(result)
            for ev in freshly_screened:
                verdict = ev.extra.get("screening_verdict")
                if verdict:
                    ck = source_fingerprint(
                        gene, disease, direction, ev.evidence_type.value, ev.source
                    )
                    await _llm_cache_set(ck, model_fp, "screening", verdict)
            all_screened = cache_hits + freshly_screened
            kept = sum(
                1
                for e in all_screened
                if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
            )
            logger.info(
                "[node] screening_first: %d screened — %d keep / %d drop+uncertain",
                len(all_screened),
                kept,
                len(all_screened) - kept,
            )
            return {"screened_evidence": all_screened, "messages": [result]}

        # No cache (force_refresh or no model fingerprint) — original path
        msg = _task_msg(
            state,
            "screening",
            {
                "target_gene": gene,
                "disease": disease,
                "pass_type": "first",
            },
            payload=all_ev,
        )
        result = await ScreeningAgent().run(msg, _c(state))
        ev = _evidences(result)
        if model_fp:
            for e in ev:
                verdict = e.extra.get("screening_verdict")
                if verdict:
                    ck = source_fingerprint(
                        gene, disease, direction, e.evidence_type.value, e.source
                    )
                    await _llm_cache_set(ck, model_fp, "screening", verdict)
        kept = sum(1 for e in ev if e.extra.get("screening_verdict", {}).get("verdict") == "keep")
        logger.info(
            "[node] screening_first: %d screened — %d keep / %d drop+uncertain",
            len(ev),
            kept,
            len(ev) - kept,
        )
        return {"screened_evidence": ev, "messages": [result]}

    async def knowledge_extraction_node(state: PipelineState) -> dict:
        deduped = _dedup_screened(state)
        logger.info("[node] knowledge_extraction: %d items", len(deduped))
        msg = _task_msg(
            state,
            "knowledge_extraction",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
            },
            payload=deduped,
        )
        result = await KnowledgeExtractionAgent().run(msg, _c(state))
        return {"screened_evidence": _evidences(result), "messages": [result]}

    async def screening_second_node(state: PipelineState) -> dict:
        model_fp = state.get("model_fingerprint", "")
        force = state.get("force_refresh", False)
        deduped = _dedup_screened(state)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        # Fetch full text for uncertain items so pass 2 re-judges on real content,
        # not the same abstract pass 1 already saw.
        deduped = await _enrich_uncertain_with_full_text(deduped, force=force)
        uncertain = [
            e for e in deduped if e.extra.get("screening_verdict", {}).get("verdict") == "uncertain"
        ]
        with_ft = sum(1 for e in uncertain if e.scope == "full_text")
        logger.info(
            "[node] screening_second: %d items (%d uncertain to re-screen, %d with full text)",
            len(deduped),
            len(uncertain),
            with_ft,
        )

        if not force and model_fp and uncertain:
            cache_hits, cache_misses = [], []
            for ev in uncertain:
                ck = source_fingerprint(gene, disease, direction, ev.evidence_type.value, ev.source)
                cached_verdict = await _llm_cache_get(ck, model_fp)
                if cached_verdict is not None:
                    cache_hits.append(
                        ev.model_copy(
                            update={"extra": {**ev.extra, "screening_verdict": cached_verdict}}
                        )
                    )
                else:
                    cache_misses.append(ev)
            if not cache_misses:
                # Merge resolved uncertain items back with already-decided items
                decided = [
                    e
                    for e in deduped
                    if e.extra.get("screening_verdict", {}).get("verdict") != "uncertain"
                ]
                all_screened = decided + cache_hits
                kept = sum(
                    1
                    for e in all_screened
                    if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
                )
                logger.info(
                    "[node] screening_second: final %d keep of %d (all from cache)",
                    kept,
                    len(all_screened),
                )
                return {"screened_evidence": all_screened, "messages": []}
            # Screen only uncertain misses; pass full deduped set so agent has context
            to_screen = [
                e
                for e in deduped
                if e not in cache_misses
                or e.extra.get("screening_verdict", {}).get("verdict") != "uncertain"
            ]
            to_screen = [
                e
                for e in deduped
                if e.extra.get("screening_verdict", {}).get("verdict") != "uncertain"
            ] + cache_misses
            msg = _task_msg(
                state,
                "screening",
                {"target_gene": gene, "disease": disease, "pass_type": "second"},
                payload=to_screen,
            )
            result = await ScreeningAgent().run(msg, _c(state))
            freshly_screened = _evidences(result)
            for ev in freshly_screened:
                verdict = ev.extra.get("screening_verdict")
                if verdict and verdict.get("verdict") != "uncertain":
                    ck = source_fingerprint(
                        gene, disease, direction, ev.evidence_type.value, ev.source
                    )
                    await _llm_cache_set(ck, model_fp, "screening", verdict)
            decided = [
                e
                for e in deduped
                if e.extra.get("screening_verdict", {}).get("verdict") != "uncertain"
            ]
            all_screened = (
                decided
                + cache_hits
                + [
                    e
                    for e in freshly_screened
                    if e.extra.get("screening_verdict", {}).get("verdict") != "uncertain"
                ]
            )
            kept = sum(
                1
                for e in all_screened
                if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
            )
            logger.info("[node] screening_second: final %d keep of %d", kept, len(all_screened))
            return {"screened_evidence": all_screened, "messages": [result]}

        # Original path (force_refresh, no fingerprint, or no uncertain items)
        msg = _task_msg(
            state,
            "screening",
            {
                "target_gene": gene,
                "disease": disease,
                "pass_type": "second",
            },
            payload=deduped,
        )
        result = await ScreeningAgent().run(msg, _c(state))
        ev = _evidences(result)
        if model_fp:
            for e in ev:
                verdict = e.extra.get("screening_verdict")
                if verdict and verdict.get("verdict") != "uncertain":
                    ck = source_fingerprint(
                        gene, disease, direction, e.evidence_type.value, e.source
                    )
                    await _llm_cache_set(ck, model_fp, "screening", verdict)
        kept = sum(1 for e in ev if e.extra.get("screening_verdict", {}).get("verdict") == "keep")
        logger.info("[node] screening_second: final %d keep of %d", kept, len(ev))
        return {"screened_evidence": ev, "messages": [result]}

    async def claim_extraction_node(state: PipelineState) -> dict:
        """Extract atomic CoreClaims from screened evidence (pre-HITL).

        Runs on screened+deduplicated evidence so lenses receive typed claims,
        not raw documents. Empty screened set is a no-op.
        """
        keep_evidence = [
            e
            for e in _dedup_screened(state)
            if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
        ]
        if not keep_evidence:
            logger.info("[node] claim_extraction: no keep-evidence, skipping")
            return {"extracted_claims": []}
        logger.info(
            "[node] claim_extraction: extracting from %d evidence items", len(keep_evidence)
        )
        try:
            claims = await extract_claims(
                keep_evidence,
                target_gene=state["target_gene"],
                disease=state["disease"],
                direction=state.get("direction") or "unspecified",
                ctx=_c(state),
            )
        except Exception as exc:
            logger.warning("claim_extraction failed, continuing without: %s", exc, exc_info=True)
            return {"extracted_claims": []}
        logger.info("[node] claim_extraction: %d claims extracted", len(claims))
        return {"extracted_claims": claims}

    async def source_quality_node(state: PipelineState) -> dict:
        """Score each kept Evidence's source quality, once, before the lenses run."""
        model_fp = state.get("model_fingerprint", "")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not force and model_fp:
            ck = source_quality_fingerprint(gene, disease, direction)
            cached = await _llm_cache_get(ck, model_fp)
            if cached is not None:
                logger.info("[node] source_quality: cache HIT")
                return {"source_quality": cached}
        keep_evidence = [
            e
            for e in _dedup_screened(state)
            if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
        ]
        if not keep_evidence:
            logger.info("[node] source_quality: no keep-evidence, skipping")
            return {"source_quality": {}}
        msg = _task_msg(
            state,
            "source_quality",
            {"target_gene": gene, "disease": disease},
            payload=keep_evidence,
        )
        result = await SourceQualityAgent().run(msg, _c(state))
        payload = result.payload if isinstance(result.payload, dict) else {}
        quality_map = payload.get("source_quality", {})
        if quality_map and model_fp and not force:
            ck = source_quality_fingerprint(gene, disease, direction)
            await _llm_cache_set(ck, model_fp, "source_quality", quality_map)
        logger.info("[node] source_quality: %d sources scored", len(quality_map))
        return {"source_quality": quality_map, "messages": [result]}

    async def hitl_gate_node(state: PipelineState) -> dict:
        kept = _keep_evidence(state)
        if not state.get("hitl_approved", False):
            logger.info(
                "[node] hitl_gate: pausing — %d keep-evidence items awaiting human review",
                len(kept),
            )
            interrupt(
                {
                    "screened_evidence_count": len(_dedup_screened(state)),
                    "awaiting": "human review of screened evidence",
                }
            )
        logger.info(
            "[node] hitl_gate: approved — %d evidence items proceeding to reasoning", len(kept)
        )
        # No state modification needed; overrides are applied by downstream nodes
        return {}

    # ── Reasoning nodes — lens architecture ──────────────────────────────────

    async def genetics_lens_node(state: PipelineState) -> dict:
        from schemas.verdicts import LensVerdict

        model_fp = state.get("model_fingerprint", "")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not force and model_fp:
            ck = lens_fingerprint(gene, disease, direction, "genetics")
            cached = await _llm_cache_get(ck, model_fp)
            if cached is not None:
                logger.info("[node] genetics_lens: cache HIT")
                return {"lens_verdicts": [LensVerdict.from_dict(cached)], "messages": []}
        keep_ev = _keep_evidence(state)
        floor_signals = _genetics_floor_signals(keep_ev)
        msg = _task_msg(
            state,
            "genetics_lens",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
                "extracted_claims": [
                    c.model_dump(mode="json") for c in state.get("extracted_claims", [])
                ],
                "source_quality": state.get("source_quality", {}),
                "source_evidence_text": _genetics_source_evidence_text(keep_ev),
                "floor_signals": floor_signals,
                "disease_classes": await _resolve_disease_classes(state, floor_signals),
            },
        )
        try:
            result = await GeneticsLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("genetics_lens failed: %s", exc, exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["genetics"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            ck = lens_fingerprint(gene, disease, direction, "genetics")
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=keep_ev,
                claims=list(state.get("extracted_claims", [])),
                quality_map=state.get("source_quality", {}),
            )
        logger.info("[node] genetics_lens: %s", verdicts[0].overall_verdict if verdicts else "none")
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def biology_lens_node(state: PipelineState) -> dict:
        from schemas.verdicts import LensVerdict

        model_fp = state.get("model_fingerprint", "")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not force and model_fp:
            ck = lens_fingerprint(gene, disease, direction, "biology")
            cached = await _llm_cache_get(ck, model_fp)
            if cached is not None:
                logger.info("[node] biology_lens: cache HIT")
                return {"lens_verdicts": [LensVerdict.from_dict(cached)], "messages": []}
        ot = _ot_extra(state)
        dm = _depmap_extra(state)
        disease_id = state.get("disease_id") or ""
        keep_ev_bio = _keep_evidence(state)
        floor_signals_bio = _genetics_floor_signals(keep_ev_bio)
        disease_classes_bio = await _resolve_disease_classes(state, floor_signals_bio)
        is_oncology = DiseaseClass.ONCOLOGY.value in disease_classes_bio
        tissue_ctx = _disease_tissue_context(state, keep_ev_bio)
        msg = _task_msg(
            state,
            "biology_lens",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": disease_id,
                "extracted_claims": [
                    c.model_dump(mode="json") for c in state.get("extracted_claims", [])
                ],
                "source_quality": state.get("source_quality", {}),
                "ot_tractability_text": ot.get("tract_text", ""),
                "ot_mouse_phenotype_count": ot.get("mouse_phenotype_count", 0),
                "ot_mouse_phenotype_labels": ot.get("mouse_phenotype_labels", []),
                "ot_mouse_text": ot.get("mouse_text", ""),
                "depmap_text": dm.get("text", ""),
                "depmap_mean_chronos": dm.get("gene_effect_mean"),
                "depmap_std_chronos": dm.get("gene_effect_std"),
                "depmap_dependency_fraction": dm.get("dependency_fraction"),
                "depmap_is_common_essential": dm.get("is_common_essential", False),
                "depmap_is_strongly_selective": dm.get("is_strongly_selective", False),
                "depmap_selective_lineages": dm.get("selective_lineages", []),
                "depmap_lineage_breakdown": dm.get("lineage_breakdown", []),
                "is_oncology_indication": is_oncology,
                "disease_classes": disease_classes_bio,
                "omics_expression_text": _biology_expression_summary(keep_ev_bio),
                "regulatory_element_text": _biology_regulatory_element_summary(keep_ev_bio),
                "bulk_tpm": tissue_ctx["bulk_tpm"],
                "hpa_specificity": tissue_ctx["hpa_specificity"],
                "disease_tissue": tissue_ctx["disease_tissue"],
                "disease_tissue_expression_note": tissue_ctx["disease_tissue_expression_note"],
                "top_tpm_tissues": tissue_ctx["top_tpm_tissues"],
                "disease_relevant_tissues": tissue_ctx["disease_relevant_tissues"],
            },
        )
        try:
            result = await BiologyLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("biology_lens failed: %s", exc, exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["biology"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            ck = lens_fingerprint(gene, disease, direction, "biology")
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=_keep_evidence(state),
                claims=list(state.get("extracted_claims", [])),
                ot_extra=_ot_extra(state),
                quality_map=state.get("source_quality", {}),
            )
        logger.info("[node] biology_lens: %s", verdicts[0].overall_verdict if verdicts else "none")
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def safety_lens_node(state: PipelineState) -> dict:
        from schemas.verdicts import LensVerdict

        model_fp = state.get("model_fingerprint", "")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not force and model_fp:
            ck = lens_fingerprint(gene, disease, direction, "safety")
            cached = await _llm_cache_get(ck, model_fp)
            if cached is not None:
                logger.info("[node] safety_lens: cache HIT")
                return {"lens_verdicts": [LensVerdict.from_dict(cached)], "messages": []}
        ot = _ot_extra(state)
        keep_ev_safety = _keep_evidence(state)
        floor_signals = _genetics_floor_signals(keep_ev_safety)
        disease_classes_safety = await _resolve_disease_classes(state, floor_signals)
        tissue_ctx = _disease_tissue_context(state, keep_ev_safety)
        msg = _task_msg(
            state,
            "safety_lens",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
                "extracted_claims": [
                    c.model_dump(mode="json") for c in state.get("extracted_claims", [])
                ],
                "source_quality": state.get("source_quality", {}),
                "ot_safety_liability_count": ot.get("safety_liability_count", 0),
                "ot_safety_liability_events": ot.get("safety_liability_events", []),
                "ot_safety_text": ot.get("safety_text", ""),
                "ot_mouse_text": ot.get("mouse_text", ""),
                "safety_structured_text": _safety_structured_summary(keep_ev_safety),
                "faers_text": _faers_safety_summary(keep_ev_safety),
                "constraint_reading": floor_signals.get("constraint_reading") or {},
                "mechanism_direction": floor_signals.get("mechanism_direction"),
                "bulk_tpm": tissue_ctx["bulk_tpm"],
                "hpa_specificity": tissue_ctx["hpa_specificity"],
                "disease_tissue": tissue_ctx["disease_tissue"],
                "disease_tissue_expression_note": tissue_ctx["disease_tissue_expression_note"],
                "top_tpm_tissues": tissue_ctx["top_tpm_tissues"],
                "disease_relevant_tissues": tissue_ctx["disease_relevant_tissues"],
                "disease_classes": disease_classes_safety,
            },
        )
        try:
            result = await SafetyLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("safety_lens failed: %s", exc, exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["safety"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            ck = lens_fingerprint(gene, disease, direction, "safety")
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=_keep_evidence(state),
                claims=list(state.get("extracted_claims", [])),
                ot_extra=_ot_extra(state),
                quality_map=state.get("source_quality", {}),
            )
        logger.info("[node] safety_lens: %s", verdicts[0].overall_verdict if verdicts else "none")
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def clinical_lens_node(state: PipelineState) -> dict:
        from schemas.verdicts import LensVerdict

        model_fp = state.get("model_fingerprint", "")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not force and model_fp:
            ck = lens_fingerprint(gene, disease, direction, "clinical")
            cached = await _llm_cache_get(ck, model_fp)
            if cached is not None:
                logger.info("[node] clinical_lens: cache HIT")
                return {"lens_verdicts": [LensVerdict.from_dict(cached)], "messages": []}
        disease_classes_clinical = await _resolve_disease_classes(
            state, _genetics_floor_signals(_keep_evidence(state))
        )
        msg = _task_msg(
            state,
            "clinical_lens",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
                "extracted_claims": [
                    c.model_dump(mode="json") for c in state.get("extracted_claims", [])
                ],
                "source_quality": state.get("source_quality", {}),
                "disease_classes": disease_classes_clinical,
            },
        )
        try:
            result = await ClinicalLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("clinical_lens failed: %s", exc, exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["clinical"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            ck = lens_fingerprint(gene, disease, direction, "clinical")
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=_keep_evidence(state),
                claims=list(state.get("extracted_claims", [])),
                quality_map=state.get("source_quality", {}),
            )
        logger.info("[node] clinical_lens: %s", verdicts[0].overall_verdict if verdicts else "none")
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def commercial_lens_node(state: PipelineState) -> dict:
        from schemas.verdicts import LensVerdict

        model_fp = state.get("model_fingerprint", "")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not force and model_fp:
            ck = lens_fingerprint(gene, disease, direction, "commercial")
            cached = await _llm_cache_get(ck, model_fp)
            if cached is not None:
                logger.info("[node] commercial_lens: cache HIT")
                return {"lens_verdicts": [LensVerdict.from_dict(cached)], "messages": []}
        ot = _ot_extra(state)
        keep_ev_commercial = _keep_evidence(state)
        disease_classes_commercial = await _resolve_disease_classes(
            state, _genetics_floor_signals(keep_ev_commercial)
        )
        msg = _task_msg(
            state,
            "commercial_lens",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
                "extracted_claims": [
                    c.model_dump(mode="json") for c in state.get("extracted_claims", [])
                ],
                "source_quality": state.get("source_quality", {}),
                "patent_count": len(list(state.get("patent_evidence", []))),
                "trial_count": len(list(state.get("trial_evidence", []))),
                "ot_known_drugs_count": ot.get("known_drugs_count", 0),
                "ot_known_drugs_approved_count": ot.get("known_drugs_approved_count", 0),
                "ot_known_drugs_phase3_count": ot.get("known_drugs_phase3_count", 0),
                "ot_known_drugs_text": ot.get("known_drugs_text", ""),
                "fda_label_text": _fda_label_summary(keep_ev_commercial),
                "orphanet_prevalence_text": _orphanet_prevalence_summary(keep_ev_commercial),
                "disease_classes": disease_classes_commercial,
            },
        )
        try:
            result = await CommercialLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("commercial_lens failed: %s", exc, exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["commercial"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            ck = lens_fingerprint(gene, disease, direction, "commercial")
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=_keep_evidence(state),
                claims=list(state.get("extracted_claims", [])),
                ot_extra=_ot_extra(state),
                quality_map=state.get("source_quality", {}),
            )
        logger.info(
            "[node] commercial_lens: %s", verdicts[0].overall_verdict if verdicts else "none"
        )
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def regulatory_lens_node(state: PipelineState) -> dict:
        from schemas.verdicts import LensVerdict

        model_fp = state.get("model_fingerprint", "")
        force = state.get("force_refresh", False)
        gene, disease = state["target_gene"], state["disease"]
        direction = state.get("direction") or "unspecified"
        if not force and model_fp:
            ck = lens_fingerprint(gene, disease, direction, "regulatory")
            cached = await _llm_cache_get(ck, model_fp)
            if cached is not None:
                logger.info("[node] regulatory_lens: cache HIT")
                return {"lens_verdicts": [LensVerdict.from_dict(cached)], "messages": []}
        keep_ev_regulatory = _keep_evidence(state)
        disease_classes_regulatory = await _resolve_disease_classes(
            state, _genetics_floor_signals(keep_ev_regulatory)
        )
        msg = _task_msg(
            state,
            "regulatory_lens",
            {
                "target_gene": gene,
                "disease": disease,
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
                "extracted_claims": [
                    c.model_dump(mode="json") for c in state.get("extracted_claims", [])
                ],
                "source_quality": state.get("source_quality", {}),
                "fda_label_text": _fda_label_summary(keep_ev_regulatory),
                "disease_classes": disease_classes_regulatory,
            },
        )
        try:
            result = await RegulatoryLensAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("regulatory_lens failed: %s", exc, exc_info=True)
            return {"lens_verdicts": [], "failed_lenses": ["regulatory"], "messages": []}
        payload = result.payload if isinstance(result.payload, dict) else {}
        verdicts = [LensVerdict.from_dict(v) for v in (payload.get("lens_verdicts") or [])]
        if verdicts and model_fp and not force:
            ck = lens_fingerprint(gene, disease, direction, "regulatory")
            await _llm_cache_set(ck, model_fp, "lens", verdicts[0].model_dump(mode="json"))
        if verdicts:
            write_lens_report(
                verdicts[0],
                state.get("disease_id") or "",
                evidence_rows=_keep_evidence(state),
                claims=list(state.get("extracted_claims", [])),
                quality_map=state.get("source_quality", {}),
            )
        logger.info(
            "[node] regulatory_lens: %s", verdicts[0].overall_verdict if verdicts else "none"
        )
        return {"lens_verdicts": verdicts, "messages": [result]}

    async def experiment_node(state: PipelineState) -> dict:
        # Send only condensed summaries to keep the scoring prompt focused.
        lens_summaries = [
            {
                "lens": lv.lens,
                "overall_verdict": lv.overall_verdict,
                "confidence": lv.confidence,
                "rationale": lv.rationale,
                "narrative": lv.narrative,
            }
            for lv in state.get("lens_verdicts", [])
        ]
        msg = _task_msg(
            state,
            "experiment",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "lens_summaries": lens_summaries,
                "genetics_floor_signals": _genetics_floor_signals(_keep_evidence(state)),
            },
            payload=_keep_evidence(state),
        )
        result = await ExperimentAgent().run(msg, _c(state))
        payload = result.payload if isinstance(result.payload, dict) else {}
        return {"experiment_results": payload.get("experiment_results", []), "messages": [result]}

    async def critic_node(state: PipelineState) -> dict:
        extracted = [c.model_dump(mode="json") for c in state.get("extracted_claims", [])]
        lens_v = [lv.model_dump(mode="json") for lv in state.get("lens_verdicts", [])]
        msg = _task_msg(
            state,
            "critic",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "extracted_claims": extracted,
                "lens_verdicts": lens_v,
                "source_quality": state.get("source_quality", {}),
            },
            payload=_dedup_screened(state),
        )
        result = await CriticAgent().run(msg, _c(state))
        payload = result.payload if isinstance(result.payload, dict) else {}
        return {"critiques": payload.get("critiques", []), "messages": [result]}

    async def reconciler_node(state: PipelineState) -> dict:
        lens_verdicts = state.get("lens_verdicts", [])

        try:
            am = reconcile(list(lens_verdicts), run_id=state["run_id"])
            logger.info(
                "[node] reconciler: consensus=%s, conflicts=%d",
                am.consensus_verdict,
                len(am.conflicts),
            )
            return {"agreement_map": am.model_dump(mode="json")}
        except Exception as exc:
            logger.warning("reconciler failed: %s", exc, exc_info=True)
            return {"agreement_map": None}

    async def reviewer_node(state: PipelineState) -> dict:
        stage_counts = {
            "literature": len(list(state.get("literature_evidence", []))),
            "genetics": len(
                list(state.get("genetics_evidence", [])) + list(state.get("omics_evidence", []))
            ),
            "clinical": len(list(state.get("trial_evidence", []))),
            "screening": len(_dedup_screened(state)),
            "extraction": len(list(state.get("extracted_claims", []))),
            "lenses": len(state.get("lens_verdicts", [])),
            "experiment": len(state.get("experiment_results", [])),
        }
        msg = _task_msg(
            state,
            "reviewer",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "stage_counts": stage_counts,
            },
        )
        result = await ReviewerAgent().run(msg, _c(state))
        payload = result.payload if isinstance(result.payload, dict) else {}
        return {"review_gaps": payload.get("review_gaps", []), "messages": [result]}

    async def gap_detection_node(state: PipelineState) -> dict:
        replan_count = state.get("replan_count", 0)
        # Safety: never trigger another replan if already at max
        if replan_count >= 1:
            logger.info("[node] gap_detection: max replans reached, proceeding")
            return {
                "replan_decision": "proceed",
                "gap_guidance": "Max replans reached — proceeding to report.",
            }
        msg = _task_msg(
            state,
            "gap_detection",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "review_gaps": state.get("review_gaps", []),
                "agreement_map": state.get("agreement_map"),
                "replan_count": replan_count,
            },
        )
        try:
            result = await GapDetectionAgent().run(msg, _c(state))
        except Exception as exc:
            logger.warning("gap_detection failed: %s", exc, exc_info=True)
            return {
                "replan_decision": "proceed",
                "gap_guidance": "Gap detection failed — proceeding.",
            }
        payload = result.payload if isinstance(result.payload, dict) else {}
        rd = payload.get("replan_decision", "proceed")
        guidance = payload.get("gap_guidance", "")
        logger.info("[node] gap_detection: decision=%s count=%d", rd, replan_count)
        if rd == "replan":
            return {
                "replan_decision": "replan",
                "gap_guidance": guidance,
                "replan_count": replan_count + 1,
                "messages": [result],
            }
        return {"replan_decision": "proceed", "gap_guidance": guidance, "messages": [result]}

    async def report_node(state: PipelineState) -> dict:
        screened = _dedup_screened(state)
        await _persist_evidence(screened, "report:screened-verdicts")
        evidence_summary = [
            {
                "source": ev.source,
                "evidence_type": ev.evidence_type.value,
                "verdict": ev.extra.get("screening_verdict", {}).get("verdict", ""),
                "screening_rationale": ev.extra.get("screening_verdict", {}).get("rationale", ""),
            }
            for ev in screened
        ]
        report_payload = {
            "lens_verdicts": [lv.model_dump(mode="json") for lv in state.get("lens_verdicts", [])],
            "agreement_map": state.get("agreement_map"),
            "experiment_results": state.get("experiment_results", []),
            "critiques": state.get("critiques", []),
            "review_gaps": state.get("review_gaps", []),
            "gap_guidance": state.get("gap_guidance", ""),
            "evidence_summary": evidence_summary,
        }
        msg = _task_msg(
            state,
            "report",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "gene_id": state.get("gene_id") or "",
                "disease_id": state.get("disease_id") or "",
            },
            payload=report_payload,
        )
        result = await ReportAgent().run(msg, _c(state))
        payload = result.payload if isinstance(result.payload, dict) else {}
        artifact_uri = payload.get("artifact_uri")
        full_report_uri = payload.get("full_report_uri")
        return {
            "report_uri": artifact_uri,
            "full_report_uri": full_report_uri,
            "messages": [result],
        }

    # ── Assemble graph ──────────────────────────────────────────────────────

    builder = StateGraph(PipelineState)

    for name, fn in [
        ("restart_router", restart_router_node),
        ("literature", literature_node),
        ("patent", patent_node),
        ("clinical_trial", clinical_trial_node),
        ("opentargets", opentargets_node),
        ("genetics", genetics_node),
        ("omics", omics_node),
        ("functional", functional_node),
        ("druggability", druggability_node),
        ("openfda", openfda_node),
        ("screening_first", screening_first_node),
        ("knowledge_extraction", knowledge_extraction_node),
        ("screening_second", screening_second_node),
        ("claim_extraction", claim_extraction_node),
        ("source_quality", source_quality_node),
        ("hitl_gate", hitl_gate_node),
        ("genetics_lens", genetics_lens_node),
        ("biology_lens", biology_lens_node),
        ("safety_lens", safety_lens_node),
        ("clinical_lens", clinical_lens_node),
        ("commercial_lens", commercial_lens_node),
        ("regulatory_lens", regulatory_lens_node),
        ("experiment", experiment_node),
        ("critic", critic_node),
        ("reviewer", reviewer_node),
        ("reconciler", reconciler_node),
        ("gap_detection", gap_detection_node),
        ("report", report_node),
    ]:
        builder.add_node(name, fn)

    # restart_router is the single entry point; for fresh runs it returns {}
    # so its static edges to all acquisition nodes fire (parallel fan-out).
    # For restart runs it returns Command(goto=jump_target), which bypasses
    # the static edges and jumps directly to the requested node.
    builder.add_edge(START, "restart_router")
    for acq in _ACQUISITION_NODE_NAMES:
        builder.add_edge("restart_router", acq)
        builder.add_edge(acq, "screening_first")

    # Processing chain
    builder.add_edge("screening_first", "knowledge_extraction")
    builder.add_edge("knowledge_extraction", "screening_second")
    builder.add_edge("screening_second", "claim_extraction")
    builder.add_edge("claim_extraction", "source_quality")
    builder.add_edge("source_quality", "hitl_gate")

    # HITL → parallel lens fan-out (replaces hypothesis + competitive)
    _lenses = (
        "genetics_lens",
        "biology_lens",
        "safety_lens",
        "clinical_lens",
        "commercial_lens",
        "regulatory_lens",
    )
    for lens_node in _lenses:
        builder.add_edge("hitl_gate", lens_node)
        builder.add_edge(lens_node, "experiment")  # all lenses must complete before experiment

    # experiment → critic + reviewer + reconciler (all three parallel)
    builder.add_edge("experiment", "critic")
    builder.add_edge("experiment", "reviewer")
    builder.add_edge("experiment", "reconciler")

    # critic + reviewer + reconciler → gap_detection (all three must finish before gap assessment)
    builder.add_edge("critic", "gap_detection")
    builder.add_edge("reviewer", "gap_detection")
    builder.add_edge("reconciler", "gap_detection")

    # gap_detection → conditional: proceed → report; replan → hitl_gate (bounded loop-back)
    def _gap_route(state: PipelineState) -> str:
        if state.get("replan_decision") == "replan" and state.get("replan_count", 0) <= 1:
            logger.info("[route] gap_detection → replan (pass %d)", state.get("replan_count", 0))
            return "hitl_gate"
        return "report"

    builder.add_conditional_edges(
        "gap_detection", _gap_route, {"hitl_gate": "hitl_gate", "report": "report"}
    )
    builder.add_edge("report", END)

    return builder.compile(checkpointer=checkpointer)


async def run_pipeline(
    graph,
    initial_state: dict,
    config: dict,
) -> None:
    """Invoke the pipeline graph with a top-level Langfuse trace.

    Uses @observe() to create the root Langfuse trace, then injects the
    LangfuseCallbackHandler into every ainvoke() call so LangGraph nodes are
    automatically nested as child spans in the Langfuse UI.

    Runs to the HITL interrupt; a second invocation (auto-approving screening)
    resumes and runs the graph to completion.
    """
    target_gene = initial_state.get("target_gene", "unknown")
    disease = initial_state.get("disease", "unknown")
    direction = initial_state.get("direction") or "unspecified"
    gene_id = initial_state.get("gene_id") or ""
    disease_id = initial_state.get("disease_id") or ""
    run_id = str(initial_state.get("run_id", ""))

    trace_name = _trace_title(target_gene, gene_id, disease, disease_id)

    # Provision (or retrieve cached) a per-gene/disease Langfuse project so
    # each run appears as its own project in the org view, then swap the
    # global Langfuse client to point at that project's keys.
    base_url = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")
    # None → only the default client from init_telemetry() exists, so @observe
    # resolves it unambiguously. A non-None key means we instantiated a second
    # (per-project) client below and must tell @observe which one to trace into,
    # or it skips tracing to avoid cross-project leakage.
    proj_pk: str | None = None
    try:
        proj_pk, proj_sk = await ensure_langfuse_project(trace_name)
        Langfuse(public_key=proj_pk, secret_key=proj_sk, host=base_url)
    except Exception as exc:
        proj_pk = None
        logger.warning("[telemetry] project provisioning failed, using default project: %s", exc)

    # Propagate cache control fields from initial_state into the graph invocation.
    # Callers (run_analysis.py, planner main.py) compute model_fingerprint from
    # router.select() before calling run_pipeline and seed it into initial_state.
    model_fingerprint = initial_state.get("model_fingerprint", "")
    force_refresh = initial_state.get("force_refresh", False)

    # Ensure a Run row exists before any Evidence FK inserts.
    try:
        async with get_session() as session:
            await RunRepository(session).create(
                run_id=uuid.UUID(run_id),
                target_gene=target_gene,
                disease=disease,
                user_request=f"{target_gene} | {disease} | {direction}",
                direction=direction,
                population=initial_state.get("population"),
                tissue=initial_state.get("tissue"),
                step_budget_total=initial_state.get("step_budget_remaining", 200),
                model_fingerprint=model_fingerprint,
                force_refresh=force_refresh,
            )
    except Exception as exc:
        logger.warning("[pipeline] run row creation failed (may already exist): %s", exc)

    # @observe creates the root OTel span / Langfuse trace.
    # propagate_attributes sets trace-level metadata (name, tags, run_id) in the
    # same OTel context so every child span inherits them.
    # Our custom get_tracer() spans inside agents nest automatically via OTel
    # context propagation — no LangChain callback injection needed.
    @observe(name=trace_name, as_type="agent")
    async def _invoke() -> None:
        with propagate_attributes(
            trace_name=trace_name,
            tags=["pipeline"],
            metadata={
                "run_id": run_id,
                "target_gene": target_gene,
                "gene_id": gene_id,
                "disease": disease,
                "disease_id": disease_id,
                "direction": direction,
            },
        ):
            logger.info(
                "[pipeline] acquisition + screening  run_id=%s  target=%s  disease=%s  direction=%s",
                run_id,
                target_gene,
                disease,
                direction,
            )
            await graph.ainvoke(initial_state, config=config)

            snapshot = await graph.aget_state(config)
            if snapshot and snapshot.next:
                logger.info("[pipeline] HITL auto-approved, resuming reasoning")
                await graph.aupdate_state(config, {"hitl_approved": True, "hitl_overrides": {}})
                await graph.ainvoke(None, config=config)
                logger.info("[pipeline] resume complete")
            else:
                logger.info("[pipeline] complete (no HITL interrupt)")

    # Pass the per-project public key so @observe traces into the provisioned
    # project rather than skipping (multiple Langfuse clients live in-process).
    # langfuse_public_key is a reserved kwarg the decorator pops before calling.
    await _invoke(langfuse_public_key=proj_pk)

    if run_id:
        try:
            async with get_session() as session:
                rows = await EvidenceRepository(session).get_by_run(uuid.UUID(run_id))
            if rows:
                csv_uri = export_summary_csv(target_gene, disease_id, direction, rows)
                logger.info("[pipeline] evidence CSV written: %s  (%d rows)", csv_uri, len(rows))
        except Exception as exc:
            logger.warning("[pipeline] CSV export failed: %s", exc)

    # Flush the per-project client explicitly; get_client() with no key returns
    # a disabled client when multiple clients exist, so its flush() is a no-op.
    get_client(public_key=proj_pk).flush() if proj_pk else get_client().flush()


async def resume_pipeline(
    graph,
    old_thread_id: str,
    from_node: str,
    config: dict,
    force_refresh: bool = False,
) -> None:
    """Restart the pipeline from a specific node using state from a prior run.

    Loads the checkpoint stored under old_thread_id, builds a clean initial_state
    (upstream fields copied verbatim, downstream fields zeroed), and calls
    run_pipeline() on a new thread_id so Langfuse tracing, HITL auto-approval,
    and CSV export all run normally.

    Because the state is seeded into a brand-new thread, _append / replace_last
    reducers are not involved — the values are set directly, bypassing the
    accumulation that makes in-place aupdate_state unsuitable for clearing lists.
    """
    jump_target = NODE_TO_JUMP_TARGET.get(from_node)
    if jump_target is None:
        raise ValueError(
            f"Unknown --from-node {from_node!r}. Valid options: {sorted(NODE_TO_JUMP_TARGET)}"
        )

    old_config = {"configurable": {"thread_id": old_thread_id}}
    snapshot = await graph.aget_state(old_config)
    if not snapshot or not snapshot.values:
        raise ValueError(
            f"No checkpoint found for thread_id={old_thread_id!r}. "
            "Run a fresh analysis first and save the printed thread_id."
        )

    old_state: dict = dict(snapshot.values)

    # Warn if required upstream fields are empty — the restart will likely produce
    # garbage output but we don't abort so the user can override with force_refresh.
    for field in _REQUIRED_UPSTREAM.get(jump_target, []):
        if not old_state.get(field):
            logger.warning(
                "[resume_pipeline] WARNING: jumping to %r but %r is empty in the "
                "old checkpoint — output may be meaningless. "
                "Consider a wider restart (e.g. --from-node hitl_gate).",
                jump_target,
                field,
            )

    # Build fresh initial_state: copy everything, then zero downstream fields.
    new_initial_state = dict(old_state)
    # Always reset loop-safety and per-run counters so the new run gets a full budget.
    new_initial_state["loop_counters"] = {}
    new_initial_state["step_budget_remaining"] = 200
    new_initial_state["failed_sources"] = []
    new_initial_state["rerun_count"] = old_state.get("rerun_count", 0) + 1
    if force_refresh:
        new_initial_state["force_refresh"] = True
    # Apply downstream field resets for this jump target.
    new_initial_state.update(CLEAR_FROM_NODE[jump_target])

    new_thread_id = config["configurable"]["thread_id"]
    new_config = {
        **config,
        "configurable": {
            **config.get("configurable", {}),
            "thread_id": new_thread_id,
            "restart_from": jump_target,
        },
    }
    logger.info(
        "[resume_pipeline] new_thread=%s  jump=%s  old_thread=%s  rerun=%d",
        new_thread_id,
        jump_target,
        old_thread_id,
        new_initial_state["rerun_count"],
    )
    await run_pipeline(graph, new_initial_state, new_config)
