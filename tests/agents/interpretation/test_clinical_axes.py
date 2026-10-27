# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Clinical axes and verdict computed from the programme reading (clinical_lens/axes.py).

The failure: a phase 3 that failed overall survival, in a class the programme reading
scored at a falsification gate, came out of the clinical lens as "neutral".
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agents.interpretation.clinical_lens.axes import (
    apply_clinical_axes,
    compute_clinical_axes,
    render_fixed_axes,
)
from schemas.verdicts import AxisVerdict, LensVerdict

_GATE = {"min_strength": 0.3, "max": 59, "reason": "programmes against the target failed"}


def _prog(drug: str, outcome: str, stage: float, failure_stage: float = 0.0) -> dict[str, Any]:
    return {
        "drug": drug,
        "effect": "decrease",
        "stage": stage,
        "outcome": outcome,
        "failure_stage": failure_stage,
    }


def _reading(*programmes: dict[str, Any], gate: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "requested": "decrease",
        "programmes": list(programmes),
        "falsification": 0.3 if gate else 0.1,
        "available": True,
        "gate": gate,
    }


def _verdict(overall: str = "neutral", narrative: str = "", **axes: bool | None) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="clinical",
        target_gene="GENE-A",
        disease="disease a",
        overall_verdict=overall,
        confidence=0.5,
        rationale="Model rationale.",
        narrative=narrative,
        axes=[
            AxisVerdict(axis=name, verdict=value, confidence=0.4, rationale="model")
            for name, value in axes.items()
        ],
    )


def _axes(v: LensVerdict) -> dict[str, bool | None]:
    return {a.axis: a.verdict for a in v.axes}


def _apply(precedent: str, reading: dict[str, Any], verdict: LensVerdict | None = None):
    axes = compute_clinical_axes({"status": precedent, "summary": "Precedent."}, reading)
    assert axes is not None
    return axes, apply_clinical_axes(verdict or _verdict(), axes)


def test_an_approved_drug_in_the_indication_supports_on_both_axes() -> None:
    axes, out = _apply("validated", _reading(_prog("DRUG-A", "approved", 4.0)))

    assert axes.rule == "approved"
    assert out.overall_verdict == "support"
    assert _axes(out) == {"clinical_precedent": True, "clinical_validation": True}


def test_opposite_direction_approvals_oppose_on_both_axes() -> None:
    _, out = _apply("opposed", _reading())

    assert out.overall_verdict == "oppose"
    assert _axes(out) == {"clinical_precedent": False, "clinical_validation": False}


def test_a_failed_phase_3_at_a_falsification_gate_opposes_whatever_the_model_said() -> None:
    model = _verdict("neutral", clinical_precedent=True, clinical_validation=None)
    reading = _reading(
        _prog("DRUG-A", "failed_hard_endpoint", 3.0, 3.0),
        _prog("DRUG-B", "early", 2.0),
        gate=_GATE,
    )

    axes, out = _apply("stalled", reading, model)

    assert axes.rule == "falsified"
    assert out.overall_verdict == "oppose"
    assert _axes(out) == {"clinical_precedent": True, "clinical_validation": False}
    assert any(f.rule_id == "clinical.computed_axes.falsified" for f in out.validation_flags)


def test_being_tested_in_the_clinic_does_not_weaken_an_oppose() -> None:
    """The precedent axis says the hypothesis was tested, which a failure was: it must not
    count against the oppose it led to."""
    reading = _reading(_prog("DRUG-A", "failed_efficacy", 3.0, 3.0), gate=_GATE)

    _, out = _apply("failed", reading)

    validation = next(a for a in out.axes if a.axis == "clinical_validation")
    assert out.confidence == pytest.approx(validation.confidence)


def test_every_tested_programme_failing_below_the_gate_still_opposes() -> None:
    """One phase 2 that failed, nothing else tested: the clinical evidence is negative, even
    though one failure is too weak to bound the score."""
    axes, out = _apply("failed", _reading(_prog("DRUG-A", "failed_efficacy", 2.0, 2.0)))

    assert axes.rule == "all_failed"
    assert out.overall_verdict == "oppose"


def test_a_failure_beside_a_running_programme_is_pending_not_negative() -> None:
    reading = _reading(
        _prog("DRUG-A", "failed_efficacy", 2.0, 2.0), _prog("DRUG-B", "ongoing", 3.0)
    )

    axes, out = _apply("late_stage", reading, _verdict("oppose"))

    assert axes.rule == "pending"
    assert out.overall_verdict == "neutral"
    assert _axes(out)["clinical_validation"] is None


def test_a_met_phase_3_supports_when_nothing_failed() -> None:
    axes, out = _apply("stalled", _reading(_prog("DRUG-A", "positive_unfiled", 3.0)))

    assert axes.rule == "positive"
    assert out.overall_verdict == "support"


def test_a_met_phase_3_beside_failed_programmes_is_mixed() -> None:
    reading = _reading(
        _prog("DRUG-A", "positive_unfiled", 3.0),
        _prog("DRUG-B", "failed_efficacy", 3.0, 3.0),
        _prog("DRUG-C", "discontinued_business", 3.0),
    )

    axes, out = _apply("stalled", reading, _verdict("oppose"))

    assert axes.rule == "mixed"
    assert out.overall_verdict == "neutral"


def test_phase_1_only_leaves_both_axes_open() -> None:
    axes, out = _apply("in_clinic", _reading(_prog("DRUG-A", "early", 1.0)), _verdict("support"))

    assert axes.rule == "none"
    assert out.overall_verdict == "neutral"
    assert _axes(out) == {"clinical_precedent": None, "clinical_validation": None}


def test_nothing_tested_keeps_an_evidence_gap_verdict() -> None:
    _, out = _apply("untested", _reading(), _verdict("insufficient_evidence"))

    assert out.overall_verdict == "insufficient_evidence"


def test_business_stops_alone_are_not_failures() -> None:
    axes, out = _apply("stalled", _reading(_prog("DRUG-A", "discontinued_business", 3.0)))

    assert axes.rule == "none"
    assert out.overall_verdict == "neutral"
    assert _axes(out)["clinical_precedent"] is True


@pytest.mark.parametrize(
    ("precedent", "reading"),
    [
        ({"status": "undetermined"}, _reading()),
        ({"status": "failed"}, {**_reading(), "available": False}),
        ({}, _reading()),
        ({"status": "failed"}, {}),
    ],
)
def test_no_usable_reading_computes_nothing(
    precedent: dict[str, Any], reading: dict[str, Any]
) -> None:
    assert compute_clinical_axes(precedent, reading) is None


def test_prose_stating_the_models_verdict_is_aligned_with_the_computed_one() -> None:
    model = _verdict("neutral", "Trials exist. The overall verdict is neutral.")
    reading = _reading(_prog("DRUG-A", "failed_efficacy", 3.0, 3.0), gate=_GATE)

    _, out = _apply("failed", reading, model)

    assert out.narrative == "Trials exist. The overall verdict is oppose."
    assert out.rationale.startswith("The failed programmes falsify the mechanism")


def test_an_agreeing_verdict_is_not_flagged() -> None:
    model = _verdict("support", clinical_precedent=True, clinical_validation=True)

    _, out = _apply("validated", _reading(_prog("DRUG-A", "approved", 4.0)), model)

    assert not any(f.rule_id.startswith("clinical.computed_axes") for f in out.validation_flags)
    # Agreeing axes are lifted to the rule's confidence.
    assert all(a.confidence >= 0.85 for a in out.axes)


def test_the_prompt_states_the_fixed_axes_and_the_verdict_they_imply() -> None:
    reading = _reading(_prog("DRUG-A", "failed_efficacy", 3.0, 3.0), gate=_GATE)
    axes = compute_clinical_axes({"status": "failed"}, reading)
    assert axes is not None

    block = render_fixed_axes(axes)

    assert "clinical_precedent: true — " in block
    assert "clinical_validation: false — The failed programmes falsify" in block
    assert "overall verdict: oppose" in block


def test_each_drug_is_named_under_the_stage_it_reached():
    """PLK1: "reached phase 3 (volasertib, bi-2536, onvansertib)" — two reached phase 2."""
    from agents.interpretation.clinical_lens.axes import _precedent
    from services.evidence.programme_outcomes import Programme, ProgrammeReading

    progs = [
        Programme(drug="VOLASERTIB", effect="decrease", stage=3.0, outcome="failed_hard_endpoint"),
        Programme(drug="BI-2536", effect="decrease", stage=2.0, outcome="early"),
        Programme(drug="ONVANSERTIB", effect="decrease", stage=2.0, outcome="ongoing"),
    ]
    value, reason = _precedent("failed", ProgrammeReading(requested="decrease", programmes=progs))
    assert value is True
    assert "phase 3: volasertib; phase 2: bi-2536, onvansertib" in reason
