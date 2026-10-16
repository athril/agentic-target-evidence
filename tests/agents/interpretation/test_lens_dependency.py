# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Post-LLM dependency and exposure rules (agents/interpretation/_lens_dependency.py)."""

from __future__ import annotations

import uuid

import pytest

from agents.interpretation._lens_dependency import (
    UNEXPOSED_TOXICITY_CAP,
    apply_common_essential,
    cap_unexposed_toxicity,
)
from schemas.verdicts import AxisVerdict, LensVerdict

_ESSENTIAL = {"kind": "common_essential", "text": "common essential"}


def _ax(name: str, verdict: bool | None, conf: float = 0.8) -> AxisVerdict:
    return AxisVerdict(
        axis=name, verdict=verdict, confidence=conf, rationale="r", evidence_sources=1
    )


def _v(lens: str, overall: str, axes: list[AxisVerdict]) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens=lens,
        target_gene="G",
        disease="d",
        overall_verdict=overall,
        confidence=0.8,
        axes=axes,
        rationale="ok",
    )


_BIOLOGY = _v(
    "biology",
    "support",
    [_ax("druggability", True), _ax("mechanism_of_action", True), _ax("developability", True)],
)
_SAFETY = _v("safety", "support", [_ax("toxicity", True, 0.7), _ax("tissue_specificity", True)])


def test_biology_does_not_count_common_essentiality_as_disease_mechanism():
    """A pan-essential kinase was "a strong target" for one cancer."""
    out = apply_common_essential(
        _BIOLOGY, _ESSENTIAL, lowers_activity=True, validated=False, is_oncology=True
    )
    assert out.overall_verdict == "neutral"
    assert next(a for a in out.axes if a.axis == "mechanism_of_action").verdict is None
    assert any(f.rule_id == "dependency.common_essential" for f in out.validation_flags)


@pytest.mark.parametrize("oncology,expected", [(True, "neutral"), (False, "oppose")])
def test_safety_marks_lowering_a_common_essential_target_as_a_concern(oncology, expected):
    out = apply_common_essential(
        _SAFETY, _ESSENTIAL, lowers_activity=True, validated=False, is_oncology=oncology
    )
    assert out.overall_verdict == expected
    assert next(a for a in out.axes if a.axis == "toxicity").verdict is False


@pytest.mark.parametrize(
    "profile,lowers,validated",
    [
        (_ESSENTIAL, True, True),  # an approved drug settles the window clinically
        (_ESSENTIAL, False, False),  # raising activity is not limited by essentiality
        ({"kind": "lineage_selective", "text": ""}, True, False),
        (None, True, False),
    ],
)
def test_the_rule_does_not_fire_outside_its_case(profile, lowers, validated):
    for v in (_BIOLOGY, _SAFETY):
        out = apply_common_essential(
            v, profile, lowers_activity=lowers, validated=validated, is_oncology=True
        )
        assert out is v


def test_unexposed_liabilities_cap_the_toxicity_axis():
    out = cap_unexposed_toxicity(_SAFETY, [{"severity": "medium"}] * 2, human_exposure=False)
    assert next(a for a in out.axes if a.axis == "toxicity").confidence == UNEXPOSED_TOXICITY_CAP


@pytest.mark.parametrize("liabilities,exposure", [([{}] * 2, True), ([{}], False)])
def test_exposure_or_a_single_liability_leaves_toxicity_alone(liabilities, exposure):
    v = _v("safety", "support", [_ax("toxicity", True, 0.9)])
    assert cap_unexposed_toxicity(v, liabilities, human_exposure=exposure) is v
