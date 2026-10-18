# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The genetics verdict is computed from its axes (settle_genetics_verdict)."""

from __future__ import annotations

import uuid

from agents.interpretation.genetics_lens.agent import direction_check_note, settle_genetics_verdict
from schemas.verdicts import AxisVerdict, LensVerdict


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


def test_causal_genetics_support_even_when_the_model_opposed():
    out = settle_genetics_verdict(_verdict("oppose", causality=True, genetic_validity=True))
    assert out.overall_verdict == "support"


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
