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
    assert result[0]["rationale"] == "Strong causality and tractability."


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
    first = result.payload["experiment_results"][0]
    # Computed, not the LLM's 78: 50 + 50 × (0.25·0.9 + 0.15·0.85) = 67.6 → 68.
    assert first["score"] == 68
    assert first["band"] == "PROCEED WITH CAUTION"
    assert first["rationale"] == "Strong causality and tractability."
    assert first["score_breakdown"]["evidence_score"] == 67.6


async def test_score_is_deterministic_whatever_the_llm_says(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    scores = []
    for llm_score in (5, 99):
        provider.complete = AsyncMock(
            return_value=_make_completion(
                json.dumps([{"target": "BRCA1", "score": llm_score, "rationale": "x"}])
            )
        )
        msg = make_task_msg(
            "experiment",
            {"target_gene": "BRCA1", "disease": "breast cancer", "lens_verdicts": _SAMPLE_VERDICTS},
            run_id,
            trace_id,
        )
        result = await ExperimentAgent().run(msg, ctx)
        scores.append(result.payload["experiment_results"][0]["score"])
    assert scores[0] == scores[1]


async def test_fallback_rationale_when_llm_output_unusable(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    provider.complete = AsyncMock(return_value=_make_completion("not json"))
    msg = make_task_msg(
        "experiment",
        {"target_gene": "BRCA1", "disease": "breast cancer", "lens_verdicts": _SAMPLE_VERDICTS},
        run_id,
        trace_id,
    )
    result = await ExperimentAgent().run(msg, ctx)
    first = result.payload["experiment_results"][0]
    assert first["score"] == 68
    assert "Raised by genetics, biology" in first["rationale"]


async def test_liabilities_lower_the_score_and_reach_the_prompt(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req)
        return _make_completion(_SAMPLE_RESULTS)

    provider.complete = mock_complete
    liabilities = [
        {"system": "lung", "severity": "medium", "source": "mouse knockout", "evidence": ["x"]},
        {"system": "dosage sensitivity", "severity": "high", "source": "gnomAD", "evidence": ["y"]},
    ]
    msg = make_task_msg(
        "experiment",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "lens_verdicts": _SAMPLE_VERDICTS,
            "candidate_liabilities": liabilities,
        },
        run_id,
        trace_id,
    )
    result = await ExperimentAgent().run(msg, ctx)
    # 67.6 − (1.5 + 4.0) = 62.1
    assert result.payload["experiment_results"][0]["score"] == 62
    user_msg = captured[0].messages[0]["content"]
    assert "dosage sensitivity" in user_msg
    assert "Computed suitability score: 62/100" in user_msg


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


def _msg(run_id, trace_id, verdicts, signals, **extra):
    return make_task_msg(
        "experiment",
        {
            "target_gene": "GENE-A",
            "disease": "DISEASE-A",
            "lens_verdicts": verdicts,
            "genetics_floor_signals": signals,
            **extra,
        },
        run_id,
        trace_id,
    )


async def test_score_floored_when_mendelian_grade(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_SAMPLE_RESULTS))
    result = await ExperimentAgent().run(
        _msg(run_id, trace_id, _SAMPLE_VERDICTS, _MENDELIAN_FLOOR_SIGNALS), ctx
    )
    first = result.payload["experiment_results"][0]
    assert first["score"] == 70
    assert first["score_breakdown"]["mendelian_floor_applied"] is True


async def test_score_not_floored_without_mendelian_grade(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_SAMPLE_RESULTS))
    result = await ExperimentAgent().run(
        _msg(run_id, trace_id, _SAMPLE_VERDICTS, {"high_star_plp": 0, "plp_count": 0}), ctx
    )
    assert result.payload["experiment_results"][0]["score"] == 68


async def test_floor_not_applied_when_safety_opposes(run_id, trace_id, exp_ctx):
    ctx, provider = exp_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_SAMPLE_RESULTS))
    verdicts = [
        *_SAMPLE_VERDICTS,
        {"lens": "safety", "overall_verdict": "oppose", "confidence": 0.8, "axes": []},
    ]
    result = await ExperimentAgent().run(
        _msg(run_id, trace_id, verdicts, _MENDELIAN_FLOOR_SIGNALS), ctx
    )
    first = result.payload["experiment_results"][0]
    assert first["score"] < 70
    assert first["score_breakdown"]["mendelian_floor_applied"] is False


@pytest.mark.parametrize(
    "raw",
    [
        '{"target": "GENE-A", "rationale": "Why."}',
        'Here is the result:\n[{"target": "GENE-A", "rationale": "Why."}]\nDone.',
        '{"experiment_results": [{"target": "GENE-A", "rationale": "Why."}]}',
    ],
)
def test_parse_results_accepts_common_small_model_shapes(raw):
    assert _parse_results(raw)[0]["rationale"] == "Why."


def test_parse_results_recovers_an_unclosed_array():
    """Small models often stop before the final "]" (every string complete)."""
    raw = '[{"target": "GENE-A", "rationale": "Why.", "supporting_evidence_ids": ["a", "b"]}'
    assert _parse_results(raw)[0]["rationale"] == "Why."


async def test_rationale_source_marks_the_fallback(run_id, trace_id, exp_ctx, caplog):
    ctx, provider = exp_ctx
    msg = make_task_msg(
        "experiment",
        {"target_gene": "BRCA1", "disease": "breast cancer", "lens_verdicts": _SAMPLE_VERDICTS},
        run_id,
        trace_id,
    )
    provider.complete = AsyncMock(return_value=_make_completion("not json"))
    fallback = (await ExperimentAgent().run(msg, ctx)).payload["experiment_results"][0]
    assert fallback["rationale_source"] == "deterministic"
    assert "no usable rationale" in caplog.text

    provider.complete = AsyncMock(return_value=_make_completion(_SAMPLE_RESULTS))
    model = (await ExperimentAgent().run(msg, ctx)).payload["experiment_results"][0]
    assert model["rationale_source"] == "model"
