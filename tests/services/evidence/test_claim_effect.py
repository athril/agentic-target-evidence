# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A claim's typed effect and its derived direction (services/evidence/claim_effect.py).

The failure these prevent: the extractor chose a therapeutic direction per claim, a sign
product it got wrong both ways. The target's change now comes from the target's drug data
when the claim names its drugs, and otherwise from two fixed-choice model fields that
must agree; code derives the direction.
"""

from __future__ import annotations

import pytest

from schemas.evidence import Direction
from services.evidence.claim_effect import (
    acts_on_target,
    effect_label,
    settle_effect,
    target_drug_effects,
    therapeutic_direction,
)

_DRUGS = {"DRUGX": "decrease", "DRUGY": "increase"}


def _settle(text: str, acts: str = "none", level: str = "none", outcome: str = "none", **kw):
    raw = {"acts_on_target": acts, "target_level": level, "outcome": outcome, **kw}
    return settle_effect(raw, text, target_drugs=_DRUGS)


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


def test_a_known_drug_of_the_target_wins_over_the_fields():
    effect = _settle("DRUGX increased disease events.", acts="raises", outcome="harm")

    assert (effect.perturbation, effect.effect_source) == ("decrease", "drug")
    assert effect.effect_basis == "DRUGX"
    assert effect.direction == Direction.ACTIVATE  # lowering the target harmed


def test_drugs_of_both_polarities_fall_through_to_the_fields():
    text = "DRUGX and DRUGY were compared; ABC1 levels fell."
    effect = _settle(text, level="down", span="ABC1 levels fell")
    neither = _settle(text)

    assert (effect.perturbation, effect.effect_source) == ("decrease", "claim")
    assert effect.effect_basis == "ABC1 levels fell"
    assert (neither.perturbation, neither.effect_source) == ("none", "none")


@pytest.mark.parametrize(
    ("acts", "level", "perturbation"),
    [
        ("lowers", "none", "decrease"),
        ("none", "up", "increase"),
        ("lowers", "down", "decrease"),
        ("raises", "up", "increase"),
        ("lowers", "up", "none"),  # the two answers disagree
        ("raises", "down", "none"),
        ("none", "none", "none"),
        ("sideways", "none", "none"),  # not one of the choices
    ],
)
def test_the_two_fields_must_agree(acts, level, perturbation):
    effect = _settle("The anti-ABC1 antibody was given.", acts=acts, level=level, span="x")

    assert effect.perturbation == perturbation
    assert effect.effect_source == ("claim" if perturbation != "none" else "none")
    assert effect.effect_basis == ("x" if perturbation != "none" else "")


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


def test_acts_on_target_reads_the_raw_field():
    assert acts_on_target({"acts_on_target": "Lowers"})
    assert not acts_on_target({"acts_on_target": "none"})
    assert not acts_on_target({})


def test_the_targets_drugs_map_to_their_action():
    rows = [
        {"drug_name": "Drugx sodium", "action_type": "INHIBITOR"},
        {"drug_name": "DRUGY", "action_type": "AGONIST"},
        {"drug_name": "DRUGZ", "action_type": "BINDING AGENT"},
    ]

    assert target_drug_effects(rows) == {"DRUGX": "decrease", "DRUGY": "increase"}


def test_the_label_reads_the_effect():
    effect = _settle(
        "Loss-of-function carriers of ABC1 had lower disease risk.",
        acts="lowers",
        outcome="benefit",
        span="Loss-of-function carriers",
    )

    assert effect_label(effect) == "less target activity → disease better"
    assert effect_label(_settle("ABC1 was measured.")) == ""
