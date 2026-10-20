# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lens prompt budget (agents/interpretation/_lens_base.py).

Regression lock: a commercial-lens prompt of ~33K tokens hit Ollama's num_ctx of 32768,
which silently drops the oldest tokens — the skill prompt — and the lens came back as
"insufficient evidence" with an empty rationale.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from agents.interpretation._lens_base import (
    _MIN_PROMPT_CLAIMS,
    _OUTPUT_RESERVE_TOKENS,
    fit_claim_limit,
    prompt_budget,
    run_lens,
)
from core.routing.providers.base import CompletionResult
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, Direction, EvidenceType, Provenance
from tests.agents.conftest import make_task_msg


class _Provider:
    def __init__(self, window: int) -> None:
        self.window = window
        self.prompts: list[str] = []

    def context_window(self, task: str) -> int:
        return self.window

    async def complete(self, req) -> CompletionResult:
        self.prompts.append(req.messages[0]["content"])
        return CompletionResult(
            content=json.dumps(
                {"overall_verdict": "neutral", "confidence": 0.4, "rationale": "r", "axes": []}
            ),
            model_used="m",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )


def test_budget_is_the_context_window_less_the_output_reserve(monkeypatch):
    monkeypatch.delenv("LENS_PROMPT_BUDGET", raising=False)
    assert prompt_budget(_Provider(32768), "commercial_lens") == 32768 - _OUTPUT_RESERVE_TOKENS


def test_env_budget_tightens_but_never_loosens(monkeypatch):
    monkeypatch.setenv("LENS_PROMPT_BUDGET", "8000")
    assert prompt_budget(_Provider(32768), "x") == 8000
    monkeypatch.setenv("LENS_PROMPT_BUDGET", "99999")
    assert prompt_budget(_Provider(32768), "x") == 32768 - _OUTPUT_RESERVE_TOKENS


@pytest.mark.parametrize("provider", [MagicMock(), object()])
def test_providers_without_a_known_window_are_unbounded(provider, monkeypatch):
    monkeypatch.delenv("LENS_PROMPT_BUDGET", raising=False)
    assert prompt_budget(provider, "x") is None


def _render(n: int) -> str:
    return "x" * (300 * n)  # 100 estimated tokens per claim at the default 3.0 ratio


def test_claims_are_not_cut_when_they_fit():
    assert fit_claim_limit(1000, 50_000, _render, 100) == 100


def test_the_largest_fitting_claim_count_is_chosen():
    assert fit_claim_limit(1000, 3000, _render, 100) == 20


def test_claims_are_never_cut_below_the_minimum():
    assert fit_claim_limit(10_000, 3000, _render, 100) == _MIN_PROMPT_CLAIMS


def _claim(run_id, i: int) -> dict:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="G",
        disease="d",
        evidence_type=EvidenceType.PATENT,
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="t", timestamp=datetime(2025, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        claim_text=f"Patent claim number {i} " + "y" * 150,
        direction=Direction.INHIBIT,
        confidence=0.5 + i / 1000,
    ).model_dump(mode="json")


async def test_run_lens_shrinks_the_claim_list_to_fit_the_window(run_id, trace_id, monkeypatch):
    monkeypatch.delenv("LENS_PROMPT_BUDGET", raising=False)
    provider = _Provider(window=_OUTPUT_RESERVE_TOKENS + 9000)
    router = MagicMock()
    router.select.return_value = (provider, "m")
    ctx = RunContext(run_id=run_id, trace_id=trace_id, router=router)
    msg = make_task_msg(
        "commercial_lens",
        {
            "target_gene": "G",
            "disease": "d",
            "extracted_claims": [_claim(run_id, i) for i in range(100)],
        },
        run_id,
        trace_id,
    )
    await run_lens(
        msg,
        ctx,
        lens="commercial",
        evidence_types=(EvidenceType.PATENT,),
        skill_name="commercial_lens",
    )
    prompt = provider.prompts[0]
    shown = len(re.findall(r'"claim_id"', prompt))
    assert _MIN_PROMPT_CLAIMS <= shown < 100
    assert "Relevant claims (100)" in prompt  # the count of what exists is kept
