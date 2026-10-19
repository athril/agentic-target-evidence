# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Perturbation outcomes in models of the disease: which way the target was changed, and
which way the model's disease phenotype moved.

The biology lens read association and expression ("up-regulated in disease") as support,
and the skill's hard rule — a knockout that worsens the model is evidence against an
inhibitor — was left to the model to apply. This question asks only what a sentence
states about one perturbation: the gene perturbed, whether its activity was lowered or
raised (with the words that say so), the model and its phenotype, and whether the
phenotype got better, worse or did not change. Code turns the checked answers into
``preclinical_perturbation`` direction observations, one vote per paper, and reads them
as their own direction reading (``perturbation_reading``) — never pooled with the human
evidence that gates the score.

Only claims from papers about the disease or one of its model phenotypes
(services/evidence/model_phenotypes.py), describing a perturbation in a model and naming
the target, are asked, a batch per call; answers are kept per claim text by the caller.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agents.interpretation._lens_base import align_verdict_statements
from agents.interpretation._lens_confidence import reaggregate
from agents.interpretation._lens_questions import LensQuestion, ask, question_digest
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, EvidenceType
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag
from services.evidence.direction_of_effect import (
    EVIDENCE_WEIGHTS,
    DirectionObservation,
    DirectionReading,
    Outcome,
    Perturbation,
    assess_direction,
)
from services.evidence.drug_precedent import Effect
from services.evidence.literature_selection import symbol_pattern

logger = logging.getLogger(__name__)

_BATCH = 15
# One answer entry is under 100 tokens; the bound leaves room for long quoted words.
_TOKENS_PER_CLAIM = 160
_MAX_CLAIMS = 45
_LITERATURE_TYPES = frozenset(
    {EvidenceType.ARTICLE, EvidenceType.ABSTRACT, EvidenceType.BOOK, EvidenceType.CONFERENCE}
)
_ABOUT_THE_DISEASE = frozenset({"in_scope", "related"})
# A perturbation of a gene or its product, in an experimental model.
PERTURBATION = re.compile(
    r"\b(?:knock[- ]?outs?|knock[- ]?downs?|deficien\w*|null|-/-|silenc\w*|delet\w*|"
    r"ablat\w*|inhibit\w*|blockade|block\w*|neutrali[sz]\w*|antibod\w*|antagoni\w*|"
    r"agonis\w*|overexpress\w*|transgenic|administration|treatment with)\b",
    re.IGNORECASE,
)
MODEL = re.compile(
    r"\b(?:mice|mouse|murine|rats?|zebrafish|rabbits?|pigs?|primates?|"
    r"models?|induced|experimental)\b",
    re.IGNORECASE,
)


class PerturbationAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    perturbed_gene: str = ""
    change: Literal["lower", "raise", "unknown"] = "unknown"
    change_basis: str = ""
    model: str = ""
    phenotype: str = ""
    outcome: Literal["better", "worse", "no_change", "unknown"] = "unknown"


class PerturbationAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[PerturbationAnswer] = []


PERTURBATION_QUESTION = LensQuestion(
    id="perturbation_outcome",
    skill="biology_q_perturbation",
    answer_model=PerturbationAnswers,
    max_claims=_BATCH,
    tokens_per_claim=_TOKENS_PER_CLAIM,
)


def perturbation_digest() -> str:
    return question_digest([PERTURBATION_QUESTION])


def candidate_claims(
    claims: Iterable[CoreClaim],
    papers: Mapping[str, Mapping[str, Any]],
    target_terms: Iterable[str],
) -> list[CoreClaim]:
    """Literature claims from papers about the disease or one of its model phenotypes that
    name the target and describe a perturbation in a model; strongest designs first."""
    target = symbol_pattern(list(target_terms))
    picked: list[tuple[int, float, CoreClaim]] = []
    for c in claims:
        if c.evidence_type not in _LITERATURE_TYPES:
            continue
        paper = papers.get(str(c.source_evidence_id)) or {}
        if paper.get("excluded") or paper.get("scope") not in _ABOUT_THE_DISEASE:
            continue
        text = c.claim_text or ""
        if target is None or not target.search(text):
            continue
        if not (PERTURBATION.search(text) and MODEL.search(text)):
            continue
        design = (paper.get("design") or {}).get("biology") or {}
        picked.append((int(design.get("level") or 0), float(paper.get("rcr") or 0.0), c))
    picked.sort(key=lambda t: (-t[0], -t[1]))
    return [c for _, _, c in picked[:_MAX_CLAIMS]]


def _quoted(basis: str, text: str) -> bool:
    def norm(s: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())

    b = norm(basis)
    return bool(b) and b in norm(text)


async def ask_perturbations(
    claims: Iterable[CoreClaim], *, ctx: RunContext, classification: DataClass
) -> dict[str, dict[str, Any]]:
    """Answers for ``claims``: {claim evidence id: answer without the id}. An answer whose
    ``change_basis`` is not in the claim loses its change (unknown); a batch the model
    cannot answer is skipped."""
    pool = list(claims)
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(pool), _BATCH):
        batch = pool[i : i + _BATCH]
        by_id = {str(c.evidence_id): c for c in batch}
        try:
            result = await ask(
                PERTURBATION_QUESTION,
                facts="",
                claims=batch,
                quality_map={},
                disease_classes=frozenset(),
                lens="biology",
                ctx=ctx,
                classification=classification,
            )
        except Exception as exc:  # degrade: no preclinical reading
            logger.warning("perturbation question failed for a batch: %s", exc)
            continue
        shown = set(result.shown_claim_ids)
        for item in (result.answer or {}).get("claims") or []:
            cid = str(item.get("claim_id") or "")
            claim = by_id.get(cid)
            if claim is None or cid not in shown:
                continue
            answer = {k: v for k, v in item.items() if k != "claim_id"}
            if answer.get("change") in ("lower", "raise") and not _quoted(
                str(answer.get("change_basis") or ""), claim.claim_text
            ):
                answer["change"] = "unknown"
            out[cid] = answer
    logger.info("perturbation question: %d of %d claim(s) answered", len(out), len(pool))
    return out


def _same_gene(written: str, symbols: Iterable[str]) -> bool:
    """The written gene is one of the target's symbols, up to case and separators
    ("Il17a", "IL-17A")."""

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    w = norm(written)
    return bool(w) and any(w == norm(s) for s in symbols if s)


def perturbation_observations(
    answers: Mapping[str, Mapping[str, Any]],
    claims: Iterable[CoreClaim],
    target_terms: Iterable[str],
) -> list[DirectionObservation]:
    """One preclinical observation per paper: the direction its checked answers favour.

    Kept: the perturbed gene is the target, the change is lowered or raised with its words
    in the claim, and the phenotype got better or worse. A paper whose answers favour both
    directions casts no vote.
    """
    symbols = [t for t in target_terms if t]
    by_id = {str(c.evidence_id): c for c in claims}
    votes: dict[str, list[tuple[Perturbation, Outcome, str]]] = {}
    for cid, raw in answers.items():
        claim = by_id.get(cid)
        answer = PerturbationAnswer.model_validate({**raw, "claim_id": cid})
        if claim is None or not _same_gene(answer.perturbed_gene, symbols):
            continue
        if answer.change == "unknown" or answer.outcome not in ("better", "worse"):
            continue
        perturbation: Perturbation = "decrease" if answer.change == "lower" else "increase"
        outcome: Outcome = "benefit" if answer.outcome == "better" else "harm"
        detail = f"{answer.model or 'a model'}: {answer.change_basis} → {answer.outcome}"
        votes.setdefault(str(claim.source_evidence_id), []).append((perturbation, outcome, detail))
    out: list[DirectionObservation] = []
    for paper, items in sorted(votes.items()):
        favoured = {
            p if o == "benefit" else ("increase" if p == "decrease" else "decrease")
            for p, o, _ in items
        }
        if len(favoured) != 1:
            continue
        perturbation, outcome, detail = items[0]
        out.append(
            DirectionObservation(
                perturbation=perturbation,
                outcome=outcome,
                evidence="preclinical_perturbation",
                in_scope=True,
                weight=EVIDENCE_WEIGHTS["preclinical_perturbation"],
                source=paper,
                detail=detail[:200],
            )
        )
    return out


def perturbation_reading(
    requested: Effect, observations: Iterable[DirectionObservation]
) -> DirectionReading:
    return assess_direction(requested, observations)


AXIS = "perturbation_direction"


def _axis_value(reading: DirectionReading) -> bool | None:
    return {"supports": True, "contradicts": False}.get(reading.verdict)


def _evidence_line(reading: DirectionReading, n: int = 3) -> str:
    sides = reading.supporting if reading.verdict == "supports" else reading.contradicting
    return "; ".join(o.detail for o in sides[:n])


def render_perturbation_block(raw: Mapping[str, Any] | None) -> str:
    """Prompt block: the perturbation reading and the axis it sets (fixed)."""
    if not raw:
        return ""
    reading = DirectionReading.model_validate(dict(raw))
    if not reading.observations:
        return ""
    value = _axis_value(reading)
    word = {True: "true", False: "false", None: "null"}[value]
    return "\n".join(
        [
            "Perturbation outcomes in models of the disease (pre-computed, one vote per "
            f"paper; {len(reading.observations)} paper(s)) — axis {AXIS} set by rule: {word}.",
            f"  {reading.summary()}",
            *(
                f"  • {o.detail} (favours {'lowering' if o.favours == 'decrease' else 'raising'} "
                "the target's activity)"
                for o in reading.observations[:6]
            ),
            "  Use these as the direction of the model evidence; do not count a model in "
            "which the requested perturbation worsened the disease as support.",
        ]
    )


def apply_perturbation_axis(
    verdict: LensVerdict, raw: Mapping[str, Any] | None, human_verdict: str
) -> LensVerdict:
    """Add the computed ``perturbation_direction`` axis; when the models contradict the
    requested direction and the human evidence does not support it, the biology verdict
    is oppose. Preclinical evidence never overrules a human reading that supports the
    direction: then the conflict is only flagged."""
    if not raw or not verdict.axes:
        return verdict
    reading = DirectionReading.model_validate(dict(raw))
    if not reading.observations:
        return verdict
    value = _axis_value(reading)
    strength = reading.support if value else reading.against if value is False else 0.0
    rationale = (
        f"{reading.summary()} {_evidence_line(reading)}".strip()
        if value is not None
        else reading.summary()
    )
    axis = AxisVerdict(axis=AXIS, verdict=value, confidence=round(strength, 3), rationale=rationale)
    axes = [a for a in verdict.axes if a.axis != AXIS] + [axis]
    overall = verdict.overall_verdict
    flags = list(verdict.validation_flags)
    if value is False and human_verdict != "supports":
        overall = "oppose"
    if value is False:
        flags.append(
            ValidationFlag(
                lens="biology",
                severity="medium" if overall != verdict.overall_verdict else "low",
                rule_id="biology.perturbation_direction",
                claim_excerpt=_evidence_line(reading)[:300],
                message=(
                    "Perturbing the target in the requested direction worsened models of the "
                    "disease"
                    + (
                        f"; verdict {verdict.overall_verdict} → oppose."
                        if overall != verdict.overall_verdict
                        else (
                            "; the human evidence supports the direction, so the verdict stands."
                            if human_verdict == "supports"
                            else "."
                        )
                    )
                ),
            )
        )
    updated = verdict.model_copy(
        update={"axes": axes, "overall_verdict": overall, "validation_flags": flags}
    )
    return align_verdict_statements(reaggregate(updated))
