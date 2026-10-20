# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Direction-of-effect reading (services/evidence/direction_of_effect.py)."""

from __future__ import annotations

from types import SimpleNamespace

from schemas.evidence import Direction
from services.evidence.direction_of_effect import (
    DirectionObservation,
    about_other_genes,
    assess_direction,
    build_direction_reading,
    from_claims,
    from_label_warnings,
    names_condition,
)
from services.evidence.drug_precedent import DrugInIndication, PrecedentReading

# A claim's typed effect for a therapeutic direction it supports: lowering the target
# helps (inhibit) or raising it helps (activate).
_EFFECT = {"inhibit": "decrease", "degrade": "decrease", "activate": "increase"}


def _claim(direction: str, text: str = "a claim") -> SimpleNamespace:
    return SimpleNamespace(
        direction=Direction(direction),
        perturbation=_EFFECT.get(direction, "none"),
        outcome="benefit" if direction in _EFFECT else "none",
        off_target=False,
        claim_text=text,
        evidence_id="c1",
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
        genetics_claims=[
            _claim("inhibit", "Carriers of a protective variant had less disease one."),
            _claim("inhibit", "Loss-of-function carriers had lower risk of disease one."),
            _claim("unspecified", "The gene is associated with disease one."),
        ],
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


def _about(direction: str, n: int, disease: str = "disease three") -> list[SimpleNamespace]:
    return [_claim(direction, f"Variant carriers had altered risk of {disease}.") for _ in range(n)]


def test_literature_alone_is_capped_near_a_decision():
    reading = build_direction_reading(
        "activate", disease="disease three", genetics_claims=_about("inhibit", 10)
    )
    assert 0.5 < reading.against <= 0.6
    assert reading.verdict == "contradicts"
    assert reading.restricted_to(["mendelian"]).verdict == "unknown"


def test_mis_tagged_literature_on_both_sides_nets_out():
    """Pooled per side, three mis-tagged claims against two read "conflicting"; a weak
    net vote says nothing either way."""
    claims = _about("inhibit", 3) + _about("activate", 2)
    reading = build_direction_reading("inhibit", disease="disease three", genetics_claims=claims)
    assert reading.verdict == "unknown"
    assert reading.against == 0


def test_a_clear_literature_majority_supports_without_overruling():
    claims = _about("inhibit", 10) + _about("activate", 2)
    reading = build_direction_reading("inhibit", disease="disease three", genetics_claims=claims)
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
        "inhibit", disease="disease three", genetics_claims=_about("inhibit", 1)
    )
    assert reading.verdict == "unknown"


def test_claims_are_scoped_to_the_disease():
    """Direction is disease-specific: a claim about another disease is not evidence, a
    claim naming a parent condition counts at reduced weight."""
    claims = [
        _claim("activate", "The variant is a risk factor for disease four."),
        _claim("inhibit", "Carriers had lower risk of parent condition."),
        _claim("inhibit", "Carriers had less broadcategory."),
        _claim("inhibit", "Carriers had lower risk of disease three."),
    ]
    obs = from_claims(
        claims,
        disease_names=["disease three"],
        parent_names=["parent condition", "broadcategory"],
    )
    assert [(o.perturbation, o.in_scope) for o in obs] == [("decrease", False), ("decrease", True)]
    assert obs[0].weight < obs[1].weight


def test_claims_about_disease_models_are_skipped():
    claims = [
        _claim(
            "inhibit", "Blocking the target reduced lesions in an animal model of disease three."
        ),
        _claim("inhibit", "Toxin-induced disease three improved after blockade."),
    ]
    assert not from_claims(claims, disease_names=["disease three"])


def test_claims_naming_only_other_genes_are_skipped():
    claims = [
        _claim("activate", "Variants in OTHER3 and ANOTHER1 raise the risk of disease three."),
        _claim("inhibit", "AB-12C loss-of-function carriers had less disease three."),
        _claim("inhibit", "The receptor's protective allele lowered risk of disease three."),
    ]
    obs = from_claims(claims, gene="AB12C", disease_names=["disease three"])
    assert [o.detail[:6] for o in obs] == ["AB-12C", "The re"]


def test_gene_symbols_are_told_apart_from_protein_changes():
    assert about_other_genes("OTHER3 variants raise risk.", ["AB12C"])
    assert not about_other_genes("The R46L variant lowers risk.", ["AB12C"])
    assert not about_other_genes("AB12C and OTHER3 both raise risk.", ["AB12C"])
    assert about_other_genes("AB12CR1 signalling drives the disease.", ["AB12C"])


def test_condition_abbreviations_match_in_their_exact_case():
    assert names_condition("Risk of DT rose.", "DT")
    assert not names_condition("Half-life was 300 dt.", "DT")
    assert names_condition("Risk of Disease Three rose.", "disease three")


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


# ------------------------------------------------------------ claims scoped by DiseaseScope


def _scoped_claim(text: str, direction: str = "inhibit", source: str = ""):
    from types import SimpleNamespace

    return SimpleNamespace(
        claim_text=text,
        direction=SimpleNamespace(value=direction),
        perturbation=_EFFECT.get(direction, "none"),
        outcome="benefit" if direction in _EFFECT else "none",
        off_target=False,
        evidence_id="c-" + text[:8],
        source_evidence_id=source,
    )


def test_scoped_claims_count_written_variants_and_related_ones_at_reduced_weight():
    """One in-scope claim of about nineteen was counted when papers hyphenated the
    disease differently, used its head noun or defined an abbreviation."""
    from services.evidence.direction_of_effect import from_claims
    from services.evidence.disease_scope import build_disease_scope

    scope = build_disease_scope(
        "non-alcoholic glomerulitis",
        names=["non-alcoholic glomerulitis"],
        parent_names=["glomerular disorder"],
        endpoints=(),
    )
    claims = [
        _scoped_claim("GENE-A loss lowered nonalcoholic glomerulitis risk"),
        _scoped_claim("GENE-A loss lowered glomerulitis risk"),
        _scoped_claim("GENE-A loss lowered NAGN risk", source="p1"),
        _scoped_claim("GENE-A loss lowered risk of disease z"),
    ]
    sources = {"p1": "Non-alcoholic glomerulitis (NAGN) is common."}

    obs = from_claims(claims, gene="GENE-A", scope=scope, source_texts=sources)

    assert [o.in_scope for o in obs] == [True, False, True]
    assert obs[0].weight > obs[1].weight


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
