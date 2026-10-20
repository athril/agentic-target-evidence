# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""What a verdict derived is refreshed when a rule changes the verdict
(agents/interpretation/_lens_base.refresh_derived).

The failure these prevent: a biology lens model said "oppose" beside four favourable axes;
the rule set "support" from the axes, but the report still printed "the verdict
contradicts every scored axis" (a flag raised on the model's verdict) above a summary
arguing the opposite of the verdict shown.
"""

from __future__ import annotations

import uuid

from agents.interpretation._lens_base import map_lens_verdict, refresh_derived
from agents.interpretation._lens_confidence import axis_conflict_flag
from agents.interpretation._lens_precedent import map_result_verdict
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict

_RUN = uuid.uuid4()


def _verdict(overall: str, axis_verdict: bool) -> LensVerdict:
    v = LensVerdict(
        run_id=_RUN,
        trace_id="t",
        lens="biology",
        target_gene="GENEX",
        disease="disease one",
        overall_verdict=overall,
        confidence=0.0,
        axes=[
            AxisVerdict(axis="druggability", verdict=axis_verdict, confidence=0.9, rationale="r")
        ],
        rationale="An unfavourable target. The overall verdict is oppose.",
    )
    return v.model_copy(update={"validation_flags": [axis_conflict_flag(v)]})


def test_a_rule_that_aligns_the_verdict_clears_the_stale_conflict_and_keeps_the_models():
    before = _verdict("oppose", True)
    after = refresh_derived(before, before.model_copy(update={"overall_verdict": "support"}))

    assert "verdict_axis_conflict" not in {f.rule_id for f in after.validation_flags}
    assert after.model_verdict == "oppose"
    assert "The overall verdict is support." in after.rationale


def test_the_models_verdict_survives_a_second_rule_and_clears_when_restored():
    first = _verdict("oppose", True)
    second = refresh_derived(first, first.model_copy(update={"overall_verdict": "support"}))
    third = refresh_derived(second, second.model_copy(update={"overall_verdict": "neutral"}))
    restored = refresh_derived(third, third.model_copy(update={"overall_verdict": "oppose"}))

    assert third.model_verdict == "oppose"
    assert restored.model_verdict is None


def test_a_rule_that_creates_a_conflict_flags_it():
    before = _verdict("support", True).model_copy(update={"validation_flags": []})
    after = refresh_derived(before, before.model_copy(update={"overall_verdict": "oppose"}))

    assert "verdict_axis_conflict" in {f.rule_id for f in after.validation_flags}


def test_both_rule_helpers_refresh():
    message = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=_RUN,
        from_agent="biology_lens",
        to_agent="orchestrator",
        intent="result",
        payload={"lens_verdicts": [_verdict("oppose", True).model_dump(mode="json")]},
        trace_id="t",
    )

    def rule(v: LensVerdict) -> LensVerdict:
        return v.model_copy(update={"overall_verdict": "support"})

    for mapped in (map_lens_verdict(message, rule), map_result_verdict(message, rule)):
        out = LensVerdict.model_validate(mapped.payload["lens_verdicts"][0])
        assert out.model_verdict == "oppose"
        assert "verdict_axis_conflict" not in {f.rule_id for f in out.validation_flags}
