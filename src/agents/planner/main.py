# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Planner service entry point.

Run with: uvicorn agents.planner.main:app --host 0.0.0.0 --port 8000 --workers 1
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, HTTPException
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel

from agents.interpretation.biology_lens.agent import (
    BiologyLensAgent,  # noqa: F401 (kept for symmetry)
)
from agents.planner.agent import (
    HitlApproveRequest,
    RunRequest,
    _make_initial_state,
    _resolve_ontology_context,
)
from agents.retrieval.genetics.agent import GeneticsAgent
from agents.retrieval.literature.agent import LiteratureAgent
from agents.retrieval.omics.agent import OmicsAgent
from agents.screening.knowledge_extraction.agent import KnowledgeExtractionAgent
from agents.screening.screening.agent import ScreeningAgent
from agents.screening.source_quality.agent import SourceQualityAgent
from capabilities.target_validation.workflow import (
    _all_raw_evidence,
    _dedup_screened,
    _evidences,
    _llm_cache_set,
    _persist_evidence,
    _task_msg,
    build_graph,
)
from core.checkpoint.pg_checkpointer import get_checkpointer
from core.persistence.db import get_session
from core.persistence.models import Run
from core.persistence.repos.runs import RunRepository
from core.routing.policy import get_policy
from core.routing.providers.base import ModelProvider
from core.routing.providers.bedrock import BedrockProvider
from core.routing.providers.ollama import OllamaProvider
from core.routing.router import Router
from core.telemetry.setup import init_telemetry
from harness.context import RunContext
from mcp_servers.opentargets.tools import resolve_disease, resolve_gene
from schemas.evidence import DataClass, Evidence, source_quality_fingerprint
from schemas.state import PipelineState
from services.evidence.claim_extraction import extract_claims
from services.retrieval.clinical_trial import fetch_trials
from services.retrieval.functional import fetch_functional
from services.retrieval.opentargets import fetch_opentargets
from services.retrieval.patent import fetch_patents


class _SessionRunRepo:
    """RunRepository wrapper that opens a fresh async session per call."""

    async def create(self, **kwargs: Any) -> Run:
        async with get_session() as session:
            return await RunRepository(session).create(**kwargs)

    async def get(self, run_id: UUID) -> Run | None:
        async with get_session() as session:
            return await RunRepository(session).get(run_id)

    async def update_status(self, run_id: UUID, status: str) -> None:
        async with get_session() as session:
            await RunRepository(session).update_status(run_id, status)

    async def increment_rerun_count(self, run_id: UUID) -> None:
        async with get_session() as session:
            await RunRepository(session).increment_rerun_count(run_id)


# Module-level state populated during lifespan startup.
_graph: CompiledStateGraph[PipelineState, None, PipelineState, PipelineState] | None = None
_router: Router | None = None
_run_repo: _SessionRunRepo | None = None
_default_model_fp: str = ""


def _require_graph() -> CompiledStateGraph[PipelineState, None, PipelineState, PipelineState]:
    if _graph is None:
        raise RuntimeError("Planner graph not initialized — lifespan startup has not run")
    return _graph


def _require_run_repo() -> _SessionRunRepo:
    if _run_repo is None:
        raise RuntimeError("Run repository not initialized — lifespan startup has not run")
    return _run_repo


def _require_router() -> Router:
    if _router is None:
        raise RuntimeError("Router not initialized — lifespan startup has not run")
    return _router


@asynccontextmanager
async def _lifespan(outer_app: FastAPI) -> AsyncIterator[None]:
    global _graph, _router, _run_repo, _default_model_fp

    init_telemetry()

    policy = get_policy()
    ollama_cfg = policy.providers["ollama"]
    providers: dict[str, ModelProvider] = {
        "ollama": OllamaProvider(
            model=ollama_cfg.model,
            embed_model=ollama_cfg.embed_model or "nomic-embed-text:latest",
            base_url=ollama_cfg.base_url or "http://ollama:11434",
            num_ctx=ollama_cfg.num_ctx,
            task_num_ctx=ollama_cfg.task_num_ctx,
            timeout=ollama_cfg.timeout,
        )
    }
    if os.environ.get("BEDROCK_REGION") or os.environ.get("AZURE_OPENAI_ENDPOINT"):
        bedrock_cfg = policy.providers["bedrock"]
        providers["bedrock"] = BedrockProvider(
            model=bedrock_cfg.model,
            region=bedrock_cfg.region or os.environ.get("BEDROCK_REGION", ""),
        )
    router = Router(policy, providers)
    # Compute once at startup; re-evaluated per-request in create_run().
    _, _default_model_fp = router.select(DataClass.NON_SENSITIVE, "screening")

    async with get_checkpointer() as checkpointer:
        await checkpointer.setup()
        _graph = build_graph(router, checkpointer=checkpointer)
        _router = router
        _run_repo = _SessionRunRepo()
        yield


app = FastAPI(title="Gene Target Validation Planner", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------


async def _run_until_interrupt(run_id: UUID, initial_state: PipelineState) -> None:
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    run_repo = _require_run_repo()
    graph = _require_graph()
    try:
        await run_repo.update_status(run_id, "running")
        await graph.ainvoke(initial_state, config=config)
        snapshot = await graph.aget_state(config)
        if snapshot and snapshot.next:
            await run_repo.update_status(run_id, "hitl_wait")
        else:
            await run_repo.update_status(run_id, "done")
    except Exception:
        await run_repo.update_status(run_id, "error")
        raise


async def _resume_after_hitl(run_id: UUID) -> None:
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    run_repo = _require_run_repo()
    graph = _require_graph()
    try:
        await run_repo.update_status(run_id, "running")
        await graph.ainvoke(Command(resume=None), config=config)
        await run_repo.update_status(run_id, "done")
    except Exception:
        await run_repo.update_status(run_id, "error")
        raise


async def _rerun_reasoning(run_id: UUID) -> None:
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    run_repo = _require_run_repo()
    graph = _require_graph()
    try:
        await run_repo.update_status(run_id, "running")
        await graph.ainvoke(None, config=config)
        await run_repo.update_status(run_id, "done")
    except Exception:
        await run_repo.update_status(run_id, "error")
        raise


# ---------------------------------------------------------------------------
# Acquisition rerun helpers
# ---------------------------------------------------------------------------

_SOURCE_BUCKET: dict[str, str] = {
    "literature": "literature_evidence",
    "patent": "patent_evidence",
    "clinical_trial": "trial_evidence",
    "opentargets": "opentargets_evidence",
    "genetics": "genetics_evidence",
    "omics": "omics_evidence",
    "functional": "functional_evidence",
}


async def _fetch_source(source: str, state: PipelineState, ctx: RunContext) -> list[Evidence]:
    """Call one acquisition service/agent directly (always fresh — no cache check).

    Returns a list of Evidence objects, empty on failure (logged at WARNING).
    """
    gene = state["target_gene"]
    disease = state["disease"]
    gene_id = state.get("gene_id") or ""
    disease_id = state.get("disease_id") or ""
    run_id = state["run_id"]
    trace_id = str(run_id)
    direction = state.get("direction") or "unspecified"

    try:
        if source == "literature":
            msg = _task_msg(
                state,
                "literature",
                {
                    "target_gene": gene,
                    "disease": disease,
                    "gene_id": gene_id,
                    "disease_id": disease_id,
                    "population": state.get("population"),
                },
            )
            result = await LiteratureAgent().run(msg, ctx)
            return _evidences(result)

        if source == "patent":
            return await fetch_patents(
                gene=gene,
                disease=disease,
                gene_id=gene_id,
                disease_id=disease_id,
                run_id=run_id,
                trace_id=trace_id,
                direction=direction,
            )

        if source == "clinical_trial":
            return await fetch_trials(
                gene=gene,
                disease=disease,
                gene_id=gene_id,
                disease_id=disease_id,
                population=state.get("population"),
                run_id=run_id,
                trace_id=trace_id,
                direction=direction,
            )

        if source == "opentargets":
            ot_result = await fetch_opentargets(
                gene=gene,
                disease=disease,
                gene_id=gene_id,
                disease_id=disease_id,
                run_id=run_id,
                trace_id=trace_id,
                direction=direction,
            )
            return ot_result.evidences

        if source == "genetics":
            msg = _task_msg(
                state,
                "genetics",
                {
                    "target_gene": gene,
                    "disease": disease,
                    "gene_id": gene_id,
                    "disease_id": disease_id,
                },
            )
            result = await GeneticsAgent().run(msg, ctx)
            return _evidences(result)

        if source == "omics":
            msg = _task_msg(
                state,
                "omics",
                {
                    "target_gene": gene,
                    "disease": disease,
                    "gene_id": gene_id,
                    "disease_id": disease_id,
                    "tissue": state.get("tissue"),
                },
            )
            result = await OmicsAgent().run(msg, ctx)
            return _evidences(result)

        if source == "functional":
            return await fetch_functional(
                gene=gene,
                disease=disease,
                gene_id=gene_id,
                disease_id=disease_id,
                run_id=run_id,
                trace_id=trace_id,
                direction=direction,
            )

    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[rerun-acquisition] %s fetch failed: %s", source, exc, exc_info=True
        )
    return []


class AcquisitionRerunRequest(BaseModel):
    sources: list[str] | None = None  # None → use state.failed_sources
    auto_approve_hitl: bool = False  # True → skip HITL gate and run reasoning immediately


async def _rerun_acquisition_task(
    run_id: UUID, sources: list[str], auto_approve_hitl: bool
) -> None:
    """Background task: re-fetch specific acquisition sources, re-run the
    screening chain in-process, then re-enter the graph at hitl_gate.

    Avoids the 7-way fan-in problem at screening_first by calling agents
    directly rather than re-entering the graph at an acquisition node.
    Existing evidence is preserved (via _append reducer); old screening
    verdicts cost zero LLM calls (hit the fingerprint cache).
    """
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    run_repo = _require_run_repo()
    graph = _require_graph()
    await run_repo.update_status(run_id, "running")
    try:
        snapshot = await graph.aget_state(config)
        state = cast("PipelineState", snapshot.values)
        ctx = RunContext(run_id=run_id, trace_id=str(run_id), router=_require_router())

        # ── 1. Re-fetch each requested source (always fresh) ─────────────────
        bucket_patch: dict[str, Any] = {"failed_sources": []}
        for source in sources:
            new_ev = await _fetch_source(source, state, ctx)
            await _persist_evidence(new_ev, source)
            bucket_patch[_SOURCE_BUCKET[source]] = new_ev  # _append reducer merges
        await graph.aupdate_state(config, bucket_patch)

        # ── 2. Re-run screening chain directly on combined evidence ───────────
        # LLM cache hits make re-screening existing items essentially free.

        snapshot = await graph.aget_state(config)
        state = cast("PipelineState", snapshot.values)

        # screening_first
        msg = _task_msg(
            state,
            "screening",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "pass_type": "first",
            },
            payload=_all_raw_evidence(state),
        )
        r1 = await ScreeningAgent().run(msg, ctx)
        await graph.aupdate_state(config, {"screened_evidence": _evidences(r1)})

        # knowledge_extraction
        snapshot = await graph.aget_state(config)
        state = cast("PipelineState", snapshot.values)
        msg = _task_msg(
            state,
            "knowledge_extraction",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
            },
            payload=_dedup_screened(state),
        )
        r2 = await KnowledgeExtractionAgent().run(msg, ctx)
        await graph.aupdate_state(config, {"screened_evidence": _evidences(r2)})

        # screening_second
        snapshot = await graph.aget_state(config)
        state = cast("PipelineState", snapshot.values)
        msg = _task_msg(
            state,
            "screening",
            {
                "target_gene": state["target_gene"],
                "disease": state["disease"],
                "pass_type": "second",
            },
            payload=_dedup_screened(state),
        )
        r3 = await ScreeningAgent().run(msg, ctx)
        await graph.aupdate_state(config, {"screened_evidence": _evidences(r3)})

        # claim_extraction
        snapshot = await graph.aget_state(config)
        state = cast("PipelineState", snapshot.values)
        keep_ev = [
            e
            for e in _dedup_screened(state)
            if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
        ]
        new_claims = await extract_claims(
            keep_ev,
            target_gene=state["target_gene"],
            disease=state["disease"],
            direction=state.get("direction") or "unspecified",
            ctx=ctx,
        )

        # source_quality — must also re-run here: its cache key
        # (source_quality_fingerprint) is keyed on (gene, disease, direction)
        # only, not on the evidence set, so leaving this step to the graph
        # would silently return the pre-rerun quality map (missing scores for
        # the freshly re-fetched sources) instead of recomputing it.
        direction = state.get("direction") or "unspecified"
        sq_msg = _task_msg(
            state,
            "source_quality",
            {"target_gene": state["target_gene"], "disease": state["disease"]},
            payload=keep_ev,
        )
        sq_result = await SourceQualityAgent().run(sq_msg, ctx)
        sq_payload = sq_result.payload if isinstance(sq_result.payload, dict) else {}
        new_quality_map = sq_payload.get("source_quality", {})
        model_fp = state.get("model_fingerprint", "")
        if new_quality_map and model_fp:
            ck = source_quality_fingerprint(state["target_gene"], state["disease"], direction)
            await _llm_cache_set(ck, model_fp, "source_quality", new_quality_map)

        # ── 3. Reposition at hitl_gate; optionally auto-approve ───────────────
        # as_node="source_quality" → LangGraph sets next=["hitl_gate"]
        reposition: dict[str, Any] = {
            "extracted_claims": new_claims,
            "source_quality": new_quality_map,
            "failed_sources": [],
            "replan_count": 0,
            "replan_decision": None,
            "gap_guidance": "",
            "rerun_count": state.get("rerun_count", 0) + 1,
        }
        if auto_approve_hitl:
            reposition["hitl_approved"] = True

        await graph.aupdate_state(config, reposition, as_node="source_quality")
        await run_repo.increment_rerun_count(run_id)

        if auto_approve_hitl:
            await graph.ainvoke(None, config=config)
            await run_repo.update_status(run_id, "done")
        else:
            await run_repo.update_status(run_id, "hitl_wait")

    except Exception:
        await run_repo.update_status(run_id, "error")
        raise


# ---------------------------------------------------------------------------
# Routes (registered at module level so Starlette's routing table picks them up)
# ---------------------------------------------------------------------------


@app.post("/runs", status_code=202)
async def create_run(request: RunRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    run_id = uuid.uuid4()
    try:
        gene_id, disease_id = await asyncio.gather(
            resolve_gene(request.target_gene),
            resolve_disease(request.disease),
        )
    except Exception:
        gene_id, disease_id = "", ""
    resolved_context = await _resolve_ontology_context(request.target_gene, request.disease, "", "")
    await _require_run_repo().create(
        run_id=run_id,
        target_gene=request.target_gene,
        disease=request.disease,
        direction=request.direction,
        population=request.population,
        user_request=f"{request.target_gene} | {request.disease} | {request.direction}",
        step_budget_total=request.step_budget,
        model_fingerprint=_default_model_fp,
        force_refresh=request.force_refresh,
    )
    initial_state = _make_initial_state(
        run_id,
        request,
        gene_id=gene_id,
        disease_id=disease_id,
        model_fingerprint=_default_model_fp,
        resolved_context=resolved_context,
    )
    background_tasks.add_task(_run_until_interrupt, run_id, initial_state)
    return {"run_id": str(run_id), "status": "pending"}


@app.get("/runs/{run_id}")
async def get_run(run_id: UUID) -> dict[str, Any]:
    run = await _require_run_repo().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    snapshot = await _require_graph().aget_state(config)
    failed_lenses: list[str] = []
    failed_sources: list[str] = []
    if snapshot and snapshot.values:
        failed_lenses = list(set(snapshot.values.get("failed_lenses", [])))
        failed_sources = list(set(snapshot.values.get("failed_sources", [])))
    return {
        "run_id": str(run.id),
        "status": run.status,
        "step_budget_consumed": run.step_budget_consumed,
        "created_at": run.created_at.isoformat(),
        "failed_lenses": failed_lenses,
        "failed_sources": failed_sources,
        "rerun_count": run.rerun_count,
    }


@app.post("/runs/{run_id}/rerun", status_code=202)
async def rerun_reasoning(run_id: UUID, background_tasks: BackgroundTasks) -> dict[str, Any]:
    run = await _require_run_repo().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Run is already in progress")

    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    snapshot = await _require_graph().aget_state(config)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Run state not found in checkpoint store")

    previously_failed = list(set(snapshot.values.get("failed_lenses", [])))

    # Reposition the checkpoint as if hitl_gate just completed. This makes
    # LangGraph set next=[all 6 lenses] so ainvoke(None) replays the full
    # reasoning phase (lenses→experiment→critic/reviewer/reconciler→gap→report)
    # without repeating the expensive acquisition+screening phase.
    # lens_verdicts is NOT cleared: the _append reducer accumulates new verdicts
    # and the reconciler's dict-comprehension naturally takes the latest per lens.
    await _require_graph().aupdate_state(
        config,
        {
            "replan_count": 0,
            "replan_decision": None,
            "gap_guidance": "",
            "failed_lenses": [],
            "rerun_count": snapshot.values.get("rerun_count", 0) + 1,
        },
        as_node="hitl_gate",
    )
    await _require_run_repo().increment_rerun_count(run_id)
    background_tasks.add_task(_rerun_reasoning, run_id)
    return {"status": "rerunning", "previously_failed_lenses": previously_failed}


@app.post("/runs/{run_id}/rerun-acquisition", status_code=202)
async def rerun_acquisition(
    run_id: UUID, body: AcquisitionRerunRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    run = await _require_run_repo().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Run is already in progress")

    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    snapshot = await _require_graph().aget_state(config)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Run state not found in checkpoint store")

    sources = body.sources or list(set(snapshot.values.get("failed_sources", [])))
    invalid = [s for s in sources if s not in _SOURCE_BUCKET]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sources: {invalid}. Valid: {sorted(_SOURCE_BUCKET)}",
        )
    if not sources:
        raise HTTPException(
            status_code=422,
            detail="No sources specified and no failed_sources recorded in run state",
        )

    background_tasks.add_task(_rerun_acquisition_task, run_id, sources, body.auto_approve_hitl)
    return {
        "status": "rerunning_acquisition",
        "sources": sources,
        "auto_approve_hitl": body.auto_approve_hitl,
    }


@app.get("/runs/{run_id}/hitl")
async def get_hitl(run_id: UUID) -> dict[str, Any]:
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    snapshot = await _require_graph().aget_state(config)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Run state not found")
    state = cast("PipelineState", snapshot.values)
    screened = state.get("screened_evidence", [])
    return {
        "screened_evidence": [e.model_dump() for e in screened],
        "verdicts": {str(e.evidence_id): e.extra.get("screening_verdict", {}) for e in screened},
    }


@app.post("/runs/{run_id}/hitl/approve")
async def approve_hitl(
    run_id: UUID, body: HitlApproveRequest, background_tasks: BackgroundTasks
) -> dict[str, str]:
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    await _require_graph().aupdate_state(
        config,
        {"hitl_approved": True, "hitl_overrides": body.overrides},
    )
    background_tasks.add_task(_resume_after_hitl, run_id)
    return {"status": "resumed"}


@app.get("/runs/{run_id}/report")
async def get_report(run_id: UUID) -> dict[str, str]:
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    snapshot = await _require_graph().aget_state(config)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Run not found")
    state = cast("PipelineState", snapshot.values)
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
