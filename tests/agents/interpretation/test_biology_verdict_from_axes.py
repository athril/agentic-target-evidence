# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The biology verdict is computed from its axes (settle_biology_verdict)."""

from __future__ import annotations

import itertools
import uuid

import pytest

from agents.interpretation.biology_lens.agent import settle_biology_verdict
from schemas.verdicts import AxisVerdict, LensVerdict

_AXES = ("druggability", "mechanism_of_action", "developability", "perturbation_direction")
_VALUES = (True, False, None)


def _verdict(overall: str, values: dict[str, bool | None]) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="biology",
        target_gene="GENE1",
        disease="disease one",
        overall_verdict=overall,  # type: ignore[arg-type]
        confidence=0.5,
        axes=[AxisVerdict(axis=k, verdict=v, confidence=0.7) for k, v in values.items()],
    )


_COMBOS = [dict(zip(_AXES, vals, strict=True)) for vals in itertools.product(_VALUES, repeat=4)]


@pytest.mark.parametrize("model", ["support", "oppose", "neutral"])
@pytest.mark.parametrize("human", ["supports", "unknown"])
def test_druggability_or_developability_alone_never_oppose(model, human):
    for values in _COMBOS:
        if values["mechanism_of_action"] is False or values["perturbation_direction"] is False:
            continue
        out = settle_biology_verdict(_verdict(model, values), human)
        assert out.overall_verdict != "oppose", values


@pytest.mark.parametrize("model", ["support", "oppose", "neutral"])
def test_an_unfavourable_mechanism_always_opposes(model):
    for values in _COMBOS:
        if values["mechanism_of_action"] is False:
            for human in ("supports", "unknown", "contradicts"):
                assert settle_biology_verdict(_verdict(model, values), human).overall_verdict == (
                    "oppose"
                )


def test_contrary_models_oppose_unless_human_evidence_supports_the_direction():
    values = {"mechanism_of_action": True, "perturbation_direction": False}
    assert settle_biology_verdict(_verdict("support", values), "unknown").overall_verdict == (
        "oppose"
    )
    assert settle_biology_verdict(_verdict("support", values), "supports").overall_verdict == (
        "neutral"
    )


def test_support_needs_every_scored_axis_favourable_and_the_flag_records_the_model():
    values = {"druggability": True, "mechanism_of_action": True, "developability": False}
    out = settle_biology_verdict(_verdict("oppose", values))
    assert out.overall_verdict == "neutral"
    [flag] = [f for f in out.validation_flags if f.rule_id == "biology.verdict_from_axes"]
    assert "oppose → neutral" in flag.message
    favourable = {"druggability": True, "mechanism_of_action": True, "developability": None}
    assert settle_biology_verdict(_verdict("neutral", favourable)).overall_verdict == "support"


def test_an_agreeing_verdict_is_unchanged_and_unparsed_is_left_alone():
    agreeing = _verdict("support", {"mechanism_of_action": True})
    assert settle_biology_verdict(agreeing) is agreeing
    unparsed = _verdict("insufficient_evidence", {})
    assert settle_biology_verdict(unparsed) is unparsed
