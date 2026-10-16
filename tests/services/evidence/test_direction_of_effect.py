# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Direction-of-effect reading (services/evidence/direction_of_effect.py)."""

from __future__ import annotations

from types import SimpleNamespace

from schemas.evidence import Direction
from services.evidence.direction_of_effect import (
    DirectionObservation,
    assess_direction,
    build_direction_reading,
    from_label_warnings,
)
from services.evidence.drug_precedent import DrugInIndication, PrecedentReading


def _claim(direction: str, text: str = "a claim") -> SimpleNamespace:
    return SimpleNamespace(direction=Direction(direction), claim_text=text, evidence_id="c1")


def _gof(confidence: float = 0.84) -> dict:
    return {"mechanism": "gof", "direction": "inhibit", "confidence": confidence}


def test_a_harm_favours_the_opposite_perturbation():
    o = DirectionObservation(
        perturbation="increase",
        outcome="harm",
        evidence="mendelian",
        weight=0.5,
        source="s",
        detail="d",
    )
    assert o.favours == "decrease"


def test_disease_causing_gain_of_function_contradicts_activation():
    """Gain-of-function variants of the gene cause a related disorder sharing the
    disease's biology, and carriers of protective variants have lower risk."""
    reading = build_direction_reading(
        "activate",
        disease="disease one",
        mechanism=_gof(),
        mechanism_in_scope=False,
        mechanism_disorder="related disorder",
        genetics_claims=[_claim("inhibit"), _claim("inhibit"), _claim("unspecified")],
    )
    assert reading.verdict == "contradicts"
    assert reading.support == 0
    assert reading.against > 0.5
    assert "[related disorder]" in reading.render()


def test_recessive_loss_of_function_and_an_approved_activator_support_activation():
    precedent = PrecedentReading(
        direction="activate",
        requested="increase",
        status="validated",
        concordant=[
            DrugInIndication(
                drug="DRUG-P", action_type="POTENTIATOR", effect="increase", stage=4, approved=True
            )
        ],
    )
    reading = build_direction_reading(
        "activate",
        disease="disease two",
        precedent=precedent,
        mechanism={"mechanism": "lof", "confidence": 0.75},
        mechanism_in_scope=True,
    )
    assert reading.verdict == "supports"
    assert {o.evidence for o in reading.supporting} == {"approved_drug", "mendelian"}


_LABEL = {
    "drug_name": "DRUG-A",
    "target_drug": True,
    "target_action_type": "ANTAGONIST",
    "warnings": (
        "5.1 Infections: serious infections were reported. 5.3 Inflammatory Bowel Disease: "
        "Cases of inflammatory bowel disease, in some cases serious, have been reported. "
        "Exacerbations of disease two occurred in clinical trials."
    ),
}


def test_a_label_warning_that_the_drug_worsens_the_disease_contradicts_its_direction():
    reading = build_direction_reading(
        "inhibit",
        disease="disease two",
        labels=[_LABEL],
        parent_names=["inflammatory bowel disease"],
    )
    [o] = reading.observations
    assert (o.perturbation, o.outcome, o.evidence, o.in_scope) == (
        "decrease",
        "harm",
        "label_warning",
        True,
    )
    assert reading.verdict == "contradicts"


def test_a_warning_naming_only_the_parent_condition_counts_at_reduced_weight():
    label = {**_LABEL, "warnings": "Cases of inflammatory bowel disease have been reported."}
    [o] = from_label_warnings(
        [label], disease_names=["disease two"], parent_names=["inflammatory bowel disease"]
    )
    assert not o.in_scope and o.weight < 0.7


def test_naming_the_disease_without_harm_wording_is_not_a_warning():
    """A label naming the indication (a trial population, a risk-reduction setting) does
    not say the drug worsens it."""
    label = {
        **_LABEL,
        "warnings": "In women at high risk for disease two, uterine events occurred.",
    }
    assert not from_label_warnings([label], disease_names=["disease two"])


def test_labels_of_drugs_not_acting_on_the_target_are_ignored():
    label = {**_LABEL, "target_drug": False}
    assert not from_label_warnings([label], disease_names=["disease two"])


def test_literature_alone_is_capped_below_a_decision():
    claims = [_claim("inhibit") for _ in range(10)]
    reading = build_direction_reading("activate", disease="d", genetics_claims=claims)
    assert reading.against == 0.6
    assert reading.verdict == "contradicts"
    assert reading.restricted_to(["mendelian"]).verdict == "unknown"


def test_evidence_both_ways_is_conflicting_not_forced():
    claims = [_claim("inhibit")] * 3 + [_claim("activate")] * 2
    reading = build_direction_reading("inhibit", disease="d", genetics_claims=claims)
    assert reading.verdict == "conflicting"


def test_weak_evidence_is_unknown():
    reading = build_direction_reading("inhibit", disease="d", genetics_claims=[_claim("inhibit")])
    assert reading.verdict == "unknown"


def test_a_direction_without_an_effect_class_is_unknown():
    reading = assess_direction("unknown", [])
    assert reading.verdict == "unknown" and reading.render() == ""
