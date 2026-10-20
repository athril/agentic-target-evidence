# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Narrow, typed questions a lens asks instead of one large verdict call.

A lens call sends 10–22K characters of skill (about twenty rules), pre-computed blocks and
up to a hundred claims to one request, and the model must return the verdict, every axis,
confidences, citations and a narrative at once. A 7B model follows a few of those rules;
the rest were patched one by one by guards and post-LLM rules. A *question* asks the model
one thing — has this liability been settled by human data, is there harm in people, is the
target expressed in the disease cell type — with the facts and claims that bear on it, and
takes a typed answer. Code validates the answer, drops citations the model was not shown,
and computes the verdict from the answers (see ``safety_lens/aggregate.py``), so whole
classes of error cannot occur instead of being caught after the fact.

``ask`` is lens-agnostic: it ranks and cuts the candidate claims with the lens's own
ranking, fits the prompt to the context window, parses the reply against the answer model
and retries once with the validation error. A question that stays unanswered returns
``answer=None``; the caller decides how to degrade (the safety lens falls back to its
single-call path rather than lose the lens).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict

from agents.interpretation._lens_base import (
    LensName,
    _claims_to_json,
    _shown_claims,
    estimate_tokens,
    fit_claim_limit,
    prompt_budget,
    reply_room,
)
from core.json_utils import loads_recovering, strip_json_fence
from core.routing.providers.base import CompletionRequest
from harness.context import RunContext
from harness.skills import load_skill
from schemas.evidence import CoreClaim, DataClass

logger = logging.getLogger(__name__)

_CITATION_FIELD = "claim_ids"
_RETRY_ERROR_CHARS = 500


_REPLY_BASE_TOKENS = 256  # the reply's envelope, before the per-claim entries
# A question whose reply does not scale with its claims (one entry per liability, say):
# the provider's usual reply cap, still bounded by the room left in the window.
_UNSCALED_REPLY_TOKENS = 4096


class LensQuestion(BaseModel):
    """One question: its instructions, the shape of the answer, and how many claims it sees."""

    model_config = ConfigDict(frozen=True)

    id: str  # logs, flags and the cache digest, e.g. "liability_resolution"
    skill: str  # skills/<skill>.md holding the question's instructions
    answer_model: type[BaseModel]  # pydantic model of the answer (extra="forbid")
    max_claims: int = 25
    # The answer when no claim bears on the question, so no call is made. Only for
    # questions that cannot be answered without a cited claim.
    empty_answer: dict[str, Any] | None = None
    # For a question answered once per claim: the reply's length bound per claim shown.
    # A greedy decode that starts repeating otherwise runs to the provider's default
    # limit (minutes on a local model) before the reply can be parsed.
    tokens_per_claim: int = 0


class QuestionResult(BaseModel):
    question_id: str
    answer: dict[str, Any] | None  # validated answer (model_dump); None if unanswered
    shown_claim_ids: list[str] = []
    dropped_claim_ids: list[str] = []  # ids the model cited that it was not shown
    attempts: int = 0
    # What code corrected after validation (e.g. a status without a valid citation).
    notes: list[str] = []


def question_digest(questions: Iterable[LensQuestion], extra_skills: Iterable[str] = ()) -> str:
    """SHA-256 over each question's id, instructions, answer schema and claim limit.

    Part of the lens cache key when a lens runs split, so editing a question's skill or
    answer model never serves a verdict produced by the old one. ``extra_skills`` are
    other prompts the split path uses (the narrative).
    """
    h = hashlib.sha256()
    for q in questions:
        h.update(
            json.dumps(
                {
                    "id": q.id,
                    "skill": hashlib.sha256(load_skill(q.skill).encode()).hexdigest(),
                    "schema": q.answer_model.model_json_schema(),
                    "max_claims": q.max_claims,
                },
                sort_keys=True,
            ).encode()
        )
    for name in extra_skills:
        h.update(hashlib.sha256(load_skill(name).encode()).hexdigest().encode())
    return h.hexdigest()


def _parse(raw: str, model: type[BaseModel]) -> tuple[BaseModel | None, str]:
    """The validated answer, or None and the error to show the model."""
    try:
        data = loads_recovering(strip_json_fence(raw))
        if not isinstance(data, dict):
            raise ValueError("the reply is not a single JSON object")
        return model.model_validate(data), ""
    except (ValueError, TypeError) as exc:  # JSONDecodeError and ValidationError are ValueErrors
        return None, str(exc)[:_RETRY_ERROR_CHARS]


def filter_citations(value: Any, shown: set[str], dropped: list[str]) -> Any:
    """``value`` with every ``claim_ids`` list cut to the ids the model was shown.

    Removed ids are appended to ``dropped``. Walks nested dicts and lists, so a list of
    events each carrying its own ``claim_ids`` is covered.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == _CITATION_FIELD and isinstance(item, list):
                kept = [i for i in item if str(i) in shown]
                dropped.extend(str(i) for i in item if str(i) not in shown)
                out[key] = [str(i) for i in kept]
            else:
                out[key] = filter_citations(item, shown, dropped)
        return out
    if isinstance(value, list):
        return [filter_citations(item, shown, dropped) for item in value]
    return value


async def ask(
    question: LensQuestion,
    *,
    facts: str,
    claims: list[CoreClaim],
    quality_map: dict[str, Any],
    disease_classes: frozenset[str],
    lens: LensName,
    ctx: RunContext,
    classification: DataClass,
    papers: Any = None,
) -> QuestionResult:
    """Put one question to the lens model and return its validated, citation-checked answer.

    ``facts`` is the deterministic text this question needs and nothing else; ``claims``
    are the candidates, ranked with the lens's own ranking (literature by paper when the
    run's literature index ``papers`` is given) and cut to the question's limit
    (lower when the prompt would overflow the context window).
    """
    if not claims and question.empty_answer is not None:
        return QuestionResult(
            question_id=question.id, answer=dict(question.empty_answer), attempts=0
        )

    skill_text = ctx.load_skill(question.skill)
    provider, model = ctx.select_model(classification, f"{lens}_lens")
    tail = "\n\nReturn one JSON object matching this schema:\n" + json.dumps(
        question.answer_model.model_json_schema()
    )
    head = f"{facts}\n\nClaims:\n" if facts else "Claims:\n"

    def render(n: int) -> str:
        return (
            _claims_to_json(claims, quality_map, disease_classes, lens, n, papers)
            if claims
            else "[]"
        )

    available = min(question.max_claims, len(claims))
    limit = min(
        question.max_claims,
        fit_claim_limit(
            estimate_tokens(skill_text + head + tail, f"{lens}_lens"),
            prompt_budget(provider, f"{lens}_lens"),
            render,
            available,
            f"{lens}_lens",
        ),
    )
    shown = _shown_claims(claims, quality_map, disease_classes, lens, limit, papers)
    shown_ids = [str(c.evidence_id) for c in shown]
    user_content = head + render(limit) + tail

    # The reply scales with the claims asked about; whatever the question, it never gets
    # more than the context window has left after the prompt.
    wanted = (
        _REPLY_BASE_TOKENS + question.tokens_per_claim * max(limit, 1)
        if question.tokens_per_claim
        else _UNSCALED_REPLY_TOKENS
    )
    prompt_tokens = estimate_tokens(skill_text + user_content, f"{lens}_lens")
    bound = {"max_tokens": reply_room(provider, f"{lens}_lens", prompt_tokens, wanted)}

    def request(messages: list[dict[str, Any]]) -> CompletionRequest:
        return CompletionRequest(
            messages=messages,
            system=skill_text,
            classification=classification,
            task=f"{lens}_lens",
            model_override=model,
            **bound,
        )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    answer: BaseModel | None = None
    attempts = 0
    for _ in range(2):
        attempts += 1
        completion = await provider.complete(request(messages))
        answer, error = _parse(completion.content, question.answer_model)
        if answer is not None:
            break
        logger.warning(
            "%s_lens question %s: reply did not match the schema (attempt %d): %s",
            lens,
            question.id,
            attempts,
            error[:200],
        )
        messages = [
            *messages,
            {"role": "assistant", "content": completion.content},
            {
                "role": "user",
                "content": (
                    f"Your reply did not match the schema: {error} "
                    "Return the corrected JSON object only."
                ),
            },
        ]

    if answer is None:
        return QuestionResult(
            question_id=question.id, answer=None, shown_claim_ids=shown_ids, attempts=attempts
        )
    dropped: list[str] = []
    checked = filter_citations(answer.model_dump(), set(shown_ids), dropped)
    logger.info(
        "%s_lens question %s: answered in %d attempt(s); %d claim(s) shown, %d citation(s) dropped",
        lens,
        question.id,
        attempts,
        len(shown_ids),
        len(dropped),
    )
    return QuestionResult(
        question_id=question.id,
        answer=checked,
        shown_claim_ids=shown_ids,
        dropped_claim_ids=list(dict.fromkeys(dropped)),
        attempts=attempts,
    )
