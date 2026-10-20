# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A claim's typed effect and its derived direction (services/evidence/claim_effect.py).

The failure these prevent: the extractor chose a therapeutic direction per claim, a sign
product it got wrong both ways ("carriers of the AA genotype had lower risk" tagged
activate with no change in the target stated; an inhibitor's harm tagged as if the
target were raised). It now reports how the target changed, the words that say so, and
the outcome; code checks the words and derives the direction.
"""

from __future__ import annotations

import pytest

from schemas.evidence import Direction
from services.evidence.claim_effect import (
    basis_perturbation,
    effect_label,
    settle_effect,
    target_drug_effects,
    therapeutic_direction,
)

_TARGET = ["ABC1"]


def _settle(text: str, perturbation: str, basis: str, outcome: str, **kw):
    raw = {"perturbation": perturbation, "effect_basis": basis, "outcome": outcome, **kw}
    return settle_effect(raw, text, target_symbols=_TARGET, target_drugs={"DRUGX": "decrease"})


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


def test_a_basis_not_quoted_from_the_claim_gives_no_perturbation():
    effect = _settle(
        "The AA genotype was associated with lower disease risk.",
        "decrease",
        "loss-of-function allele",
        "benefit",
    )

    assert effect.perturbation == "none"
    assert effect.direction == Direction.UNSPECIFIED


def test_a_variant_named_without_its_effect_gives_no_perturbation():
    """The model may guess a change for an allele letter; no cue in the words, no change."""
    effect = _settle(
        "Carriers of the AA genotype of ABC1 had lower disease risk.",
        "increase",
        "AA genotype of ABC1",
        "benefit",
    )

    assert effect.perturbation == "none"


def test_a_cue_of_one_polarity_overrides_the_model():
    effect = _settle(
        "Loss-of-function carriers of ABC1 had lower disease risk.",
        "increase",
        "Loss-of-function carriers",
        "benefit",
    )

    assert effect.perturbation == "decrease"
    assert effect.direction == Direction.INHIBIT


def test_a_level_counts_only_for_the_target_itself():
    target_level = _settle(
        "The B allele is associated with lower concentrations of ABC1 and lower disease risk.",
        "decrease",
        "lower concentrations of ABC1",
        "benefit",
    )
    marker_level = _settle(
        "The B allele is associated with higher marker-C levels and lower disease risk.",
        "increase",
        "higher marker-C levels",
        "benefit",
    )

    assert target_level.perturbation == "decrease"
    assert marker_level.perturbation == "none"


def test_a_drug_of_the_target_takes_its_action_and_an_adverse_effect_has_no_direction():
    harm = _settle("DRUGX increased disease events.", "increase", "DRUGX", "harm")
    adverse = _settle("DRUGX caused hypertension.", "decrease", "DRUGX", "adverse")

    assert harm.perturbation == "decrease"
    assert harm.direction == Direction.ACTIVATE  # lowering the target harmed
    assert adverse.direction == Direction.UNSPECIFIED


def test_a_change_in_another_gene_is_not_the_targets():
    effect = _settle(
        "XYZ2 loss-of-function variants raise disease risk.",
        "decrease",
        "XYZ2 loss-of-function variants",
        "harm",
    )

    assert effect.perturbation == "none"


def test_an_off_target_outcome_keeps_its_factors_but_has_no_direction():
    effect = _settle(
        "Off-target toxicity of the ABC1 inhibitor DRUGX raised blood pressure.",
        "decrease",
        "the ABC1 inhibitor DRUGX",
        "adverse",
        off_target=True,
    )

    assert effect.perturbation == "decrease"
    assert effect.off_target is True
    assert effect.direction == Direction.UNSPECIFIED
    assert effect_label(effect) == "off-target effect"


def test_cues_of_both_polarities_keep_the_models_choice_only_if_it_is_one_of_them():
    text = "Gain-of-function and knockout alleles of ABC1 were both studied."
    basis = "Gain-of-function and knockout alleles"

    assert basis_perturbation(basis, target_symbols=_TARGET) == {"decrease", "increase"}
    assert _settle(text, "decrease", basis, "none").perturbation == "decrease"
    assert _settle(text, "none", basis, "none").perturbation == "none"


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
        "decrease",
        "Loss-of-function carriers",
        "benefit",
    )

    assert effect_label(effect) == "less target activity → disease better"
    assert effect_label(_settle("ABC1 was measured.", "none", "", "none")) == ""
