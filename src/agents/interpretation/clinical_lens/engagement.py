# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Did a drug that failed for efficacy actually engage its target?

A programme stopped for futility falsifies the target's role in the disease only if the
drug changed the target's activity in patients; a drug that never moved its
pharmacodynamic marker failed for a pharmacological reason. The registry does not say, so
this asks one narrow question per such programme through the lens question harness
(``_lens_questions``): shown, not shown, or not reported, with the claims behind it.

The answer scales the programme's contribution to the falsification strength
(``services/evidence/programme_outcomes``). An efficacy endpoint is not engagement, and a
status without a cited claim is ``not_reported``: the question is only worth asking
because the answer is checked.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from agents.interpretation._lens_base import claim_matches_lens
from agents.interpretation._lens_questions import LensQuestion, ask, question_digest
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass
from services.evidence.programme_outcomes import Engagement, Programme, ProgrammeReading

_MIN_STAGE = 2.0  # a phase 1 failure carries too little weight to be worth a call
_MAX_TRIALS = 5


class EngagementAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engagement: Literal["shown", "not_shown", "not_reported"]
    claim_ids: list[str] = []


ENGAGEMENT_QUESTION = LensQuestion(
    id="target_engagement",
    skill="clinical_q_engagement",
    answer_model=EngagementAnswer,
    # Without a claim that names the drug there is nothing to cite: not reported.
    empty_answer={"engagement": "not_reported", "claim_ids": []},
)


def engagement_digest() -> str:
    return question_digest([ENGAGEMENT_QUESTION])


def programmes_to_ask(reading: ProgrammeReading) -> list[Programme]:
    """The efficacy failures at phase 2 or later."""
    return [
        p
        for p in reading.programmes
        if p.outcome == "failed_efficacy" and p.failure_stage >= _MIN_STAGE
    ]


def programme_claims(programme: Programme, claims: Iterable[CoreClaim]) -> list[CoreClaim]:
    """Clinical- and biology-routed claims whose text names the drug."""
    named = re.compile(rf"\b{re.escape(programme.drug)}\b", re.IGNORECASE)
    return [
        c
        for c in claims
        if (claim_matches_lens(c, "clinical") or claim_matches_lens(c, "biology"))
        and named.search(c.claim_text)
    ]


def programme_facts(
    programme: Programme, trial_facts: Iterable[Mapping[str, Any]], disease: str
) -> str:
    """The drug, its outcome and its trials in the indication: title, phase, status and the
    primary outcome measures when results were posted."""
    by_id = {str(f.get("nct_id") or "").lower(): f for f in trial_facts}
    lines = [
        f"Drug: {programme.drug.lower()} — best stage in {disease}: "
        f"phase {programme.stage:g}; it failed for lack of efficacy.",
    ]
    if programme.reasons:
        lines.append(f"Reason on record: {programme.reasons[0]}")
    listed = 0
    for trial_id in programme.trials:
        fact = by_id.get(trial_id.lower())
        if fact is None or listed >= _MAX_TRIALS:
            continue
        measures = list(
            dict.fromkeys(
                str(a.get("measure")) for a in fact.get("arm_results") or [] if a.get("measure")
            )
        )
        lines.append(
            f"Trial {trial_id}: {fact.get('phase') or 'phase unknown'}, "
            f"{str(fact.get('status') or 'status unknown').lower()} — "
            f"{str(fact.get('title') or 'no title')[:160]}"
            + (f"; primary outcome measures: {'; '.join(measures[:3])}" if measures else "")
        )
        listed += 1
    if listed == 0:
        lines.append("Trials in this indication: " + (", ".join(programme.trials) or "none listed"))
    return "\n".join(lines)


def inputs_digest(facts: str, claims: Iterable[CoreClaim]) -> str:
    """Digest of what the question is shown, for the cache key."""
    h = hashlib.sha256(facts.encode())
    for text in sorted(c.claim_text for c in claims):
        h.update(text.encode())
    return h.hexdigest()


async def ask_engagement(
    programme: Programme,
    *,
    facts: str,
    claims: list[CoreClaim],
    quality_map: dict[str, Any],
    disease_classes: frozenset[str],
    ctx: RunContext,
    classification: DataClass,
) -> tuple[Engagement, list[str]]:
    """The programme's target engagement and the claims behind it.

    ``shown`` and ``not_shown`` need a citation the model was shown; without one, and when
    the question cannot be answered, the result is ``not_reported`` — never a guess.
    """
    result = await ask(
        ENGAGEMENT_QUESTION,
        facts=facts,
        claims=claims,
        quality_map=quality_map,
        disease_classes=disease_classes,
        lens="clinical",
        ctx=ctx,
        classification=classification,
    )
    return normalise_engagement(result.answer)


def normalise_engagement(answer: dict[str, Any] | None) -> tuple[Engagement, list[str]]:
    if answer is None:
        return "not_reported", []
    parsed = EngagementAnswer.model_validate(answer)
    if parsed.engagement != "not_reported" and not parsed.claim_ids:
        return "not_reported", []
    return cast("Engagement", parsed.engagement), list(parsed.claim_ids)
