# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Direction-of-effect reading (services/evidence/direction_of_effect.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from schemas.statement import TargetStatement
from services.evidence.direction_of_effect import (
    DirectionObservation,
    assess_direction,
    build_direction_reading,
    from_label_warnings,
    from_statements,
)
from services.evidence.drug_precedent import DrugInIndication, PrecedentReading

# A statement for a therapeutic direction it supports: carriers whose change lowers the
# target had less disease (inhibit), or carriers whose change raises it had less (activate).
_EFFECT = {"inhibit": "decrease", "degrade": "decrease", "activate": "increase"}
_PAPERS = iter(range(1, 10_000))


def _st(
    direction: str,
    text: str = "a sentence",
    *,
    in_scope: bool = True,
    design: str = "genetic_association",
    effect_source: str = "qtl",
    variant: str = "rs1",
    source: str = "",
) -> TargetStatement:
    return TargetStatement(
        source=source or f"PMID:{next(_PAPERS)}",
        sentence=1,
        text=text,
        exposure_kind="variant",
        exposure_id=variant,
        exposure_text=variant,
        effect_on_target=_EFFECT.get(direction, "none"),
        effect_source=effect_source if direction in _EFFECT else "none",
        outcome_kind="disease",
        outcome_text="the disease",
        outcome_direction="lower" if direction in _EFFECT else "none",
        in_scope=in_scope,
        design=design,
    )


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
        statements=[
            _st("inhibit", effect_source="curated"),
            _st("inhibit", effect_source="consequence"),
            _st("unspecified"),
        ],
    )
    assert reading.verdict == "contradicts"
    assert reading.support == 0
    # A related disorder's mechanism and two weak-to-moderate designs: a clear reading,
    # below what one curated in-scope mechanism would give.
    assert 0.3 < reading.against < 0.8
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


def _about(direction: str, n: int) -> list[TargetStatement]:
    return [_st(direction) for _ in range(n)]


def _causal(direction: str, n: int) -> list[TargetStatement]:
    return [_st(direction, design="mendelian_randomisation") for _ in range(n)]


def test_literature_alone_is_capped_near_a_decision():
    reading = build_direction_reading(
        "activate", disease="disease three", statements=_causal("inhibit", 10)
    )
    assert 0.5 < reading.against <= 0.6
    assert reading.verdict == "contradicts"
    assert reading.restricted_to(["mendelian"]).verdict == "unknown"


def test_mis_tagged_literature_on_both_sides_nets_out():
    """Pooled per side, three statements against two read "conflicting"; a weak
    net vote says nothing either way."""
    claims = _about("inhibit", 3) + _about("activate", 2)
    reading = build_direction_reading("inhibit", disease="disease three", statements=claims)
    assert reading.verdict == "unknown"
    assert reading.against == 0


def test_a_study_designed_for_direction_outweighs_candidate_associations():
    """Ten candidate-polymorphism associations count for less than ten Mendelian
    randomisation results, and their observations name the design weighed."""
    association = build_direction_reading(
        "activate", disease="disease three", statements=_about("inhibit", 10)
    )
    causal = build_direction_reading(
        "activate", disease="disease three", statements=_causal("inhibit", 10)
    )

    assert 0 < association.against < causal.against
    assert {o.design for o in association.observations} == {"association"}
    assert {o.design for o in causal.observations} == {"causal"}
    assert "causal design" in causal.render()


def test_a_clear_literature_majority_supports_without_overruling():
    claims = _about("inhibit", 10) + _about("activate", 2)
    reading = build_direction_reading("inhibit", disease="disease three", statements=claims)
    assert reading.verdict == "supports"
    assert 0.3 <= reading.support < 0.6 and reading.against == 0


def test_evidence_kinds_that_disagree_are_conflicting_not_forced():
    precedent = PrecedentReading(
        direction="inhibit",
        requested="decrease",
        status="validated",
        concordant=[
            DrugInIndication(
                drug="DRUG-I", action_type="INHIBITOR", effect="decrease", stage=4, approved=True
            )
        ],
    )
    reading = build_direction_reading(
        "inhibit", disease="disease two", precedent=precedent, labels=[_LABEL]
    )
    assert reading.verdict == "conflicting"


def test_weak_evidence_is_unknown():
    reading = build_direction_reading(
        "inhibit", disease="disease three", statements=_about("inhibit", 1)
    )
    assert reading.verdict == "unknown"


def test_statements_count_only_in_scope_disease_outcomes():
    """Direction is disease-specific: a statement whose disease is outside the run's scope
    (by MeSH id) is not evidence, nor is a biomarker outcome or a change not known."""
    biomarker = _st("inhibit").model_copy(update={"outcome_kind": "biomarker"})
    unknown = _st("inhibit").model_copy(update={"effect_on_target": "none"})
    obs = from_statements([_st("inhibit", in_scope=False), biomarker, unknown, _st("inhibit")])

    assert [(o.perturbation, o.outcome, o.in_scope) for o in obs] == [("decrease", "benefit", True)]


def test_a_variant_counts_once_per_paper():
    obs = from_statements(
        [_st("inhibit", source="PMID:9"), _st("inhibit", source="PMID:9"), _st("inhibit")]
    )
    assert len(obs) == 2


def test_more_disease_with_more_target_favours_lowering_it():
    raised = _st("activate").model_copy(update={"outcome_direction": "higher"})
    (o,) = from_statements([raised])

    assert (o.perturbation, o.outcome, o.favours) == ("increase", "harm", "decrease")


def test_a_cancer_indications_variants_give_no_germline_reading():
    assert from_statements([_st("inhibit")], somatic_indication=True) == []


def test_a_model_species_is_not_human_genetics():
    mouse = _st("inhibit").model_copy(update={"species": "10090"})
    assert from_statements([mouse]) == []


@pytest.mark.parametrize(
    ("design", "effect_source", "weighed"),
    [
        ("mendelian_randomisation", "qtl", "causal"),
        ("genetic_association", "consequence", "functional"),
        ("genetic_association", "curated", "functional"),
        ("genetic_association", "qtl", "association"),
    ],
)
def test_the_design_weighed_follows_the_statement(design, effect_source, weighed):
    (o,) = from_statements([_st("inhibit", design=design, effect_source=effect_source)])
    assert o.design == weighed


def test_a_direction_without_an_effect_class_is_unknown():
    reading = assess_direction("unknown", [])
    assert reading.verdict == "unknown" and reading.render() == ""


def _warning(text: str) -> dict:
    return {**_LABEL, "warnings": text}


def test_the_indication_as_a_trial_population_is_not_a_warning():
    """A harm reported *in a subject with* the disease is not the disease worsening."""
    label = _warning(
        "5.4 Hepatotoxicity: a serious adverse reaction of drug-induced liver injury was "
        "reported in a clinical trial subject with disease two."
    )
    assert not from_label_warnings([label], disease_names=["disease two"])


def test_a_warning_may_follow_the_condition_it_names():
    [o] = from_label_warnings(
        [_warning("(5.4) Disease Two: exacerbation or new onset may occur.")],
        disease_names=["disease two"],
    )
    assert o.in_scope


def test_a_listed_condition_governed_by_the_cue_is_a_warning():
    label = _warning("Cases of other one, disease two and other two have been reported.")
    assert from_label_warnings([label], disease_names=["disease two"])


def test_a_negated_report_is_not_a_warning():
    label = _warning("No cases of disease two were reported in clinical trials.")
    assert not from_label_warnings([label], disease_names=["disease two"])


def test_a_parent_condition_matches_by_its_head_without_the_site():
    """A parent term "<kind> disease of <site>" is how ontologies name it; labels write
    "<kind> disease" or "<kind> disorders"."""
    label = _warning("Kind Disorders: exacerbation or new onset may occur.")
    [o] = from_label_warnings(
        [label],
        disease_names=["disease two"],
        parent_names=["kind disease of central nervous system"],
    )
    assert not o.in_scope and o.weight < 0.7


def test_a_one_word_parent_or_a_like_condition_is_not_a_warning():
    label = _warning("Cases of severe eruptions, including kindtitis-like eruptions, occurred.")
    assert not from_label_warnings(
        [label], disease_names=["disease two"], parent_names=["kindtitis"]
    )
    label = _warning("Cases of other kindtitis-like eruptions occurred.")
    assert not from_label_warnings(
        [label], disease_names=["disease two"], parent_names=["other kindtitis"]
    )


def test_a_related_disorders_mechanism_corroborates_but_never_decides_alone():
    alone = build_direction_reading(
        "activate", disease="disease one", mechanism=_gof(0.85), mechanism_in_scope=False
    )
    assert alone.verdict == "unknown" and 0 < alone.against < 0.3
    in_scope = build_direction_reading(
        "activate", disease="disease one", mechanism=_gof(0.85), mechanism_in_scope=True
    )
    assert in_scope.verdict == "contradicts"


def test_a_germline_disorders_mechanism_says_nothing_about_a_tumour():
    reading = build_direction_reading(
        "inhibit",
        disease="tumour one",
        mechanism={"mechanism": "lof", "confidence": 0.8},
        mechanism_in_scope=False,
        somatic_indication=True,
    )
    assert reading.observations == []


def test_a_curated_mechanism_becomes_the_genetics_prompts_in_scope_mechanism():
    from capabilities.target_validation.workflow import _curated_mechanism
    from services.evidence.direction_of_effect import build_direction_reading

    reading = build_direction_reading(
        "activate",
        disease="disease a",
        structured_rows=[
            SimpleNamespace(
                datasource="orphanet",
                disease_name="disease a",
                direction_on_target="LoF",
                direction_on_trait="risk",
            )
        ],
    )

    mechanism = _curated_mechanism(reading)

    assert mechanism is not None
    assert (mechanism["mechanism"], mechanism["direction"]) == ("lof", "activate")
    assert _curated_mechanism(build_direction_reading("activate", disease="disease a")) is None
