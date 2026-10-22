# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Perturbation outcomes in models of the disease: which way the target was changed, and
which way the model's disease phenotype moved.

The biology lens read association and expression ("up-regulated in disease") as support,
and a question that asked the model which gene a sentence perturbed wrote another gene's
knockout as the target's. The perturbed gene is now a grounded mention
(services/evidence/grounding.py): a statement (services/evidence/statements.py) is the
target's perturbation only when its exposure is the target or its ortholog in the
model's species, by NCBI Gene id, and

* a genetic perturbation (knockout, knockdown, overexpression): its change is the one the
  sentence states; or
* a compound given in a model (a preclinical design or a non-human species) that is one
  of the target's known drugs: its change is the drug's action. A tool compound outside
  Open Targets known drugs is not one: its effect on the target is unknown.

Kept when the paper is about the disease or one of its model phenotypes
(services/evidence/model_phenotypes.py), or the outcome is in scope by MeSH id, and the
disease outcome moved. Code turns them into ``preclinical_perturbation`` direction
observations, one vote per paper, and reads them as their own direction reading
(``perturbation_reading``) — never pooled with the human evidence that gates the score.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from agents.interpretation._lens_base import align_verdict_statements
from agents.interpretation._lens_confidence import reaggregate
from schemas.statement import TargetStatement
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

logger = logging.getLogger(__name__)

_ABOUT_THE_DISEASE = frozenset({"in_scope", "related"})
_HUMAN = "9606"


def _in_a_model(st: TargetStatement) -> bool:
    return st.design == "preclinical" or st.species not in ("", _HUMAN)


def is_target_perturbation(st: TargetStatement) -> bool:
    """The statement changes the target (or its ortholog) in a model, with its change
    known (module docstring)."""
    if st.effect_on_target not in ("decrease", "increase"):
        return False
    if st.exposure_kind == "genetic_perturbation":
        return True
    return st.exposure_kind == "drug" and _in_a_model(st)


def perturbation_observations(
    statements: Iterable[TargetStatement], papers: Mapping[str, Mapping[str, Any]]
) -> list[DirectionObservation]:
    """One preclinical observation per paper: the direction its perturbation statements
    favour. ``papers`` is the run's literature index (by evidence id, each with its
    ``pmid``, ``scope`` and ``excluded``). A paper whose statements favour both directions
    casts no vote."""
    by_pmid = {str(p.get("pmid") or ""): p for p in papers.values()}
    votes: dict[str, list[tuple[Perturbation, Outcome, str]]] = {}
    for st in statements:
        if not is_target_perturbation(st):
            continue
        if st.outcome_kind != "disease" or st.outcome_direction not in ("higher", "lower"):
            continue
        paper = by_pmid.get(st.source.removeprefix("PMID:")) or {}
        if paper.get("excluded") or not (st.in_scope or paper.get("scope") in _ABOUT_THE_DISEASE):
            continue
        perturbation: Perturbation = st.effect_on_target  # type: ignore[assignment]
        outcome: Outcome = "benefit" if st.outcome_direction == "lower" else "harm"
        how = "drug" if st.exposure_kind == "drug" else "genetic perturbation"
        detail = (
            f"{st.exposure_text} ({how}, {'less' if perturbation == 'decrease' else 'more'} "
            f"target) → {'less' if outcome == 'benefit' else 'more'} {st.outcome_text}"
        )
        votes.setdefault(st.source, []).append((perturbation, outcome, detail))
    out: list[DirectionObservation] = []
    for paper_id, items in sorted(votes.items()):
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
                source=paper_id,
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


def apply_perturbation_axis(verdict: LensVerdict, raw: Mapping[str, Any] | None) -> LensVerdict:
    """Add the computed ``perturbation_direction`` axis; when the models contradict the
    requested direction, the biology verdict is oppose. Which way human genetic variation
    moves the disease is the genetics lens's question, so it does not soften this."""
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
    axis = AxisVerdict(
        axis=AXIS,
        verdict=value,
        confidence=round(strength, 3),
        rationale=rationale,
        corroborative=True,
    )
    axes = [a for a in verdict.axes if a.axis != AXIS] + [axis]
    overall = verdict.overall_verdict
    flags = list(verdict.validation_flags)
    if value is False:
        overall = "oppose"
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
                        else "."
                    )
                ),
            )
        )
    updated = verdict.model_copy(
        update={"axes": axes, "overall_verdict": overall, "validation_flags": flags}
    )
    return align_verdict_statements(reaggregate(updated))
