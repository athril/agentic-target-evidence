# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ReviewerAgent."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.challenge.reviewer.agent import ReviewerAgent, _parse_gaps
from core.routing.providers.base import CompletionResult
from tests.agents.conftest import make_task_msg


def _make_completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content,
        model_used="test-model",
        input_tokens=25,
        output_tokens=90,
        latency_ms=180.0,
    )


_SAMPLE_GAPS_JSON = json.dumps(
    [
        {
            "stage": "literature",
            "missing_aspects": ["No RCT data found."],
            "completeness_score": 60,
        },
        {"stage": "screening", "missing_aspects": [], "completeness_score": 90},
        {
            "stage": "hypothesis",
            "missing_aspects": ["Missing tissue specificity data."],
            "completeness_score": 70,
        },
        {"stage": "experiment", "missing_aspects": [], "completeness_score": 85},
    ]
)


@pytest.fixture()
def reviewer_ctx(run_id, trace_id):
    provider = MagicMock()
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    from harness.context import RunContext

    return RunContext(run_id=run_id, trace_id=trace_id, router=router), provider


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_parse_gaps_returns_list():
    result = _parse_gaps(_SAMPLE_GAPS_JSON)
    assert len(result) == 4
    assert result[0]["stage"] == "literature"
    assert result[0]["completeness_score"] == 60


def test_parse_gaps_fallback_on_bad_json():
    result = _parse_gaps("bad json")
    # pipeline stages: literature, genetics, clinical, screening, extraction, lenses, experiment
    assert len(result) == 7  # one per stage
    assert all("unparseable" in r["missing_aspects"][0] for r in result)


# ---------------------------------------------------------------------------
# Agent behaviour
# ---------------------------------------------------------------------------


async def test_reviewer_agent_returns_gap_report(run_id, trace_id, reviewer_ctx):
    ctx, provider = reviewer_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_SAMPLE_GAPS_JSON))

    stage_counts = {
        "literature": 50,
        "patent": 10,
        "clinical_trial": 5,
        "opentargets": 1,
        "genetics": 3,
        "omics": 2,
        "screened": 30,
        "hypotheses": 5,
        "experiment_results": 1,
    }
    msg = make_task_msg(
        "reviewer",
        {"target_gene": "BRCA1", "disease": "breast cancer", "stage_counts": stage_counts},
        run_id,
        trace_id,
    )

    result = await ReviewerAgent().run(msg, ctx)

    assert result.intent == "result"
    assert isinstance(result.payload, dict)
    assert "review_gaps" in result.payload
    assert len(result.payload["review_gaps"]) == 4


async def test_reviewer_agent_includes_stage_counts_in_prompt(run_id, trace_id, reviewer_ctx):
    ctx, provider = reviewer_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req)
        return _make_completion(_SAMPLE_GAPS_JSON)

    provider.complete = mock_complete

    msg = make_task_msg(
        "reviewer",
        {"target_gene": "BRCA1", "disease": "breast cancer", "stage_counts": {"literature": 42}},
        run_id,
        trace_id,
    )

    await ReviewerAgent().run(msg, ctx)

    user_content = captured[0].messages[0]["content"]
    assert "42" in user_content


async def test_reviewer_uses_non_sensitive_routing(run_id, trace_id, reviewer_ctx):
    ctx, provider = reviewer_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_SAMPLE_GAPS_JSON))

    from schemas.evidence import DataClass

    msg = make_task_msg(
        "reviewer",
        {"target_gene": "BRCA1", "disease": "breast cancer", "stage_counts": {}},
        run_id,
        trace_id,
    )

    await ReviewerAgent().run(msg, ctx)

    ctx.router.select.assert_called_with(DataClass.NON_SENSITIVE, "reviewer")
