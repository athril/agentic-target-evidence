# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Annotated, Any, TypedDict
from uuid import UUID

from .evidence import CoreClaim, Evidence
from .messages import AgentMessage
from .verdicts import LensVerdict


def replace_last(old: list[Any], new: list[Any]) -> list[Any]:
    """Reducer for stage outputs: the newest write wins, empty update is a no-op."""
    return new if new else old


def _append(old: list[Any], new: list[Any]) -> list[Any]:
    """Reducer for evidence buckets: accumulate across retries without dedup."""
    return old + new


def merge_degradations(
    old: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Reducer for the degradation ledger (core/degradations.py): one entry per
    (component, cause), the first node's kept."""
    out = list(old or [])
    seen = {(d.get("component"), d.get("cause")) for d in out}
    for d in new or []:
        key = (d.get("component"), d.get("cause"))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _merge_by_lens(old: list[LensVerdict], new: list[LensVerdict]) -> list[LensVerdict]:
    """Reducer for lens verdicts: keep the most recent verdict per lens name."""
    merged: dict[str, LensVerdict] = {lv.lens: lv for lv in old}
    for lv in new:
        merged[lv.lens] = lv
    return list(merged.values())


def _union(old: list[str], new: list[str]) -> list[str]:
    """Set-union reducer for string lists; used for failed_lenses tracking."""
    return list(set(old) | set(new))


class PipelineState(TypedDict):
    # ── Run identity ──────────────────────────────────────────────────────────
    run_id: UUID
    target_gene: str
    disease: str
    direction: str  # inhibit | activate | degrade | modulate | unspecified
    population: str | None
    tissue: str | None
    gene_id: str  # Ensembl ID (e.g. ENSG00000012048) for database lookups
    disease_id: str  # EFO/MONDO ID (e.g. EFO_0000305) for database lookups
    resolved_context: dict[
        str, Any
    ]  # Resolver output: hgnc_symbol, gene_aliases, mondo_id, mondo_label, efo_id, omim_xref, doid_xref

    # ── Evidence buckets (accumulate across retries) ──────────────────────────
    literature_evidence: Annotated[list[Evidence], _append]
    patent_evidence: Annotated[list[Evidence], _append]
    trial_evidence: Annotated[list[Evidence], _append]
    opentargets_evidence: Annotated[list[Evidence], _append]
    genetics_evidence: Annotated[list[Evidence], _append]
    omics_evidence: Annotated[list[Evidence], _append]
    functional_evidence: Annotated[list[Evidence], _append]
    druggability_evidence: Annotated[list[Evidence], _append]
    openfda_evidence: Annotated[list[Evidence], _append]
    gbd_evidence: Annotated[list[Evidence], _append]
    # Disease-keyed competition aggregate (any mechanism) — deliberately NOT in
    # _EVIDENCE_BUCKETS (workflow.py); it is a count, not a screenable claim, and
    # is read straight from this bucket by the commercial lens.
    competition_evidence: Annotated[list[Evidence], _append]
    screened_evidence: Annotated[list[Evidence], _append]
    extracted_claims: Annotated[list[CoreClaim], _append]  # atomic claims (post-extraction)
    # Claims left out of extraction, or kept without their effect, and why
    # (services/evidence/claim_faithfulness.DroppedClaim, dumped).
    dropped_claims: Annotated[list[dict[str, Any]], _append]
    # What each selected literature sentence says about the target, read from grounded
    # mentions (schemas/statement.TargetStatement, dumped); cleared with the claims.
    target_statements: list[dict[str, Any]]
    source_quality: dict[str, Any]  # evidence_id (str) → SJR/quality assessment; latest-write-wins

    # ── Downstream outputs (each stage fully replaces previous) ───────────────
    lens_verdicts: Annotated[list[LensVerdict], _merge_by_lens]  # one per lens; newest wins
    agreement_map: dict[str, Any] | None  # AgreementMap.model_dump
    # ProgrammeReading.model_dump: how strongly the failed programmes falsify the mechanism
    # (services/evidence/programme_outcomes.py); set by the clinical lens node, read by the
    # experiment and report nodes. Last writer wins.
    programme_reading: dict[str, Any] | None
    experiment_results: Annotated[list[dict[str, Any]], replace_last]
    critiques: Annotated[list[dict[str, Any]], _append]
    review_gaps: Annotated[list[dict[str, Any]], replace_last]
    report_uri: str | None
    full_report_uri: str | None

    # ── Investigator (post-challenge tool-calling enrichment) ─────────────
    investigation_summary: str  # evidence-grounded note from InvestigatorAgent; "" if skipped
    investigation_tools_used: list[str]  # retrieval tool names called during the investigation
    investigation_error: str  # why the investigation stopped without an assessment; "" if not

    # ── Loop safety ───────────────────────────────────────────────────────────
    step_budget_remaining: int
    loop_counters: dict[str, int]  # edge_key → count; enforced by LoopGuard

    # ── Human-in-the-loop ─────────────────────────────────────────────────────
    hitl_approved: bool
    hitl_overrides: dict[str, bool]  # evidence_id (str) → keep=True / drop=False

    # ── Partial-rerun tracking ────────────────────────────────────────────────
    failed_lenses: Annotated[list[str], _union]  # lens names that threw on this run
    failed_sources: Annotated[list[str], _union]  # acquisition source names that threw
    # What the run went on without: failed lookups and fallbacks recorded by any code a
    # node ran (core/degradations.py), one entry per component and cause.
    degradations: Annotated[list[dict[str, Any]], merge_degradations]
    rerun_count: int  # incremented each time /rerun is called

    # ── Rerun caching ────────────────────────────────────────────────────────
    model_fingerprint: str  # router.select() model name at run start; LLM cache discriminator
    force_refresh: bool  # True → bypass both evidence and LLM caches for this run

    # ── A2A message log (full traceability) ───────────────────────────────────
    messages: Annotated[list[AgentMessage], _append]
