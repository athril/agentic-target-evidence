# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Prose that states a verdict other than the final one (_lens_base.align_verdict_statements).

The failure: narratives are written before the deterministic rules, so a rule that set the
verdict left "The overall verdict is support" beside a neutral verdict.
"""

from __future__ import annotations

import uuid

from agents.interpretation._lens_base import align_verdict_statements, map_lens_verdict
from schemas.messages import AgentMessage
from schemas.verdicts import LensVerdict


def _verdict(overall: str, narrative: str, rationale: str = "Reasons.") -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="safety",
        target_gene="GENE-A",
        disease="disease a",
        overall_verdict=overall,
        rationale=rationale,
        narrative=narrative,
    )


def test_a_sentence_stating_another_verdict_is_replaced_and_the_rest_kept() -> None:
    v = _verdict(
        "neutral",
        "Para one is fine. The overall verdict is support given the data.\n\nPara two.",
        rationale="This lens supports the target.",
    )

    out = align_verdict_statements(v)

    assert out.narrative == "Para one is fine. The overall verdict is neutral.\n\nPara two."
    assert out.rationale == "The overall verdict is neutral."
    flags = [f for f in out.validation_flags if f.rule_id == "narrative.verdict_aligned"]
    assert len(flags) == 2 and "support" in flags[0].message


def test_a_matching_statement_or_no_statement_is_left_alone() -> None:
    v = _verdict("oppose", "The verdict is oppose. Supporting evidence is thin.")

    assert align_verdict_statements(v) is v


def test_a_rule_applied_after_the_lens_also_aligns_the_prose() -> None:
    v = _verdict("oppose", "The overall verdict is oppose.")
    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=v.run_id,
        from_agent="safety_lens",
        to_agent="orchestrator",
        intent="result",
        payload={"lens_verdicts": [v.model_dump(mode="json")]},
        trace_id="t",
    )

    out = map_lens_verdict(msg, lambda x: x.model_copy(update={"overall_verdict": "neutral"}))

    after = LensVerdict.model_validate(out.payload["lens_verdicts"][0])
    assert after.narrative == "The overall verdict is neutral."


def _with_axis(value, rationale: str) -> LensVerdict:
    from schemas.verdicts import AxisVerdict

    return _verdict("neutral", "x").model_copy(
        update={"axes": [AxisVerdict(axis="causality", verdict=value, rationale=rationale)]}
    )


def test_an_axis_whose_rationale_contradicts_its_value_is_flagged_not_changed() -> None:
    from agents.interpretation._lens_base import flag_axis_rationale_conflicts

    affirming_null = flag_axis_rationale_conflicts(
        _with_axis(None, "Strong evidence of causality.")
    )
    denying_true = flag_axis_rationale_conflicts(
        _with_axis(True, "There is no evidence of causality.")
    )
    consistent = _with_axis(True, "Carrier studies support causality.")
    negated_support = _with_axis(False, "The data do not support causality.")

    assert [f.rule_id for f in affirming_null.validation_flags] == ["axis_rationale_conflict"]
    assert affirming_null.axes[0].verdict is None
    assert [f.rule_id for f in denying_true.validation_flags] == ["axis_rationale_conflict"]
    assert flag_axis_rationale_conflicts(consistent) is consistent
    assert flag_axis_rationale_conflicts(negated_support) is negated_support


def test_a_harm_phrased_axis_reporting_an_absence_is_not_a_conflict() -> None:
    from agents.interpretation._lens_base import flag_axis_rationale_conflicts
    from schemas.verdicts import AxisVerdict

    v = _verdict("support", "x").model_copy(
        update={"axes": [AxisVerdict(axis="toxicity", verdict=True, rationale="No boxed warning.")]}
    )

    assert flag_axis_rationale_conflicts(v) is v
