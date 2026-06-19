# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for GapDetectionAgent — bounded replanning advisory."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.synthesis.gap_detection.agent import GapDetectionAgent, _parse_gap_decision
from core.routing.providers.base import CompletionResult
from tests.agents.conftest import make_task_msg


def _make_completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content,
        model_used="test-model",
        input_tokens=20,
        output_tokens=40,
        latency_ms=80.0,
    )


_REVIEW_GAPS_THIN = [
    {
        "stage": "genetics",
        "missing_aspects": ["No GWAS data.", "No constraint scores."],
        "completeness_score": 25,
    },
    {
        "stage": "clinical",
        "missing_aspects": ["No clinical trial data found."],
        "completeness_score": 10,
    },
    {"stage": "literature", "missing_aspects": [], "completeness_score": 70},
    {"stage": "screening", "missing_aspects": [], "completeness_score": 80},
    {"stage": "extraction", "missing_aspects": [], "completeness_score": 75},
    {"stage": "lenses", "missing_aspects": [], "completeness_score": 60},
    {"stage": "experiment", "missing_aspects": [], "completeness_score": 60},
]

_REVIEW_GAPS_OK = [
    {"stage": stage, "missing_aspects": [], "completeness_score": 80}
    for stage in (
        "genetics",
        "clinical",
        "literature",
        "screening",
        "extraction",
        "lenses",
        "experiment",
    )
]

_AGREEMENT_MAP = {
    "consensus_verdict": "support",
    "conflicts": [],
}


@pytest.fixture()
def gap_ctx(run_id, trace_id):
    provider = MagicMock()
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    from harness.context import RunContext

    return RunContext(run_id=run_id, trace_id=trace_id, router=router), provider


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_parse_gap_decision_proceed():
    raw = json.dumps({"replan_decision": "proceed", "guidance": "All good."})
    result = _parse_gap_decision(raw)
    assert result["replan_decision"] == "proceed"
    assert result["guidance"] == "All good."


def test_parse_gap_decision_replan():
    raw = json.dumps({"replan_decision": "replan", "guidance": "Genetics lens underpowered."})
    result = _parse_gap_decision(raw)
    assert result["replan_decision"] == "replan"


def test_parse_gap_decision_fallback_on_bad_json():
    result = _parse_gap_decision("not json")
    assert result["replan_decision"] == "proceed"
    assert "Could not parse" in result["guidance"]


def test_parse_gap_decision_fallback_on_invalid_verdict():
    raw = json.dumps({"replan_decision": "maybe", "guidance": "hmm"})
    result = _parse_gap_decision(raw)
    assert result["replan_decision"] == "proceed"


# ---------------------------------------------------------------------------
# Agent behaviour
# ---------------------------------------------------------------------------


async def test_gap_detection_returns_proceed(run_id, trace_id, gap_ctx):
    ctx, provider = gap_ctx
    provider.complete = AsyncMock(
        return_value=_make_completion(
            json.dumps({"replan_decision": "proceed", "guidance": "Evidence sufficient."})
        )
    )

    msg = make_task_msg(
        "gap_detection",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "review_gaps": _REVIEW_GAPS_OK,
            "agreement_map": _AGREEMENT_MAP,
            "replan_count": 0,
        },
        run_id,
        trace_id,
    )

    result = await GapDetectionAgent().run(msg, ctx)

    assert result.intent == "result"
    assert result.payload["replan_decision"] == "proceed"
    assert isinstance(result.payload["gap_guidance"], str)


async def test_gap_detection_returns_replan(run_id, trace_id, gap_ctx):
    ctx, provider = gap_ctx
    provider.complete = AsyncMock(
        return_value=_make_completion(
            json.dumps(
                {"replan_decision": "replan", "guidance": "Genetics lens missing GWAS data."}
            )
        )
    )

    msg = make_task_msg(
        "gap_detection",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "review_gaps": _REVIEW_GAPS_THIN,
            "agreement_map": _AGREEMENT_MAP,
            "replan_count": 0,
        },
        run_id,
        trace_id,
    )

    result = await GapDetectionAgent().run(msg, ctx)

    assert result.payload["replan_decision"] == "replan"
    assert "GWAS" in result.payload["gap_guidance"]


async def test_gap_detection_includes_replan_count_in_prompt(run_id, trace_id, gap_ctx):
    ctx, provider = gap_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(json.dumps({"replan_decision": "proceed", "guidance": "ok"}))

    provider.complete = mock_complete

    msg = make_task_msg(
        "gap_detection",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "review_gaps": [],
            "agreement_map": _AGREEMENT_MAP,
            "replan_count": 1,
        },
        run_id,
        trace_id,
    )

    await GapDetectionAgent().run(msg, ctx)

    # Prompt should mention max replans reached when replan_count >= 1
    assert (
        "Max replans" in captured[0]
        or "max replans" in captured[0].lower()
        or "pass #2" in captured[0]
    )


async def test_gap_detection_fallback_on_bad_llm_response(run_id, trace_id, gap_ctx):
    ctx, provider = gap_ctx
    provider.complete = AsyncMock(return_value=_make_completion("certainly!"))

    msg = make_task_msg(
        "gap_detection",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "review_gaps": [],
            "agreement_map": _AGREEMENT_MAP,
            "replan_count": 0,
        },
        run_id,
        trace_id,
    )

    result = await GapDetectionAgent().run(msg, ctx)

    # Fallback: proceed (safe default)
    assert result.payload["replan_decision"] == "proceed"
