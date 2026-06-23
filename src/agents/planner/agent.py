# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""PlannerAgent request models and state helpers.

Shared building blocks for the gene-target validation pipeline: the user-facing
request models and the helpers that seed PipelineState. The live FastAPI app and
its background-task drivers are defined in ``agents/planner/main.py``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from pydantic import BaseModel

from mcp_servers.ontology.tools import resolve_hgnc_symbol, resolve_mondo_term

DEFAULT_STEP_BUDGET = 200


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    target_gene: str
    disease: str
    direction: str = "unspecified"
    population: str | None = None
    tissue: str | None = None
    step_budget: int = DEFAULT_STEP_BUDGET
    force_refresh: bool = False
    # Optional caller-supplied IDs bypass automatic resolution via Open Targets search.
    # Use these when you know the correct EFO/MONDO/Ensembl ID and want a deterministic run.
    gene_id: str | None = None
    disease_id: str | None = None


class HitlApproveRequest(BaseModel):
    overrides: dict[str, bool] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_ontology_context(
    target_gene: str, disease: str, gene_id: str, disease_id: str
) -> dict:
    """Best-effort HGNC/MONDO enrichment alongside the existing Open Targets
    gene_id/disease_id resolution.

    HGNC catches gene aliases/previous symbols that Open Targets' fuzzy search
    has no visibility into; MONDO adds disease cross-references (OMIM/DOID/EFO)
    for later crosswalking. Only queried when the corresponding id is missing
    (mirrors the existing resolve_gene/resolve_disease skip-if-supplied rule).
    A lookup failure never blocks the pipeline — it just leaves resolved_context
    sparse.
    """
    resolved_context: dict = {}
    lookups: list[tuple[str, object]] = []
    if not gene_id:
        lookups.append(("gene", resolve_hgnc_symbol(target_gene)))
    if not disease_id:
        lookups.append(("disease", resolve_mondo_term(disease)))
    if not lookups:
        return resolved_context

    results = await asyncio.gather(*(coro for _, coro in lookups), return_exceptions=True)
    for (kind, _), result in zip(lookups, results, strict=True):
        if isinstance(result, Exception):
            continue
        if kind == "gene":
            resolved_context["hgnc_symbol"] = result.symbol
            resolved_context["gene_aliases"] = result.aliases
        else:
            resolved_context["mondo_id"] = result.mondo_id
            resolved_context["mondo_label"] = result.label
            for xref_key, ctx_key in (
                ("efo", "efo_id"),
                ("omim", "omim_xref"),
                ("doid", "doid_xref"),
            ):
                if result.xrefs.get(xref_key):
                    resolved_context[ctx_key] = result.xrefs[xref_key]
    return resolved_context


def _make_initial_state(
    run_id: UUID,
    req: RunRequest,
    gene_id: str = "",
    disease_id: str = "",
    model_fingerprint: str = "",
    resolved_context: dict | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "target_gene": req.target_gene,
        "disease": req.disease,
        "direction": req.direction,
        "population": req.population,
        "tissue": req.tissue,
        "gene_id": gene_id,
        "disease_id": disease_id,
        "resolved_context": resolved_context or {},
        "model_fingerprint": model_fingerprint,
        "force_refresh": req.force_refresh,
        "literature_evidence": [],
        "patent_evidence": [],
        "trial_evidence": [],
        "opentargets_evidence": [],
        "genetics_evidence": [],
        "omics_evidence": [],
        "functional_evidence": [],
        "screened_evidence": [],
        "extracted_claims": [],
        "lens_verdicts": [],
        "agreement_map": None,
        "experiment_results": [],
        "critiques": [],
        "review_gaps": [],
        "report_uri": None,
        "replan_decision": None,
        "gap_guidance": "",
        "replan_count": 0,
        "investigation_summary": "",
        "investigation_tools_used": [],
        "step_budget_remaining": req.step_budget,
        "loop_counters": {},
        "hitl_approved": False,
        "hitl_overrides": {},
        "failed_lenses": [],
        "failed_sources": [],
        "rerun_count": 0,
        "messages": [],
    }
