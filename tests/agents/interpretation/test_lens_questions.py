# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The typed-question harness (agents/interpretation/_lens_questions.py).

The answer model is a small placeholder; the cases test validation, the single retry,
citation checking, claim limits and the cache digest, not any lens's questions.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ConfigDict

from agents.interpretation._lens_questions import (
    LensQuestion,
    QuestionResult,
    ask,
    filter_citations,
    question_digest,
)
from core.routing.providers.base import CompletionResult
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, Direction, EvidenceType, Provenance


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["yes", "no"]
    claim_ids: list[str] = []


class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[_Item]
    claim_ids: list[str] = []


_QUESTION = LensQuestion(id="test_question", skill="safety_narrative", answer_model=_Answer)


def _completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content, model_used="m", input_tokens=1, output_tokens=1, latency_ms=1.0
    )


def _claim(run_id: uuid.UUID, text: str = "A claim.", confidence: float = 0.9) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="GENE-A",
        disease="disease one",
        evidence_type=EvidenceType.REGULATORY,
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="t", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        claim_text=text,
        direction=Direction.UNSPECIFIED,
        confidence=confidence,
    )


@pytest.fixture()
def harness(run_id, trace_id):
    provider = MagicMock()
    provider.context_window = None
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    return RunContext(run_id=run_id, trace_id=trace_id, router=router), provider


async def _ask(ctx, claims, question=_QUESTION, facts="Facts: none."):
    return await ask(
        question,
        facts=facts,
        claims=claims,
        quality_map={},
        disease_classes=frozenset(),
        lens="safety",
        ctx=ctx,
        classification=DataClass.NON_SENSITIVE,
    )


async def test_a_valid_answer_is_validated(harness, run_id):
    ctx, provider = harness
    claim = _claim(run_id)
    cid = str(claim.evidence_id)
    reply = {"items": [{"status": "yes", "claim_ids": [cid]}], "claim_ids": [cid]}
    provider.complete = AsyncMock(return_value=_completion(json.dumps(reply)))

    result = await _ask(ctx, [claim])

    assert isinstance(result, QuestionResult)
    assert result.answer == reply
    assert result.attempts == 1 and result.shown_claim_ids == [cid]
    assert result.dropped_claim_ids == []


async def test_an_invalid_answer_is_retried_with_the_validation_error(harness, run_id):
    ctx, provider = harness
    seen: list[list[dict]] = []
    replies = iter(
        [
            json.dumps({"items": [{"status": "maybe"}]}),
            json.dumps({"items": [{"status": "no"}]}),
        ]
    )

    async def complete(req):
        seen.append(req.messages)
        return _completion(next(replies))

    provider.complete = complete

    result = await _ask(ctx, [_claim(run_id)])

    assert result.attempts == 2
    assert result.answer == {"items": [{"status": "no", "claim_ids": []}], "claim_ids": []}
    retry = seen[1]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert "did not match the schema" in retry[2]["content"]
    assert "status" in retry[2]["content"]  # the pydantic error names the field
    assert "Return the corrected JSON object only." in retry[2]["content"]


async def test_two_invalid_answers_leave_the_question_unanswered(harness, run_id):
    ctx, provider = harness
    provider.complete = AsyncMock(return_value=_completion("not json at all"))

    result = await _ask(ctx, [_claim(run_id)])

    assert result.answer is None and result.attempts == 2
    assert provider.complete.await_count == 2


async def test_a_reply_that_is_not_an_object_is_invalid(harness, run_id):
    ctx, provider = harness
    provider.complete = AsyncMock(return_value=_completion("[1, 2, 3]"))
    assert (await _ask(ctx, [_claim(run_id)])).answer is None


async def test_a_fenced_reply_is_accepted(harness, run_id):
    ctx, provider = harness
    provider.complete = AsyncMock(
        return_value=_completion('```json\n{"items": [{"status": "yes"}]}\n```')
    )
    assert (await _ask(ctx, [_claim(run_id)])).answer is not None


async def test_cited_ids_the_model_was_not_shown_are_dropped_and_recorded(harness, run_id):
    ctx, provider = harness
    claim = _claim(run_id)
    cid, invented = str(claim.evidence_id), str(uuid.uuid4())
    reply = {
        "items": [{"status": "yes", "claim_ids": [cid, invented]}],
        "claim_ids": [invented],
    }
    provider.complete = AsyncMock(return_value=_completion(json.dumps(reply)))

    result = await _ask(ctx, [claim])

    assert result.answer == {
        "items": [{"status": "yes", "claim_ids": [cid]}],
        "claim_ids": [],
    }
    assert result.dropped_claim_ids == [invented]


async def test_a_claim_beyond_the_limit_is_not_shown_and_cannot_be_cited(harness, run_id):
    ctx, provider = harness
    strong = _claim(run_id, "strong", confidence=0.95)
    weak = _claim(run_id, "weak", confidence=0.1)
    question = LensQuestion(id="q", skill="safety_narrative", answer_model=_Answer, max_claims=1)
    reply = {"items": [{"status": "yes", "claim_ids": [str(weak.evidence_id)]}]}
    provider.complete = AsyncMock(return_value=_completion(json.dumps(reply)))

    result = await _ask(ctx, [weak, strong], question)

    assert result.shown_claim_ids == [str(strong.evidence_id)]
    assert result.dropped_claim_ids == [str(weak.evidence_id)]


async def test_the_claim_limit_is_respected(harness, run_id):
    ctx, provider = harness
    claims = [_claim(run_id, f"claim {i}", confidence=0.5 + i / 100) for i in range(40)]
    sent: list[str] = []

    async def complete(req):
        sent.append(req.messages[0]["content"])
        return _completion('{"items": []}')

    provider.complete = complete

    result = await _ask(ctx, claims)  # default max_claims is 25

    assert len(result.shown_claim_ids) == 25
    assert sent[0].count('"claim_id"') == 25
    # the strongest first: the highest-confidence claim is among those shown
    assert str(claims[-1].evidence_id) in result.shown_claim_ids


async def test_the_limit_shrinks_to_fit_the_prompt_budget(harness, run_id, monkeypatch):
    ctx, provider = harness
    provider.context_window = lambda task: 3072 + 700  # a 700-token input budget
    claims = [_claim(run_id, "x" * 190) for _ in range(25)]
    provider.complete = AsyncMock(return_value=_completion('{"items": []}'))

    result = await _ask(ctx, claims)

    assert 10 <= len(result.shown_claim_ids) < 25


async def test_the_prompt_carries_the_facts_the_claims_and_the_schema(harness, run_id):
    ctx, provider = harness
    sent: list[CompletionResult] = []

    async def complete(req):
        sent.append(req)
        return _completion('{"items": []}')

    provider.complete = complete

    await _ask(ctx, [_claim(run_id, "the claim text")], facts="Fact: the requested direction.")

    req = sent[0]
    user = req.messages[0]["content"]
    assert user.startswith("Fact: the requested direction.\n\nClaims:\n")
    assert "the claim text" in user
    assert "Return one JSON object matching this schema:" in user
    assert json.dumps(_Answer.model_json_schema()) in user
    assert req.system == ctx.load_skill("safety_narrative")
    assert req.task == "safety_lens" and req.model_override == "mock-model"


async def test_no_claim_and_an_empty_answer_means_no_call(harness):
    ctx, provider = harness
    provider.complete = AsyncMock()
    question = LensQuestion(
        id="q", skill="safety_narrative", answer_model=_Answer, empty_answer={"items": []}
    )

    result = await _ask(ctx, [], question)

    assert result.answer == {"items": []} and result.attempts == 0
    provider.complete.assert_not_awaited()


async def test_no_claim_without_an_empty_answer_still_asks(harness):
    ctx, provider = harness
    provider.complete = AsyncMock(return_value=_completion('{"items": []}'))
    assert (await _ask(ctx, [])).answer == {"items": [], "claim_ids": []}


def test_filter_citations_walks_nested_lists():
    dropped: list[str] = []
    value = {"events": [{"claim_ids": ["a", "x"]}, {"claim_ids": ["y"]}], "claim_ids": ["b"]}
    out = filter_citations(value, {"a", "b"}, dropped)
    assert out == {"events": [{"claim_ids": ["a"]}, {"claim_ids": []}], "claim_ids": ["b"]}
    assert dropped == ["x", "y"]


def test_the_digest_changes_when_a_question_skill_changes(monkeypatch):
    base = question_digest([_QUESTION])
    assert question_digest([_QUESTION]) == base

    import agents.interpretation._lens_questions as module

    monkeypatch.setattr(module, "load_skill", lambda name: "edited instructions")
    assert question_digest([_QUESTION]) != base


def test_the_digest_changes_when_the_answer_schema_changes():
    class _Other(BaseModel):
        model_config = ConfigDict(extra="forbid")
        value: int

    other = LensQuestion(id="test_question", skill="safety_narrative", answer_model=_Other)
    assert question_digest([other]) != question_digest([_QUESTION])


def test_the_digest_covers_the_extra_skills_and_the_claim_limit():
    base = question_digest([_QUESTION])
    assert question_digest([_QUESTION], extra_skills=["clinical_lens"]) != base
    fewer = LensQuestion(
        id="test_question", skill="safety_narrative", answer_model=_Answer, max_claims=5
    )
    assert question_digest([fewer]) != base


async def test_a_per_claim_question_bounds_its_reply_by_the_claims_shown(harness, run_id):
    """A greedy decode that starts repeating ran to the provider's 4096-token default
    (twelve minutes on a local model) before the reply could be parsed."""
    ctx, provider = harness
    sent = []

    async def complete(req):
        sent.append(req.max_tokens)
        return _completion('{"items": []}')

    provider.complete = complete
    bounded = LensQuestion(
        id="q", skill="safety_narrative", answer_model=_Answer, max_claims=3, tokens_per_claim=100
    )

    await _ask(ctx, [_claim(run_id, f"claim {i}") for i in range(5)], question=bounded)
    await _ask(ctx, [_claim(run_id)])  # no per-claim bound: the provider default

    assert sent == [256 + 3 * 100, 4096]


async def test_a_reply_never_gets_more_than_the_window_has_left(harness, run_id):
    ctx, provider = harness
    sent = []

    async def complete(req):
        sent.append((req.max_tokens, len(req.system or "") + len(req.messages[0]["content"])))
        return _completion('{"items": []}')

    provider.complete = complete
    provider.context_window = lambda task: 3_000  # room for the prompt, not for 4096 more
    await _ask(ctx, [_claim(run_id)])  # unscaled question: wants 4096
    [(max_tokens, prompt_chars)] = sent
    assert 256 < max_tokens < 4096
    assert prompt_chars / 3.0 + max_tokens <= 3_000
