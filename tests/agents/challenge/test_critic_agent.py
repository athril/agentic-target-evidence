# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for CriticAgent — three-pass challenge."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.challenge.critic.agent import CriticAgent
from core.routing.providers.base import CompletionResult
from tests.agents.conftest import make_evidence, make_task_msg


def _make_completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content,
        model_used="test-model",
        input_tokens=40,
        output_tokens=60,
        latency_ms=120.0,
    )


@pytest.fixture()
def critic_ctx(run_id, trace_id):
    provider = MagicMock()
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    from harness.context import RunContext

    return RunContext(run_id=run_id, trace_id=trace_id, router=router), provider


# ---------------------------------------------------------------------------
# Agent behaviour — Pass 1 (source-quality lookup, no LLM call)
# ---------------------------------------------------------------------------


async def test_critic_agent_reads_precomputed_source_quality(run_id, trace_id, critic_ctx):
    ctx, provider = critic_ctx
    ev = make_evidence(run_id, trace_id, extra={"screening_verdict": {"verdict": "keep"}})
    quality_map = {
        str(ev.evidence_id): {
            "sjr_score": 0.75,
            "impact_factor": 8.2,
            "sjr_quartile": "Q1",
            "novelty_flag": False,
            "predatory_flag": False,
            "preprint_flag": False,
            "quality_note": "SJR Q1; no concerns.",
        }
    }

    msg = make_task_msg(
        "critic",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "source_quality": quality_map,
        },
        run_id,
        trace_id,
        payload=[ev],
    )

    result = await CriticAgent().run(msg, ctx)

    # Pass 1 is a lookup, not an LLM call.
    provider.complete.assert_not_called()
    assert result.intent == "result"
    critiques = result.payload["critiques"]
    assert len(critiques) == 1
    assert critiques[0]["evidence_id"] == str(ev.evidence_id)
    assert critiques[0]["sjr_score"] == 0.75
    assert critiques[0]["quality_challenge"] == "SJR Q1; no concerns."


async def test_critic_agent_source_quality_miss_falls_back(run_id, trace_id, critic_ctx):
    ctx, provider = critic_ctx
    ev = make_evidence(run_id, trace_id, extra={"screening_verdict": {"verdict": "keep"}})

    msg = make_task_msg(
        "critic",
        {"target_gene": "BRCA1", "disease": "breast cancer", "source_quality": {}},
        run_id,
        trace_id,
        payload=[ev],
    )

    result = await CriticAgent().run(msg, ctx)

    provider.complete.assert_not_called()
    critiques = result.payload["critiques"]
    assert len(critiques) == 1
    assert critiques[0]["sjr_score"] is None
    assert "no precomputed source-quality entry" in critiques[0]["quality_challenge"]


async def test_critic_agent_skips_dropped_evidence(run_id, trace_id, critic_ctx):
    ctx, provider = critic_ctx
    dropped_ev = make_evidence(run_id, trace_id, extra={"screening_verdict": {"verdict": "drop"}})

    msg = make_task_msg(
        "critic",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[dropped_ev],
    )

    result = await CriticAgent().run(msg, ctx)

    provider.complete.assert_not_called()
    assert result.payload == {"critiques": []}


async def test_critic_agent_empty_payload_returns_empty(run_id, trace_id, critic_ctx):
    ctx, _ = critic_ctx
    msg = make_task_msg(
        "critic",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[],
    )

    result = await CriticAgent().run(msg, ctx)

    assert result.payload == {"critiques": []}


# ---------------------------------------------------------------------------
# Pass 3: verdict-QA
# ---------------------------------------------------------------------------


_SAMPLE_VERDICTS = [
    {
        "schema_version": "1.0",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "trace_id": "trace-test",
        "lens": "genetics",
        "target_gene": "BRCA1",
        "disease": "breast cancer",
        "overall_verdict": "support",
        "confidence": 0.9,
        "axes": [],
        "rationale": "Strong GWAS evidence.",
    },
    {
        "schema_version": "1.0",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "trace_id": "trace-test",
        "lens": "safety",
        "target_gene": "BRCA1",
        "disease": "breast cancer",
        "overall_verdict": "oppose",
        "confidence": 0.7,
        "axes": [],
        "rationale": "High off-target expression.",
    },
]

_VERDICT_QA_RESPONSE = json.dumps(
    [
        {
            "issue_type": "conflict",
            "affected_lenses": ["genetics", "safety"],
            "description": "Genetics supports while safety opposes — conflict requires human review.",
            "severity": "high",
        }
    ]
)


async def test_critic_agent_verdict_qa_pass_runs_when_lens_verdicts_present(
    run_id, trace_id, critic_ctx
):
    ctx, provider = critic_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_VERDICT_QA_RESPONSE))

    msg = make_task_msg(
        "critic",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "lens_verdicts": _SAMPLE_VERDICTS,
        },
        run_id,
        trace_id,
        payload=[],  # no screened evidence → passes 1+2 skipped
    )

    result = await CriticAgent().run(msg, ctx)

    assert result.intent == "result"
    critiques = result.payload["critiques"]
    verdict_qa = next((c for c in critiques if c.get("pass") == "verdict_qa"), None)
    assert verdict_qa is not None
    assert verdict_qa["lens_count"] == 2
    issues = verdict_qa["issues"]
    assert len(issues) == 1
    assert issues[0]["issue_type"] == "conflict"


async def test_critic_agent_verdict_qa_skipped_when_no_verdicts(run_id, trace_id, critic_ctx):
    ctx, provider = critic_ctx
    provider.complete = AsyncMock(return_value=_make_completion("[]"))

    msg = make_task_msg(
        "critic",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "lens_verdicts": [],
        },
        run_id,
        trace_id,
        payload=[],
    )

    result = await CriticAgent().run(msg, ctx)

    provider.complete.assert_not_called()
    assert result.payload["critiques"] == []


async def test_critic_agent_verdict_qa_fallback_on_bad_json(run_id, trace_id, critic_ctx):
    ctx, provider = critic_ctx
    provider.complete = AsyncMock(return_value=_make_completion("not json"))

    msg = make_task_msg(
        "critic",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "lens_verdicts": _SAMPLE_VERDICTS,
        },
        run_id,
        trace_id,
        payload=[],
    )

    result = await CriticAgent().run(msg, ctx)

    critiques = result.payload["critiques"]
    verdict_qa = next((c for c in critiques if c.get("pass") == "verdict_qa"), None)
    assert verdict_qa is not None
    assert "unparseable" in str(verdict_qa["issues"])
