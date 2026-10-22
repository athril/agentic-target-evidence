# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A claim's typed effect and its derived direction (services/evidence/claim_effect.py).

The failure these prevent: the extractor chose a therapeutic direction per claim, a sign
product it got wrong both ways, and two fixed-choice fields that followed typed a
biomarker's level as the target's. The target's change now comes from the target's drug
data when the claim names its drugs, and otherwise from the statement of the claim's
sentence; code derives the direction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from schemas.evidence import CoreClaim, DataClass, Direction, EvidenceType, Provenance
from schemas.statement import TargetStatement
from services.evidence.claim_effect import (
    effect_label,
    settle_effect,
    statement_outcome,
    target_drug_effects,
    therapeutic_direction,
    with_statement,
)

_DRUGS = {"DRUGX": "decrease", "DRUGY": "increase"}


def _settle(text: str, outcome: str = "none", **kw):
    raw = {"outcome": outcome, **kw}
    return settle_effect(raw, text, target_drugs=_DRUGS)


def _claim(text: str = "A claim.", **kw) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid4(),
        source_evidence_id=uuid4(),
        run_id=uuid4(),
        gene="ABC1",
        disease="DISEASE-A",
        evidence_type=EvidenceType.ARTICLE,
        claim_text=text,
        provenance=Provenance(agent_name="t", timestamp=datetime.now(UTC), trace_id="t"),
        classification=DataClass.NON_SENSITIVE,
        **kw,
    )


def _statement(**kw) -> TargetStatement:
    return TargetStatement(source="PMID:1", sentence=2, text="A sentence.", **kw)


@pytest.mark.parametrize(
    ("perturbation", "outcome", "direction"),
    [
        ("decrease", "benefit", Direction.INHIBIT),
        ("increase", "harm", Direction.INHIBIT),
        ("increase", "benefit", Direction.ACTIVATE),
        ("decrease", "harm", Direction.ACTIVATE),
        ("decrease", "adverse", Direction.UNSPECIFIED),
        ("decrease", "none", Direction.UNSPECIFIED),
        ("none", "benefit", Direction.UNSPECIFIED),
    ],
)
def test_the_direction_is_the_product_of_the_two_factors(perturbation, outcome, direction):
    assert therapeutic_direction(perturbation, outcome) == direction


def test_a_known_drug_of_the_target_types_the_claim():
    effect = _settle("DRUGX increased disease events.", outcome="harm")

    assert (effect.perturbation, effect.effect_source) == ("decrease", "drug")
    assert effect.effect_basis == "DRUGX"
    assert effect.direction == Direction.ACTIVATE  # lowering the target harmed


def test_without_drug_data_the_claim_is_untyped_whatever_it_says():
    """The two fixed-choice fields are gone: a claim's words never type the target."""
    both = _settle("DRUGX and DRUGY were compared; ABC1 levels fell.", outcome="benefit")
    worded = _settle(
        "ABC1 levels fell.", outcome="benefit", acts_on_target="lowers", target_level="down"
    )

    assert (both.perturbation, both.effect_source) == ("none", "none")
    assert (worded.perturbation, worded.effect_source) == ("none", "none")


def test_the_sentence_statement_types_the_claim():
    statement = _statement(
        exposure_kind="variant",
        exposure_text="p.Y74*",
        effect_on_target="decrease",
        effect_source="consequence",
        outcome_kind="disease",
        outcome_direction="lower",
    )
    claim = with_statement(_claim(sentence=2), statement)

    assert (claim.perturbation, claim.effect_source, claim.effect_basis) == (
        "decrease",
        "consequence",
        "p.Y74*",
    )
    assert claim.outcome == "benefit"
    assert claim.direction == Direction.INHIBIT


def test_a_statement_without_a_change_of_the_target_leaves_the_claim():
    """An observed level never sets a change, so the claim stays untyped."""
    statement = _statement(
        exposure_kind="observed_level", outcome_kind="disease", outcome_direction="higher"
    )
    claim = _claim(outcome="harm")

    assert with_statement(claim, statement) is claim
    assert with_statement(claim, None) is claim


def test_drug_data_wins_over_the_statement():
    claim = _claim(perturbation="decrease", effect_source="drug", outcome="benefit")
    statement = _statement(effect_on_target="increase", effect_source="text")

    assert with_statement(claim, statement) is claim


def test_a_biomarker_outcome_keeps_the_extractors_outcome():
    statement = _statement(
        effect_on_target="increase",
        effect_source="text",
        outcome_kind="biomarker",
        outcome_direction="lower",
    )

    assert statement_outcome(statement) == "none"
    assert with_statement(_claim(outcome="adverse"), statement).outcome == "adverse"


def test_a_drug_named_only_inside_another_word_is_not_the_drug():
    effect = _settle("PREDRUGXINE lowered risk.", outcome="benefit")

    assert effect.effect_source == "none"


def test_an_off_target_outcome_keeps_its_factors_but_has_no_direction():
    effect = _settle(
        "Off-target toxicity of DRUGX raised blood pressure.", outcome="adverse", off_target=True
    )

    assert effect.perturbation == "decrease"
    assert effect.off_target is True
    assert effect.direction == Direction.UNSPECIFIED
    assert effect_label(effect) == "off-target effect"


def test_the_targets_drugs_map_to_their_action():
    rows = [
        {"drug_name": "Drugx sodium", "action_type": "INHIBITOR"},
        {"drug_name": "DRUGY", "action_type": "AGONIST"},
        {"drug_name": "DRUGZ", "action_type": "BINDING AGENT"},
    ]

    assert target_drug_effects(rows) == {"DRUGX": "decrease", "DRUGY": "increase"}


def test_the_label_reads_the_effect():
    effect = _settle("DRUGX lowered disease risk.", outcome="benefit")

    assert effect_label(effect) == "less target activity → disease better"
    assert effect_label(_settle("ABC1 was measured.")) == ""
