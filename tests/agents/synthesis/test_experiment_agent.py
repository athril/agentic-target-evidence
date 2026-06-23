# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ExperimentAgent — lens verdicts drive experiment proposals."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.synthesis.experiment.agent import ExperimentAgent, _parse_results
from core.routing.providers.base import CompletionResult
from tests.agents.conftest import make_evidence, make_task_msg


def _make_completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content,
        model_used="test-model",
        input_tokens=30,
        output_tokens=80,
        latency_ms=150.0,
    )


_SAMPLE_RESULTS = json.dumps(
    [
        {
            "target": "BRCA1",
            "score": 78,
            "rationale": "Strong causality and tractability.",
            "supporting_evidence_ids": [],
        },
    ]
)

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
        "axes": [
            {
                "axis": "causality",
                "verdict": True,
                "confidence": 0.9,
                "rationale": "Strong GWAS evidence.",
                "supporting_claim_ids": [],
            }
        ],
        "rationale": "Strong genetic causality.",
    },
    {
        "schema_version": "1.0",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "trace_id": "trace-test",
        "lens": "biology",
        "target_gene": "BRCA1",
        "disease": "breast cancer",
        "overall_verdict": "support",
        "confidence": 0.85,
        "axes": [
            {
                "axis": "druggability",
                "verdict": True,
                "confidence": 0.85,
                "rationale": "Tractable binding site.",
                "supporting_claim_ids": [],
            }
        ],
        "rationale": "Good druggability profile.",
    },
]


@pytest.fixture()
def exp_ctx(run_id, trace_id):
    provider = MagicMock()
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    from harness.context import RunContext

    return RunContext(run_id=run_id, trace_id=trace_id, router=router), provider


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_parse_results_returns_list():
    result = _parse_results(_SAMPLE_RESULTS)
    assert len(result) == 1
    assert result[0]["score"] == 78


def test_parse_results_fallback_on_bad_json():
    result = _parse_results("bad json")
    assert result == []


# ---------------------------------------------------------------------------
# Agent behaviour
# ---------------------------------------------------------------------------


async def test_experiment_agent_returns_experiment_results(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_SAMPLE_RESULTS))

    evidences = [make_evidence(run_id, trace_id) for _ in range(2)]
    msg = make_task_msg(
        "experiment",
        {"target_gene": "BRCA1", "disease": "breast cancer", "lens_verdicts": _SAMPLE_VERDICTS},
        run_id,
        trace_id,
        payload=evidences,
    )

    result = await ExperimentAgent().run(msg, ctx)

    assert result.intent == "result"
    assert isinstance(result.payload, dict)
    assert "experiment_results" in result.payload
    assert result.payload["experiment_results"][0]["score"] == 78


async def test_experiment_agent_includes_verdicts_in_prompt(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    captured_reqs = []

    async def mock_complete(req):
        captured_reqs.append(req)
        return _make_completion(_SAMPLE_RESULTS)

    provider.complete = mock_complete

    msg = make_task_msg(
        "experiment",
        {"target_gene": "BRCA1", "disease": "breast cancer", "lens_verdicts": _SAMPLE_VERDICTS},
        run_id,
        trace_id,
    )

    await ExperimentAgent().run(msg, ctx)

    user_msg = captured_reqs[0].messages[0]["content"]
    assert "genetics" in user_msg
    assert "biology" in user_msg


async def test_experiment_agent_empty_lens_verdicts(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_SAMPLE_RESULTS))

    msg = make_task_msg(
        "experiment",
        {"target_gene": "BRCA1", "disease": "breast cancer", "lens_verdicts": []},
        run_id,
        trace_id,
    )

    result = await ExperimentAgent().run(msg, ctx)

    assert result.intent == "result"
    assert "experiment_results" in result.payload


# ---------------------------------------------------------------------------
# Mendelian causality score floor
# ---------------------------------------------------------------------------


_MENDELIAN_FLOOR_SIGNALS = {
    "high_star_plp": 2,
    "plp_count": 3,
    "clingen_classification": "Definitive",
}


async def test_low_score_floored_when_mendelian_grade(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    low_score_results = json.dumps(
        [
            {
                "target": "TRPC6",
                "score": 30,
                "rationale": "Weak clinical signal.",
                "supporting_evidence_ids": [],
            },
        ]
    )
    provider.complete = AsyncMock(return_value=_make_completion(low_score_results))

    msg = make_task_msg(
        "experiment",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "lens_verdicts": _SAMPLE_VERDICTS,
            "genetics_floor_signals": _MENDELIAN_FLOOR_SIGNALS,
        },
        run_id,
        trace_id,
    )

    result = await ExperimentAgent().run(msg, ctx)

    score = result.payload["experiment_results"][0]["score"]
    assert score >= 70
    assert "Mendelian causality floor" in result.payload["experiment_results"][0]["rationale"]


async def test_high_score_not_lowered_when_mendelian_grade(run_id, trace_id, exp_ctx):
    """The floor must never lower a score the LLM already set above it."""
    ctx, provider = exp_ctx
    high_score_results = json.dumps(
        [
            {
                "target": "TRPC6",
                "score": 92,
                "rationale": "Strong everything.",
                "supporting_evidence_ids": [],
            },
        ]
    )
    provider.complete = AsyncMock(return_value=_make_completion(high_score_results))

    msg = make_task_msg(
        "experiment",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "lens_verdicts": _SAMPLE_VERDICTS,
            "genetics_floor_signals": _MENDELIAN_FLOOR_SIGNALS,
        },
        run_id,
        trace_id,
    )

    result = await ExperimentAgent().run(msg, ctx)

    assert result.payload["experiment_results"][0]["score"] == 92


async def test_score_not_floored_without_mendelian_grade(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    low_score_results = json.dumps(
        [
            {"target": "TRPC6", "score": 30, "rationale": "Weak.", "supporting_evidence_ids": []},
        ]
    )
    provider.complete = AsyncMock(return_value=_make_completion(low_score_results))

    msg = make_task_msg(
        "experiment",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "lens_verdicts": _SAMPLE_VERDICTS,
            "genetics_floor_signals": {"high_star_plp": 0, "plp_count": 0},
        },
        run_id,
        trace_id,
    )

    result = await ExperimentAgent().run(msg, ctx)

    assert result.payload["experiment_results"][0]["score"] == 30


async def test_mendelian_context_injected_into_experiment_prompt(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    captured_reqs = []

    async def mock_complete(req):
        captured_reqs.append(req)
        return _make_completion(_SAMPLE_RESULTS)

    provider.complete = mock_complete

    msg = make_task_msg(
        "experiment",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "lens_verdicts": _SAMPLE_VERDICTS,
            "genetics_floor_signals": _MENDELIAN_FLOOR_SIGNALS,
        },
        run_id,
        trace_id,
    )

    await ExperimentAgent().run(msg, ctx)

    user_msg = captured_reqs[0].messages[0]["content"]
    assert "Mendelian context" in user_msg
