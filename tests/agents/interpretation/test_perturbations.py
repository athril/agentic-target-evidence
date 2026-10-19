# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Perturbation outcomes in models of the disease (biology_lens/perturbations.py).

The failure: the biology lens read a target whose blockade worsened models of the
disease as mechanism support, and its skill's direction rule was left to the model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

import agents.interpretation.biology_lens.perturbations as pt
from agents.interpretation._lens_questions import QuestionResult
from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance
from schemas.verdicts import AxisVerdict, LensVerdict

_TERMS = ["GENE-A"]


def _claim(text: str, paper: uuid.UUID | None = None) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=paper or uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENE-A",
        disease="disease one",
        evidence_type=EvidenceType.ARTICLE,
        claim_text=text,
        provenance=Provenance(agent_name="t", timestamp=datetime.now(UTC), trace_id="t"),
        classification=DataClass.NON_SENSITIVE,
    )


def _answer(**kw: Any) -> dict[str, Any]:
    return {
        "perturbed_gene": "GENE-A",
        "change": "lower",
        "change_basis": "GENE-A knockout",
        "model": "mice",
        "phenotype": "phenotype two",
        "outcome": "worse",
        **kw,
    }


def test_candidates_describe_a_perturbation_of_the_target_in_a_model_of_the_disease() -> None:
    model, unrelated = uuid.uuid4(), uuid.uuid4()
    papers = {str(model): {"scope": "related"}, str(unrelated): {"scope": "none"}}
    kept = _claim("GENE-A knockout mice developed more severe phenotype two.", model)
    claims = [
        kept,
        _claim("GENE-A expression is raised in disease one tissue.", model),  # no perturbation
        _claim("GENE-B knockout mice developed phenotype two.", model),  # not the target
        _claim("GENE-A knockout mice developed phenotype three.", unrelated),  # another disease
    ]

    assert pt.candidate_claims(claims, papers, _TERMS) == [kept]


def test_one_vote_per_paper_and_only_for_the_target_with_a_quoted_change() -> None:
    paper_a, paper_b, paper_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    c1 = _claim("GENE-A knockout mice had worse phenotype two.", paper_a)
    c2 = _claim("GENE-A knockout mice had more phenotype two.", paper_a)  # same paper
    c3 = _claim("Gene-b knockout mice had worse phenotype two.", paper_b)
    c4 = _claim("Il-gene-a.", paper_c)
    answers = {
        str(c1.evidence_id): _answer(),
        str(c2.evidence_id): _answer(),
        str(c3.evidence_id): _answer(perturbed_gene="GENE-B"),  # a partner gene
        str(c4.evidence_id): _answer(outcome="no_change"),
    }

    obs = pt.perturbation_observations(answers, [c1, c2, c3, c4], _TERMS)

    assert [(o.source, o.favours) for o in obs] == [(str(paper_a), "increase")]


def test_a_paper_whose_answers_point_both_ways_casts_no_vote() -> None:
    paper = uuid.uuid4()
    worse = _claim("GENE-A knockout worsened phenotype two.", paper)
    better = _claim("GENE-A overexpression worsened phenotype two.", paper)
    answers = {
        str(worse.evidence_id): _answer(),
        str(better.evidence_id): _answer(change="raise", change_basis="overexpression"),
    }

    assert pt.perturbation_observations(answers, [worse, better], _TERMS) == []


def _reading(n_papers: int, outcome: str = "worse") -> dict[str, Any]:
    claims = [_claim(f"GENE-A knockout mice {i}.") for i in range(n_papers)]
    answers = {str(c.evidence_id): _answer(outcome=outcome) for c in claims}
    obs = pt.perturbation_observations(answers, claims, _TERMS)
    return pt.perturbation_reading("decrease", obs).model_dump(mode="json")


def test_one_paper_says_nothing_and_two_unanimous_papers_decide() -> None:
    assert _reading(1)["verdict"] == "unknown"
    assert _reading(2)["verdict"] == "contradicts"
    assert _reading(2, outcome="better")["verdict"] == "supports"


def _verdict(overall: str = "support") -> LensVerdict:
    # A neutral verdict reaching the rule has an uncertain axis (scoring aligns a neutral
    # verdict whose axes are all favourable).
    mechanism = None if overall == "neutral" else True
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="biology",
        target_gene="GENE-A",
        disease="disease one",
        overall_verdict=overall,
        confidence=0.6,
        axes=[
            AxisVerdict(
                axis="mechanism_of_action", verdict=mechanism, confidence=0.6, rationale="m"
            ),
            AxisVerdict(axis="druggability", verdict=True, confidence=0.6, rationale="d"),
        ],
    )


def test_models_worsened_by_the_requested_perturbation_oppose_without_human_support() -> None:
    out = pt.apply_perturbation_axis(_verdict(), _reading(3), "unknown")

    axis = next(a for a in out.axes if a.axis == pt.AXIS)
    assert axis.verdict is False and axis.confidence > 0.3
    assert out.overall_verdict == "oppose"
    assert any(f.rule_id == "biology.perturbation_direction" for f in out.validation_flags)


def test_models_never_overrule_human_evidence_that_supports_the_direction() -> None:
    out = pt.apply_perturbation_axis(_verdict(), _reading(3), "supports")

    assert out.overall_verdict == "support"
    assert next(a for a in out.axes if a.axis == pt.AXIS).verdict is False


@pytest.mark.parametrize(("papers", "outcome", "value"), [(3, "better", True), (1, "worse", None)])
def test_supportive_or_unreplicated_models_leave_the_verdict(papers, outcome, value) -> None:
    out = pt.apply_perturbation_axis(_verdict("neutral"), _reading(papers, outcome), "unknown")

    assert out.overall_verdict == "neutral"
    assert next(a for a in out.axes if a.axis == pt.AXIS).verdict is value


def test_no_reading_no_axis() -> None:
    v = _verdict()
    assert pt.apply_perturbation_axis(v, {}, "unknown") is v
    assert pt.render_perturbation_block({}) == ""


def test_the_prompt_states_the_axis_and_each_papers_outcome() -> None:
    block = pt.render_perturbation_block(_reading(2))

    assert "axis perturbation_direction set by rule: false" in block
    assert "favours raising the target's activity" in block


async def test_answers_for_unshown_claims_or_unquoted_changes_are_dropped(monkeypatch) -> None:
    shown = _claim("GENE-A knockout mice had worse phenotype two.")
    unquoted = _claim("GENE-A mice had worse phenotype two.")
    hidden = _claim("GENE-A knockout mice improved.")
    result = QuestionResult(
        question_id="perturbation_outcome",
        answer={
            "claims": [
                {"claim_id": str(shown.evidence_id), **_answer()},
                {"claim_id": str(unquoted.evidence_id), **_answer()},
                {"claim_id": str(hidden.evidence_id), **_answer()},
            ]
        },
        shown_claim_ids=[str(shown.evidence_id), str(unquoted.evidence_id)],
    )
    monkeypatch.setattr(pt, "ask", AsyncMock(return_value=result))

    out = await pt.ask_perturbations(
        [shown, unquoted, hidden],
        ctx=None,  # type: ignore[arg-type]
        classification=DataClass.NON_SENSITIVE,
    )

    assert set(out) == {str(shown.evidence_id), str(unquoted.evidence_id)}
    assert out[str(unquoted.evidence_id)]["change"] == "unknown"
