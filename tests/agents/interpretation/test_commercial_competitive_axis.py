# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The commercial lens's competitive axis and verdict, set by rule after the model call.

The failure this prevents: the model set competitive_opportunity unfavourable for every
treated indication, copying the landscape block's sentence as its rationale, so the lens
could only oppose unless another rule rescued it.
"""

from __future__ import annotations

import uuid

from agents.interpretation.commercial_lens.agent import computed_competitive_axis
from schemas.verdicts import AxisVerdict, LensVerdict


def _verdict(competitive: bool | None, market: bool | None = None, overall: str = "oppose"):
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="commercial",
        target_gene="GENE-A",
        disease="disease a",
        overall_verdict=overall,
        axes=[
            AxisVerdict(
                axis="competitive_opportunity",
                verdict=competitive,
                confidence=0.8,
                rationale="Disease a itself is contested.",
            ),
            AxisVerdict(axis="market_size", verdict=market, confidence=0.5),
        ],
    )


def _spec(**kw):
    return {
        "disease": "disease a",
        "precedent_status": "untested",
        "programme_reading": {"available": True, "programmes": [], "gate": None},
        "indication_landscape_known": True,
        "indication_approved_drug_count": 0,
        **kw,
    }


def _axis(v: LensVerdict) -> AxisVerdict:
    return next(a for a in v.axes if a.axis == "competitive_opportunity")


def test_an_untreated_indication_with_no_programme_supports():
    v = computed_competitive_axis(_verdict(False), _spec())

    assert _axis(v).verdict is True
    assert v.overall_verdict == "support"
    assert "contested" not in _axis(v).rationale
    assert any("verdict oppose → support" in f.message for f in v.validation_flags)


def test_a_too_small_market_keeps_a_first_in_class_opening_neutral():
    v = computed_competitive_axis(_verdict(False, market=False), _spec())

    assert v.overall_verdict == "neutral"


def test_a_follower_is_neutral_and_an_abandoned_class_opposes_once():
    follower = computed_competitive_axis(
        _verdict(False), _spec(precedent_status="validated", indication_approved_drug_count=4)
    )
    gated = computed_competitive_axis(
        _verdict(True, overall="support"),
        _spec(
            precedent_status="failed",
            programme_reading={
                "available": True,
                "programmes": [{"outcome": "failed_efficacy"}],
                "gate": {"max": 59},
            },
        ),
    )

    from schemas.verdicts import COUNTED_BY_GATE_RULE

    assert (follower.overall_verdict, _axis(follower).verdict) == ("neutral", None)
    assert (gated.overall_verdict, _axis(gated).verdict) == ("oppose", False)
    assert COUNTED_BY_GATE_RULE in {f.rule_id for f in gated.validation_flags}
    assert COUNTED_BY_GATE_RULE not in {f.rule_id for f in follower.validation_flags}


def test_a_gate_on_an_unavailable_programme_reading_does_not_count():
    v = computed_competitive_axis(
        _verdict(False),
        _spec(programme_reading={"available": False, "programmes": [], "gate": {"max": 59}}),
    )

    assert _axis(v).verdict is True
