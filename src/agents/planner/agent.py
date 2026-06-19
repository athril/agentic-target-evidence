# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""PlannerAgent REST API.

Entry point for the gene-target validation pipeline. Exposes the user-facing
REST API, seeds PipelineState, drives the LangGraph graph, and manages the
HITL gate.

Usage:
    graph = build_graph(router, checkpointer=checkpointer)
    app = create_app(graph, run_repo)
"""

from __future__ import annotations

import asyncio
import uuid
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from mcp_servers.ontology.tools import resolve_hgnc_symbol, resolve_mondo_term
from mcp_servers.opentargets.tools import resolve_disease, resolve_gene

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
        "step_budget_remaining": req.step_budget,
        "loop_counters": {},
        "hitl_approved": False,
        "hitl_overrides": {},
        "failed_lenses": [],
        "failed_sources": [],
        "rerun_count": 0,
        "messages": [],
    }


async def _run_until_interrupt(graph, run_id: UUID, initial_state: dict, run_repo) -> None:
    config = {"configurable": {"thread_id": str(run_id)}}
    try:
        await run_repo.update_status(run_id, "running")
        await graph.ainvoke(initial_state, config=config)
        snapshot = await graph.aget_state(config)
        # snapshot.next is non-empty when the graph is paused at an interrupt
        if snapshot and snapshot.next:
            await run_repo.update_status(run_id, "hitl_wait")
        else:
            await run_repo.update_status(run_id, "done")
    except Exception:
        await run_repo.update_status(run_id, "error")
        raise


async def _resume_after_hitl(graph, run_id: UUID, run_repo) -> None:
    config = {"configurable": {"thread_id": str(run_id)}}
    try:
        await run_repo.update_status(run_id, "running")
        # Command(resume=None) resumes execution from the interrupt() call
        await graph.ainvoke(Command(resume=None), config=config)
        await run_repo.update_status(run_id, "done")
    except Exception:
        await run_repo.update_status(run_id, "error")
        raise


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------


def create_app(graph, run_repo) -> FastAPI:
    """Create the Planner FastAPI app bound to *graph* and *run_repo*."""
    app = FastAPI(title="Gene Target Validation Planner")

    @app.post("/runs", status_code=202)
    async def create_run(request: RunRequest, background_tasks: BackgroundTasks):
        run_id = uuid.uuid4()
        gene_id = request.gene_id or ""
        disease_id = request.disease_id or ""
        try:
            resolvers = []
            if not gene_id:
                resolvers.append(resolve_gene(request.target_gene))
            if not disease_id:
                resolvers.append(resolve_disease(request.disease))
            if resolvers:
                resolved = await asyncio.gather(*resolvers, return_exceptions=True)
                idx = 0
                if not gene_id:
                    result = resolved[idx]
                    gene_id = result if isinstance(result, str) else ""
                    idx += 1
                if not disease_id:
                    result = resolved[idx]
                    disease_id = result if isinstance(result, str) else ""
        except Exception:
            pass
        resolved_context = await _resolve_ontology_context(
            request.target_gene, request.disease, gene_id, disease_id
        )
        await run_repo.create(
            run_id=run_id,
            target_gene=request.target_gene,
            disease=request.disease,
            population=request.population,
            user_request=f"{request.target_gene} | {request.disease}",
            step_budget_total=request.step_budget,
        )
        initial_state = _make_initial_state(
            run_id,
            request,
            gene_id=gene_id,
            disease_id=disease_id,
            resolved_context=resolved_context,
        )
        background_tasks.add_task(_run_until_interrupt, graph, run_id, initial_state, run_repo)
        return {"run_id": str(run_id), "status": "pending"}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: UUID):
        run = await run_repo.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {
            "run_id": str(run.id),
            "status": run.status,
            "step_budget_consumed": run.step_budget_consumed,
            "created_at": run.created_at.isoformat(),
        }

    @app.get("/runs/{run_id}/hitl")
    async def get_hitl(run_id: UUID):
        config = {"configurable": {"thread_id": str(run_id)}}
        snapshot = await graph.aget_state(config)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Run state not found")
        state = snapshot.values
        screened = state.get("screened_evidence", [])
        return {
            "screened_evidence": [e.model_dump() for e in screened],
            "verdicts": {
                str(e.evidence_id): e.extra.get("screening_verdict", {}) for e in screened
            },
        }

    @app.post("/runs/{run_id}/hitl/approve")
    async def approve_hitl(
        run_id: UUID, body: HitlApproveRequest, background_tasks: BackgroundTasks
    ):
        config = {"configurable": {"thread_id": str(run_id)}}
        await graph.aupdate_state(
            config,
            {"hitl_approved": True, "hitl_overrides": body.overrides},
        )
        background_tasks.add_task(_resume_after_hitl, graph, run_id, run_repo)
        return {"status": "resumed"}

    @app.get("/runs/{run_id}/report")
    async def get_report(run_id: UUID):
        config = {"configurable": {"thread_id": str(run_id)}}
        snapshot = await graph.aget_state(config)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Run not found")
        state = snapshot.values
        report_uri = state.get("report_uri")
        if not report_uri:
            raise HTTPException(status_code=404, detail="Report not yet available")
        content_md = ""
        try:
            path = report_uri.replace("file://", "")
            with open(path) as f:
                content_md = f.read()
        except (FileNotFoundError, OSError):
            pass
        return {"report_uri": report_uri, "content_md": content_md}

    return app
