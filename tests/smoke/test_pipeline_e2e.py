# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""End-to-end smoke test: real Ollama, real external APIs, MemorySaver.

Exercises the full LangGraph pipeline for PTPN1 / pancreatic cancer:
  data-acquisition → screening → HITL gate (auto-approved) → reasoning → report

Nothing is mocked except the DB write in ReportAgent (Postgres not required).
All LLM calls go to the locally running Ollama instance.
All external data calls (PubMed, USPTO, ClinicalTrials, OpenTargets, etc.) are live.

Run:
    pytest tests/smoke/test_pipeline_e2e.py -v -s -m smoke

Requirements:
    - Ollama running at http://localhost:11434
    - qwen2.5:7b-instruct-q4_K_M pulled in Ollama
    - Internet access (PubMed, USPTO, ClinicalTrials.gov, OpenTargets)
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv()

_OLLAMA_URL = "http://localhost:11434"
_TIMEOUT_SECONDS = 1800  # 30 min — sequential LLM calls on local hardware; warmup is excluded


def _ollama_available() -> bool:
    try:
        r = httpx.get(f"{_OLLAMA_URL}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.smoke

# Skip the whole module if Ollama isn't reachable
if not _ollama_available():
    pytest.skip("Ollama not reachable at http://localhost:11434", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_initial_state(run_id: uuid.UUID) -> dict:
    return {
        "run_id": run_id,
        "target_gene": "PTPN1",
        "disease": "pancreatic cancer",
        "gene_id": "ENSG00000196396",  # Ensembl ID for PTPN1
        "disease_id": "EFO_0002618",  # EFO ID for pancreatic carcinoma
        "population": None,
        "tissue": None,
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
        "step_budget_remaining": 200,
        "loop_counters": {},
        "hitl_approved": False,
        "hitl_overrides": {},
        "messages": [],
    }


def _noop_session():
    """Minimal async context manager that satisfies ReportAgent's DB write/read."""
    scalars = MagicMock()
    scalars.all.return_value = []
    result = MagicMock()
    result.scalars.return_value = scalars

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = lambda _: None
    session.execute = AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


async def test_pipeline_ptpn1_pancreatic_cancer(tmp_path: Path) -> None:
    """Full pipeline from data-acquisition through report generation."""
    from langgraph.checkpoint.memory import MemorySaver

    import core.routing.policy as _policy_mod
    from capabilities.target_validation.workflow import build_graph, run_pipeline
    from core.routing.policy import get_policy
    from core.routing.providers.ollama import OllamaProvider
    from core.routing.router import Router

    # ── 1. Build router with real local Ollama ───────────────────────────────
    _policy_mod._cached = None  # clear cache so we read the updated routing.yaml
    policy = get_policy(Path("config/routing.yaml"))
    assert policy.policy == "all_local", (
        f"routing.yaml policy must be 'all_local' for this smoke test, got {policy.policy!r}"
    )

    ollama_cfg = policy.providers["ollama"]
    ollama = OllamaProvider(
        model=ollama_cfg.model,
        embed_model=ollama_cfg.embed_model or "nomic-embed-text:latest",
        base_url=_OLLAMA_URL,  # always localhost for the smoke test
    )
    router = Router(policy, {"ollama": ollama})

    print(f"\n[smoke] model   : {ollama_cfg.model}")
    print(f"[smoke] policy  : {policy.policy}")
    print("[smoke] target  : PTPN1 / pancreatic cancer")

    # ── 2. Warm up the model before the timed section ────────────────────────
    # The first Ollama call loads the model from disk (cold start can take 1-3 min).
    # Running warmup here keeps that latency out of _TIMEOUT_SECONDS.
    print("[smoke] warming up Ollama model (cold-start may take a few minutes) …")
    await ollama.warmup()
    print("[smoke] model warm — starting timed pipeline run")

    # ── 3. Build graph with MemorySaver (no Postgres needed) ─────────────────
    graph = build_graph(router, checkpointer=MemorySaver())

    run_id = uuid.uuid4()
    config = {"configurable": {"thread_id": str(run_id)}}
    initial_state = _make_initial_state(run_id)

    report_dir = tmp_path / "report"
    report_dir.mkdir()

    async def _run():
        with (
            patch("agents.synthesis.report.agent._REPORT_ROOT", tmp_path / "report"),
            patch("agents.synthesis.report.agent.get_session", side_effect=lambda: _noop_session()),
        ):
            print("[smoke] phase 1: running to HITL gate …")
            print("[smoke] phase 2: HITL auto-approved, resuming reasoning …")
            # run_pipeline handles propagate_attributes trace context, HITL auto-approve, and flush.
            await run_pipeline(graph, initial_state, config)

            snapshot = await graph.aget_state(config)
            assert snapshot is not None
            screened = snapshot.values.get("screened_evidence", [])
            kept = [
                e for e in screened if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
            ]
            print(f"[smoke] screened: {len(screened)} items  kept: {len(kept)}")

        # ── Verify final state ────────────────────────────────────────────────
        final = await graph.aget_state(config)
        assert final is not None
        values = final.values

        lens_verdicts = values.get("lens_verdicts", [])
        agreement_map = values.get("agreement_map")
        experiment_results = values.get("experiment_results", [])
        report_uri = values.get("report_uri")

        print(f"[smoke] lens_verdicts   : {len(lens_verdicts)}")
        print(
            f"[smoke] agreement_map   : {agreement_map.get('consensus_verdict') if agreement_map else None}"
        )
        print(f"[smoke] experiment_results: {len(experiment_results)}")
        print(f"[smoke] report_uri      : {report_uri}")

        assert lens_verdicts, "Pipeline should produce lens verdicts"
        assert agreement_map is not None, "Pipeline should produce an agreement map"
        assert experiment_results, "Pipeline should produce experiment results"
        assert report_uri, "Pipeline should produce a report_uri"

        report_path = Path(report_uri.replace("file://", ""))
        assert report_path.exists(), f"Report file missing at {report_path}"

        content = report_path.read_text()
        assert "PTPN1" in content
        assert "pancreatic cancer" in content
        assert "## Recommendations" in content
        assert "Lens Verdicts" in content or "lens" in content.lower()
        assert "consensus" in content.lower() or "Agreement" in content

        print(f"\n[smoke] ── Report preview ({len(content)} chars) ──")
        print(content[:800])
        print("[smoke] ── end preview ──")

    await asyncio.wait_for(_run(), timeout=_TIMEOUT_SECONDS)
