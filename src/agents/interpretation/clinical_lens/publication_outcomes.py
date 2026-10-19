# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Trial outcomes that exist only in publications: which drug, which phase, what happened.

Programmes tested before trials were registered, or whose results were never posted,
exist in the registry and in Open Targets as nothing more than a stage, if at all: a
randomised trial in which the disease worsened on the drug read as "no drug tested in
this indication". This question reads the literature claims that report a trial of one
of the target's drugs in the indication and returns, per claim, what the sentence states:
the drug, the phase, whether the trial was randomised and controlled, the enrolment and
the result (primary endpoint met or not, the disease worsened, or harm).

Code keeps an answer only when its drug is one of the target's known drugs named in the
claim, its claim was shown, and its result is stated; the programme reading merges what
is left (services/evidence/programme_outcomes.apply_publication_outcomes). Answers are
kept per claim text by the caller: they do not depend on the run's direction.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agents.interpretation._lens_questions import LensQuestion, ask, question_digest
from core.evidence_text import mentions_term
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, EvidenceType
from services.evidence.disease_scope import DiseaseScope
from services.evidence.drug_precedent import drug_key
from services.evidence.programme_outcomes import PublicationOutcome

logger = logging.getLogger(__name__)

_BATCH = 15
_MAX_CLAIMS = 45  # three calls at most per run

_LITERATURE_TYPES = frozenset(
    {EvidenceType.ARTICLE, EvidenceType.ABSTRACT, EvidenceType.BOOK, EvidenceType.CONFERENCE}
)
# Designs that report other people's trials, or none: a review's sentence about a drug's
# trial is not attributable to a trial. Unclassified papers (no publication type) are
# kept; the claim must still report a trial.
_NOT_A_TRIAL_REPORT = frozenset({"review", "protocol", "case_report"})
# The claim reports what happened in a trial, not background.
_TRIAL_REPORT = re.compile(
    r"\b(?:trials?|randomi[sz]\w*|placebo|double[- ]blind|phase\s*(?:[1-4]|i{1,3}|iv)\b|"
    r"primary (?:end ?points?|outcomes?)|efficacy|exacerbations?|relapses?|"
    r"(?:patients|participants) (?:treated|receiving|given|randomi[sz]ed))",
    re.IGNORECASE,
)


class OutcomeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    drug: str = ""
    phase: Literal["1", "2", "3", "4", "unknown"] = "unknown"
    design: Literal["randomised_controlled", "uncontrolled", "unknown"] = "unknown"
    enrollment: int | None = None
    result: Literal["met", "not_met", "worsened", "harm", "unknown"] = "unknown"


class OutcomeAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[OutcomeAnswer] = []


OUTCOME_QUESTION = LensQuestion(
    id="publication_outcome",
    skill="clinical_q_publication_outcome",
    answer_model=OutcomeAnswers,
    max_claims=_BATCH,
)


def outcome_digest() -> str:
    return question_digest([OUTCOME_QUESTION])


def candidate_claims(
    claims: Iterable[CoreClaim],
    drugs: Iterable[str],
    papers: Mapping[str, Mapping[str, Any]],
    scope: DiseaseScope | None = None,
) -> list[CoreClaim]:
    """Literature claims worth asking: naming one of ``drugs`` and reporting a trial, from
    a paper about the indication (the run's literature index ``papers``, by paper
    evidence id) that is not a review, protocol or case report. A paper only related to
    the indication (a neighbouring disease that mentions it) counts only for a claim that
    names the indication itself: such papers report trials in the neighbouring disease.
    The strongest designs first, then the most cited papers; at most three batches."""
    names = [d for d in dict.fromkeys(drugs) if d]
    picked: list[tuple[int, float, CoreClaim]] = []
    for c in claims:
        if c.evidence_type not in _LITERATURE_TYPES:
            continue
        paper = papers.get(str(c.source_evidence_id)) or {}
        design = (paper.get("design") or {}).get("clinical") or {}
        if paper.get("excluded") or design.get("family") in _NOT_A_TRIAL_REPORT:
            continue
        text = c.claim_text or ""
        in_scope = paper.get("scope") == "in_scope"
        names_indication = scope is not None and scope.match(text) == "in_scope"
        if not (in_scope or (paper.get("scope") == "related" and names_indication)):
            continue
        if not _TRIAL_REPORT.search(text) or not any(mentions_term(text, d) for d in names):
            continue
        picked.append((int(design.get("level") or 0), float(paper.get("rcr") or 0.0), c))
    picked.sort(key=lambda t: (-t[0], -t[1]))
    return [c for _, _, c in picked[:_MAX_CLAIMS]]


def facts_block(drugs: Iterable[str], disease: str) -> str:
    names = ", ".join(d.lower() for d in dict.fromkeys(drugs) if d)
    return f"Indication: {disease}\nDrugs acting on the target: {names}"


def _named_drug(answer: OutcomeAnswer, claim: CoreClaim, drugs: Iterable[str]) -> str:
    """The known drug the answer names, when the claim names it too; "" otherwise."""
    key = drug_key(answer.drug)
    for d in drugs:
        if key and drug_key(d) == key and mentions_term(claim.claim_text, d):
            return drug_key(d)
    return ""


async def ask_publication_outcomes(
    claims: Iterable[CoreClaim],
    *,
    drugs: list[str],
    disease: str,
    ctx: RunContext,
    classification: DataClass,
) -> dict[str, dict[str, Any]]:
    """Answers for ``claims``: {claim evidence id: answer without the id}. A batch the
    model cannot answer is skipped; its claims stay unanswered."""
    pool = list(claims)
    out: dict[str, dict[str, Any]] = {}
    facts = facts_block(drugs, disease)
    for i in range(0, len(pool), _BATCH):
        batch = pool[i : i + _BATCH]
        try:
            result = await ask(
                OUTCOME_QUESTION,
                facts=facts,
                claims=batch,
                quality_map={},
                disease_classes=frozenset(),
                lens="clinical",
                ctx=ctx,
                classification=classification,
            )
        except Exception as exc:  # degrade: the reading keeps the registry alone
            logger.warning("publication-outcome question failed for a batch: %s", exc)
            continue
        shown = set(result.shown_claim_ids)
        by_id = {str(c.evidence_id): c for c in batch}
        for item in (result.answer or {}).get("claims") or []:
            cid = str(item.get("claim_id") or "")
            if cid in by_id and cid in shown:
                out[cid] = {k: v for k, v in item.items() if k != "claim_id"}
    logger.info("publication-outcome question: %d of %d claim(s) answered", len(out), len(pool))
    return out


def to_outcomes(
    answers: Mapping[str, Mapping[str, Any]],
    claims: Iterable[CoreClaim],
    drugs: list[str],
) -> dict[str, list[PublicationOutcome]]:
    """The checked answers as publication outcomes per drug (``drug_key``).

    Dropped: an unstated result, a drug that is not one of ``drugs`` or that the claim
    does not name, and a claim the answers do not cover.
    """
    by_id = {str(c.evidence_id): c for c in claims}
    out: dict[str, list[PublicationOutcome]] = {}
    for cid, raw in answers.items():
        claim = by_id.get(cid)
        if claim is None:
            continue
        answer = OutcomeAnswer.model_validate({**raw, "claim_id": cid})
        if answer.result == "unknown":
            continue
        drug = _named_drug(answer, claim, drugs)
        if not drug:
            continue
        out.setdefault(drug, []).append(
            PublicationOutcome(
                claim_id=cid,
                phase=0.0 if answer.phase == "unknown" else float(answer.phase),
                randomised=answer.design == "randomised_controlled",
                enrollment=answer.enrollment if (answer.enrollment or 0) > 0 else None,
                result=answer.result,
                excerpt=" ".join((claim.claim_text or "").split()),
            )
        )
    return out
