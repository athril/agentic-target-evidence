# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The genetics verdict is computed from its axes (settle_genetics_verdict)."""

from __future__ import annotations

import itertools
import uuid

import pytest

from agents.interpretation.genetics_lens.agent import direction_check_note, settle_genetics_verdict
from schemas.verdicts import AxisVerdict, LensVerdict
from services.evidence.direction_of_effect import (
    MIN_STRENGTH,
    DirectionObservation,
    DirectionReading,
    assess_direction,
)


def _verdict(overall: str, **axes: bool | None) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="genetics",
        target_gene="GENE1",
        disease="disease one",
        overall_verdict=overall,  # type: ignore[arg-type]
        confidence=0.5,
        axes=[AxisVerdict(axis=k, verdict=v, confidence=0.8) for k, v in axes.items()],
    )


def test_absent_causality_is_neutral_not_oppose():
    out = settle_genetics_verdict(_verdict("oppose", causality=False, genetic_validity=True))
    assert out.overall_verdict == "neutral"
    assert any(f.rule_id == "genetics.verdict_from_axes" for f in out.validation_flags)


def _reading(verdict: str, against: float = 0.0, support: float = 0.0) -> DirectionReading:
    return DirectionReading(requested="decrease", verdict=verdict, support=support, against=against)  # type: ignore[arg-type]


_READINGS = {
    "supports": _reading("supports", support=0.8),
    "contradicts_weak": _reading("contradicts", against=MIN_STRENGTH + 0.05),
    "contradicts_strong": _reading("contradicts", against=0.8),
    "conflicting": _reading("conflicting", against=0.6, support=0.6),
    "unknown": _reading("unknown"),
}
_RANK = {"oppose": 0, "neutral": 1, "support": 2}


def _settled(model: str, causality: bool | None, reading: str) -> str:
    axes: dict[str, bool | None] = {"causality": causality, "genetic_validity": True}
    if reading == "contradicts_strong":
        axes["direction_of_effect"] = False  # what apply_direction sets at this strength
    return settle_genetics_verdict(_verdict(model, **axes), _READINGS[reading]).overall_verdict


def test_causal_genetics_support_when_the_direction_is_read_and_agrees():
    out = settle_genetics_verdict(
        _verdict("oppose", causality=True, genetic_validity=True), _READINGS["supports"]
    )
    assert out.overall_verdict == "support"


def test_a_model_oppose_without_a_direction_reading_becomes_neutral_not_support():
    out = settle_genetics_verdict(_verdict("oppose", causality=True, genetic_validity=True))
    assert out.overall_verdict == "neutral"
    assert "no human genetics read the direction" in out.validation_flags[-1].message


@pytest.mark.parametrize("reading", ["contradicts_weak", "conflicting"])
def test_a_contradicting_or_conflicting_reading_withholds_support(reading):
    for model in ("support", "oppose", "neutral"):
        assert _settled(model, True, reading) == "neutral"


@pytest.mark.parametrize(
    ("model", "causality"),
    list(itertools.product(["support", "oppose", "neutral"], [True, None, False])),
)
def test_removing_direction_evidence_never_makes_the_verdict_more_favourable(model, causality):
    """Dropping the direction reading (to unknown) from any reading never improves the
    verdict, except that removing a contradiction is not penalised below neutral."""
    without = _RANK[_settled(model, causality, "unknown")]
    for reading in ("contradicts_weak", "contradicts_strong", "conflicting"):
        assert _RANK[_settled(model, causality, reading)] <= max(without, _RANK["neutral"])
    # Supporting evidence removed: never better than with it.
    assert without <= _RANK[_settled(model, causality, "supports")]


def _obs(perturbation: str, outcome: str, weight: float) -> DirectionObservation:
    return DirectionObservation(
        perturbation=perturbation,  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        evidence="genetics_structured",
        weight=weight,
        source="s",
        detail="d",
    )


@pytest.mark.parametrize("model", ["support", "oppose", "neutral"])
@pytest.mark.parametrize(
    "observations",
    [
        [_obs("decrease", "benefit", 0.4)],
        [_obs("decrease", "benefit", 0.9)],
        [_obs("decrease", "benefit", 0.5), _obs("increase", "benefit", 0.5)],
        [_obs("decrease", "harm", 0.35), _obs("decrease", "benefit", 0.2)],
    ],
)
def test_opposite_requested_directions_over_the_same_observations_never_both_support(
    model, observations
):
    """When the observations say anything about direction. With none, causal genetics are
    not contradicted either way, so both directions may support."""
    readings = [assess_direction(r, observations) for r in ("decrease", "increase")]  # type: ignore[arg-type]
    assert any(r.verdict != "unknown" for r in readings)
    verdicts = []
    for reading in readings:
        axes: dict[str, bool | None] = {"causality": True, "genetic_validity": True}
        if reading.verdict == "contradicts" and reading.against >= 0.5:
            axes["direction_of_effect"] = False
        verdicts.append(settle_genetics_verdict(_verdict(model, **axes), reading).overall_verdict)
    assert verdicts != ["support", "support"]


def test_an_unfavourable_direction_axis_opposes():
    out = settle_genetics_verdict(
        _verdict("support", causality=True, genetic_validity=True, direction_of_effect=False)
    )
    assert out.overall_verdict == "oppose"


def test_unfavourable_genetic_validity_withholds_support():
    out = settle_genetics_verdict(_verdict("support", causality=True, genetic_validity=False))
    assert out.overall_verdict == "neutral"


def test_an_agreeing_verdict_is_unchanged_and_unparsed_is_left_alone():
    agreeing = _verdict("support", causality=True, genetic_validity=True)
    assert settle_genetics_verdict(agreeing) is agreeing
    unparsed = _verdict("insufficient_evidence")
    assert settle_genetics_verdict(unparsed) is unparsed


def test_the_direction_note_keeps_direction_out_of_causality():
    note = direction_check_note("inhibit")
    assert "decrease" in note and "whichever way" in note
    assert "Never set causality to false because of the direction" in note
    assert direction_check_note("unspecified") == ""
