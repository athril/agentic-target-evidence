# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Run the gene-target validation pipeline from the command line.

Usage:
    make run
    make run GENE=BRCA1 DISEASE="breast cancer"
    uv run python run_analysis.py PTPN1 "pancreatic cancer"
    uv run python run_analysis.py --resume <thread-id>
    uv run python run_analysis.py --resume <thread-id> --from-node experiment
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S"
)
for _noisy in ("httpx", "httpcore", "opentelemetry", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

_RUNS_FILE = Path("results/runs.json")


def _save_thread_id(run_id: uuid.UUID, gene: str, disease: str) -> None:
    """Append run metadata to results/runs.json for later --resume lookup."""
    _RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    runs = json.loads(_RUNS_FILE.read_text()) if _RUNS_FILE.exists() else []
    runs.append(
        {
            "thread_id": str(run_id),
            "gene": gene,
            "disease": disease,
            "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
    )
    _RUNS_FILE.write_text(json.dumps(runs, indent=2))


async def main(
    gene: str,
    disease: str,
    tissue: str | None,
    population: str | None,
    direction: str = "unspecified",
    force_refresh: bool = False,
    resume_thread_id: str | None = None,
    from_node: str | None = None,
) -> None:
    from capabilities.target_validation.workflow import (
        build_graph,
        resume_pipeline,
        run_pipeline,
    )
    from core.checkpoint.pg_checkpointer import get_checkpointer
    from core.routing.policy import get_policy
    from core.routing.providers.ollama import OllamaProvider
    from core.routing.router import Router
    from mcp_servers.opentargets.tools import resolve_disease, resolve_gene
    from schemas.evidence import DataClass

    policy = get_policy(Path("config/routing.yaml"))
    ollama_cfg = policy.providers["ollama"]
    ollama = OllamaProvider(
        model=ollama_cfg.model,
        embed_model=ollama_cfg.embed_model or "nomic-embed-text:latest",
        base_url="http://localhost:11434",
        num_ctx=ollama_cfg.num_ctx,
        task_num_ctx=ollama_cfg.task_num_ctx,
        timeout=ollama_cfg.timeout,
    )
    router = Router(policy, {"ollama": ollama})

    # Compute the model fingerprint used as the LLM cache discriminator.
    # When the model changes (routing.yaml edit), prior LLM decisions are misses.
    _, model_fingerprint = router.select(DataClass.NON_SENSITIVE, "screening")

    print(f"Model  : {ollama_cfg.model}")
    if resume_thread_id:
        print(f"Resume : {resume_thread_id}  from-node={from_node}")
    else:
        print(f"Target : {gene} | {disease}")
    if force_refresh:
        print("Cache  : BYPASSED (--force-refresh)")

    # Skip warmup for late-stage restarts that don't need the model preloaded.
    skip_warmup = bool(resume_thread_id and from_node in {"report", "gap_detection"})
    if not skip_warmup:
        print("Warming up Ollama…")
        await ollama.warmup()
    print("Starting pipeline")

    async with get_checkpointer() as checkpointer:
        await checkpointer.setup()
        graph = build_graph(router, checkpointer=checkpointer)
        run_id = uuid.uuid4()
        config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}

        if resume_thread_id:
            print(f"Thread : {run_id}  ← restart of {resume_thread_id}")
            assert from_node is not None, "--from-node is required with --resume"
            try:
                await resume_pipeline(
                    graph,
                    old_thread_id=resume_thread_id,
                    from_node=from_node,
                    config=config,
                    force_refresh=force_refresh,
                )
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Thread : {run_id}  ← save this for --resume")
            _save_thread_id(run_id, gene, disease)
            gene_id_result, disease_id_result = await asyncio.gather(
                resolve_gene(gene), resolve_disease(disease), return_exceptions=True
            )
            gene_id = gene_id_result if isinstance(gene_id_result, str) else ""
            if isinstance(disease_id_result, BaseException):
                print(
                    f"ERROR: failed to resolve disease '{disease}': {disease_id_result}",
                    file=sys.stderr,
                )
                sys.exit(1)
            disease_id = disease_id_result
            if gene_id:
                print(f"Gene ID: {gene_id}")
            if disease_id:
                print(f"Disease: {disease_id}")
            print(f"Direction: {direction}")
            initial_state = {
                "run_id": run_id,
                "target_gene": gene,
                "disease": disease,
                "direction": direction,
                "gene_id": gene_id,
                "disease_id": disease_id,
                "population": population,
                "tissue": tissue,
                "model_fingerprint": model_fingerprint,
                "force_refresh": force_refresh,
                "literature_evidence": [],
                "patent_evidence": [],
                "trial_evidence": [],
                "opentargets_evidence": [],
                "genetics_evidence": [],
                "omics_evidence": [],
                "functional_evidence": [],
                "druggability_evidence": [],
                "openfda_evidence": [],
                "gbd_evidence": [],
                "screened_evidence": [],
                "extracted_claims": [],
                "lens_verdicts": [],
                "agreement_map": None,
                "experiment_results": [],
                "critiques": [],
                "review_gaps": [],
                "report_uri": None,
                "full_report_uri": None,
                "replan_decision": None,
                "gap_guidance": "",
                "replan_count": 0,
                "investigation_summary": "",
                "investigation_tools_used": [],
                "step_budget_remaining": 200,
                "loop_counters": {},
                "hitl_approved": False,
                "hitl_overrides": {},
                "failed_lenses": [],
                "failed_sources": [],
                "rerun_count": 0,
                "messages": [],
            }
            await run_pipeline(graph, initial_state, config)

        snapshot = await graph.aget_state(config)
        if snapshot:
            report_uri = snapshot.values.get("report_uri")
            if report_uri:
                path = report_uri.replace("file://", "")
                print("\n" + "=" * 70)
                print(Path(path).read_text())
            else:
                print("Pipeline complete — no report URI in state")


if __name__ == "__main__":
    # Import the valid node list early for argparse validation (before Ollama warmup).
    from capabilities.target_validation.workflow import NODE_TO_JUMP_TARGET

    parser = argparse.ArgumentParser(description="Gene target validation pipeline")
    parser.add_argument(
        "gene",
        nargs="?",
        default=None,
        help="Target gene, e.g. PTPN1 (required for fresh runs; inferred from checkpoint on --resume)",
    )
    parser.add_argument(
        "disease",
        nargs="?",
        default=None,
        help="Disease, e.g. 'pancreatic cancer' (required for fresh runs; inferred on --resume)",
    )
    parser.add_argument("--tissue", default=None)
    parser.add_argument("--population", default=None)
    parser.add_argument(
        "--direction",
        default="unspecified",
        choices=["inhibit", "activate", "degrade", "modulate", "unspecified"],
        help="Therapeutic hypothesis direction (v0.5 entity dimension)",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        default=False,
        help="Bypass all rerun caches — re-fetch evidence and re-run all LLM decisions",
    )
    parser.add_argument(
        "--resume",
        metavar="THREAD_ID",
        default=None,
        help="Thread ID of a prior run (printed at run start; also saved in results/runs.json)",
    )
    parser.add_argument(
        "--from-node",
        metavar="NODE",
        default=None,
        help=(
            "Node to restart from when using --resume (default: report). "
            f"Valid: {', '.join(sorted(NODE_TO_JUMP_TARGET))}"
        ),
    )
    args = parser.parse_args()

    if args.resume:
        # Default restart point is report — the most common quick fix.
        if args.from_node is None:
            args.from_node = "report"
        if args.from_node not in NODE_TO_JUMP_TARGET:
            parser.error(
                f"Unknown --from-node {args.from_node!r}. Valid: {sorted(NODE_TO_JUMP_TARGET)}"
            )
        # gene/disease are optional when resuming (values come from the checkpoint).
        if args.gene is None:
            args.gene = ""
        if args.disease is None:
            args.disease = ""
    else:
        if not args.gene or not args.disease:
            parser.error("gene and disease are required for fresh runs")
        if args.from_node:
            parser.error("--from-node requires --resume")

    asyncio.run(
        main(
            args.gene,
            args.disease,
            args.tissue,
            args.population,
            args.direction,
            args.force_refresh,
            resume_thread_id=args.resume,
            from_node=args.from_node,
        )
    )
