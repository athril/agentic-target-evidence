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
    FIXED_SHARE,
    estimate_tokens,
    fit_claim_limit,
    fit_fixed_blocks,
    prompt_budget,
    run_lens,
)
from core.routing.providers.base import CompletionResult
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, Direction, EvidenceType, Provenance
from tests.agents.conftest import make_task_msg


class _Provider:
    def __init__(self, window: int, fill_first: bool = False) -> None:
        self.window = window
        self.prompts: list[str] = []
        self.max_tokens: list[int | None] = []
        self.fill_first = fill_first

    def context_window(self, task: str) -> int:
        return self.window

    async def complete(self, req) -> CompletionResult:
        self.prompts.append(req.messages[0]["content"])
        self.max_tokens.append(req.max_tokens)
        filled = self.fill_first and len(self.prompts) == 1
        return CompletionResult(
            content=json.dumps(
                {"overall_verdict": "neutral", "confidence": 0.4, "rationale": "r", "axes": []}
            ),
            model_used="m",
            input_tokens=self.window - 10 if filled else 1,
            output_tokens=10 if filled else 1,
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


# ---- fixed blocks ------------------------------------------------------------------

_DRUG_ROWS = "\n".join(
    f"  • drug {i} [inhibitor; same direction] — phase {4 - i % 4} in disease one"
    for i in range(40)
)
_LONG_TEXT = "Label section. " + "Warnings and precautions apply to everyone. " * 40
_LEDGER = "Evidence-strength ledger:\n" + "\n".join(
    f"- kind {i}: 3 claim(s), weight {1 - i / 30:.2f}" for i in range(30)
)


def test_fixed_blocks_within_their_share_are_left_alone():
    blocks = ["short direction text", "short context", "- kind: 1 claim(s), weight 0.9"]
    assert fit_fixed_blocks(blocks, 10_000) == (blocks, [])


def test_long_free_text_is_cut_first_and_says_so():
    blocks = ["", _LONG_TEXT, ""]
    out, notes = fit_fixed_blocks(blocks, estimate_tokens(_LONG_TEXT) // 2)
    assert notes == ["long free text cut to its first sentence"]
    assert out[1].startswith("Label section. Warnings and precautions apply to everyone.")
    assert out[1].endswith("[…]")


def test_lists_keep_their_first_rows_and_count_what_they_left_out():
    blocks = ["", f"Precedent:\n{_DRUG_ROWS}", _LEDGER]
    cap = estimate_tokens("".join(blocks)) // 3
    out, notes = fit_fixed_blocks(blocks, cap)
    assert notes[0] == "long free text cut to its first sentence"
    assert notes[1].startswith("lists cut to their first")
    assert "drug 0 " in out[1] and "drug 39 " not in out[1]
    assert re.search(r"… \d+ more rows not shown", out[1])
    # The ledger keeps its strongest kinds, the weakest go.
    assert "kind 0:" in out[2] and "kind 29:" not in out[2]
    assert estimate_tokens("".join(out)) <= cap


def test_a_row_keeps_its_indented_detail_lines():
    block = "\n".join(
        f"  {i}. drug {i} — best stage phase 3\n     • NCT{i:08d}: completed" for i in range(1, 11)
    )
    out, _ = fit_fixed_blocks([block], estimate_tokens(block) // 2)
    assert "1. drug 1 —" in out[0] and "NCT00000001" in out[0]
    assert "10. drug 10 —" not in out[0] and "NCT00000010" not in out[0]


@pytest.mark.parametrize("cap", [50, 200, 800, 2000])
def test_trimmed_blocks_always_fit_their_cap(cap):
    out, _ = fit_fixed_blocks([_LONG_TEXT, f"Precedent:\n{_DRUG_ROWS}", _LEDGER], cap)
    assert estimate_tokens("".join(out)) <= cap * 1.05  # the cut point is estimated


async def test_claims_never_sit_at_the_minimum_while_fixed_blocks_exceed_their_share(
    run_id, trace_id, monkeypatch
):
    """TNF commercial: a long drug list pushed the claims to the 10-claim minimum."""
    monkeypatch.delenv("LENS_PROMPT_BUDGET", raising=False)
    provider = _Provider(window=_OUTPUT_RESERVE_TOKENS + 12_000)
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
    huge_context = "\n".join([_LONG_TEXT] * 20 + [_DRUG_ROWS] * 20)
    await run_lens(
        msg,
        ctx,
        lens="commercial",
        evidence_types=(EvidenceType.PATENT,),
        skill_name="commercial_lens",
        extra_context=huge_context,
    )
    prompt = provider.prompts[0]
    shown = len(re.findall(r'"claim_id"', prompt))
    assert shown > _MIN_PROMPT_CLAIMS
    head = prompt.split("Relevant claims", 1)[0]
    assert estimate_tokens(head) <= FIXED_SHARE * 12_000 * 1.05
    assert "more rows not shown" in head


async def test_the_reply_is_capped_and_a_filled_window_is_retried_once_with_fewer_claims(
    run_id, trace_id, monkeypatch
):
    monkeypatch.delenv("LENS_PROMPT_BUDGET", raising=False)
    provider = _Provider(window=_OUTPUT_RESERVE_TOKENS + 20_000, fill_first=True)
    router = MagicMock()
    router.select.return_value = (provider, "m")
    ctx = RunContext(run_id=run_id, trace_id=trace_id, router=router)
    msg = make_task_msg(
        "commercial_lens",
        {
            "target_gene": "G",
            "disease": "d",
            "extracted_claims": [_claim(run_id, i) for i in range(60)],
        },
        run_id,
        trace_id,
    )
    result = await run_lens(
        msg,
        ctx,
        lens="commercial",
        evidence_types=(EvidenceType.PATENT,),
        skill_name="commercial_lens",
    )
    assert len(provider.prompts) == 2  # one retry, no more
    first, second = (len(re.findall(r'"claim_id"', p)) for p in provider.prompts)
    assert second == int(first * 0.8)
    assert all(t is not None and t <= _OUTPUT_RESERVE_TOKENS for t in provider.max_tokens)
    flags = result.payload["lens_verdicts"][0]["validation_flags"]
    assert any(f["rule_id"] == "prompt.window_filled" for f in flags)
