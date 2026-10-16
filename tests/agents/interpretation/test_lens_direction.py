# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Post-LLM direction-of-effect rule (agents/interpretation/_lens_direction.py)."""

from __future__ import annotations

import uuid

from agents.interpretation._lens_direction import AXIS, apply_direction
from schemas.verdicts import AxisVerdict, LensVerdict
from services.evidence.direction_of_effect import DirectionObservation, assess_direction


def _verdict(overall: str = "support") -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="genetics",
        target_gene="G",
        disease="d",
        overall_verdict=overall,
        confidence=0.62,
        axes=[
            AxisVerdict(
                axis="causality",
                verdict=True,
                confidence=0.6,
                rationale="loss-of-function carriers have lower risk",
                evidence_sources=2,
            )
        ],
        rationale="Genetics support the target.",
    )


def _obs(favoured: str, weight: float, kind: str = "mendelian") -> DirectionObservation:
    return DirectionObservation(
        perturbation=favoured,
        outcome="benefit",
        evidence=kind,
        weight=weight,
        source="s",
        detail=f"{kind} evidence favouring {favoured}",
    )


def test_clear_contradiction_turns_support_into_oppose():
    """Activation was supported while loss-of-function carriers were protected."""
    reading = assess_direction(
        "increase", [_obs("decrease", 0.42), _obs("decrease", 0.25, "genetics_literature")]
    )
    out = apply_direction(_verdict(), reading)
    assert out.overall_verdict == "oppose"
    [axis] = [a for a in out.axes if a.axis == AXIS]
    assert axis.verdict is False and axis.confidence == reading.against
    assert any(f.rule_id == "direction_of_effect.contradicted" for f in out.validation_flags)
    assert out.direction_of_effect.startswith("The direction-of-effect evidence contradicts")
    # causality still stands: the gene is causal, the direction is wrong
    assert next(a for a in out.axes if a.axis == "causality").verdict is True


def test_weak_or_conflicting_evidence_only_records_the_summary():
    weak = assess_direction("increase", [_obs("decrease", 0.35)])
    conflicting = assess_direction("increase", [_obs("decrease", 0.6), _obs("increase", 0.5)])
    for reading in (weak, conflicting):
        out = apply_direction(_verdict(), reading)
        assert out.overall_verdict == "support"
        assert out.axes == _verdict().axes
        assert out.direction_of_effect


def test_a_supporting_reading_leaves_the_verdict_alone():
    reading = assess_direction("decrease", [_obs("decrease", 0.8)])
    out = apply_direction(_verdict(), reading)
    assert out.overall_verdict == "support" and out.confidence == 0.62


def test_no_observations_no_summary():
    out = apply_direction(_verdict(), assess_direction("decrease", []))
    assert out.direction_of_effect == ""
