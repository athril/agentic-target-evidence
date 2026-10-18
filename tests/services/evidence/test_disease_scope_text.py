# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Disease scope for text (services/evidence/disease_scope.py: DiseaseScope).

One test per naming rule, each with a negative case, on synthetic disease names. The
failure they prevent: a disease written with another hyphenation, spelling, word order,
abbreviation or subtype name was matched as a different disease, and most of the
relevant literature, labels and outcome trials were dropped from the readings.
"""

from __future__ import annotations

from services.evidence.disease_scope import (
    Endpoint,
    build_disease_scope,
    load_endpoints,
    name_forms,
    scope_from_ontology,
)


def _scope(disease: str = "non-alcoholic glomerulitis", **kw):
    return build_disease_scope(disease, names=kw.pop("names", [disease]), endpoints=(), **kw)


def test_the_three_hyphenation_forms_of_a_name_all_match() -> None:
    scope = _scope()

    assert scope.match("Patients with nonalcoholic glomerulitis improved") == "in_scope"
    assert scope.match("non alcoholic glomerulitis cohort") == "in_scope"
    assert scope.match("Non-Alcoholic Glomerulitis") == "in_scope"
    assert scope.match("alcoholic glomerulitis") != "in_scope"


def test_a_joined_prefix_in_the_name_matches_a_hyphenated_text() -> None:
    scope = _scope("nonalcoholic glomerulitis")

    assert scope.match("in non-alcoholic glomerulitis") == "in_scope"


def test_british_spelling_inverted_headings_and_plurals_match() -> None:
    scope = _scope("acute myeloid leukemia")

    assert scope.match("acute myeloid leukaemia relapse") == "in_scope"
    assert scope.match("Leukemia, Myeloid, Acute") == "in_scope"
    assert scope.match("acute myeloid leukemias") == "in_scope"
    # A sibling subtype shares the specific head noun: related, never in scope.
    assert scope.match("Leukemia, Lymphoid, Acute") == "related"


def test_disease_and_disorder_are_interchangeable_and_possessives_fold() -> None:
    scope = _scope("Smith disease")

    assert scope.match("Smith's disorder") == "in_scope"
    assert scope.match("Smith's disease") == "in_scope"
    assert scope.match("Smithson disease") == "none"


def test_a_specific_head_noun_alone_is_related_and_a_generic_one_is_nothing() -> None:
    specific = _scope()
    generic = _scope("lobular breast cancer")

    assert specific.match("glomerulitis progressed") == "related"
    assert generic.match("cancer survivors") == "none"


def test_ontology_abbreviations_match_in_exact_case_and_short_ones_need_a_definition() -> None:
    scope = _scope(names=["non-alcoholic glomerulitis", "NAGL", "NG"])

    assert scope.match("NAGL patients") == "in_scope"
    assert scope.match("nagl patients") == "none"
    assert scope.match("NG patients") == "none"  # two letters: too ambiguous undefined
    source = "We studied non-alcoholic glomerulitis (NG) in two cohorts."
    assert scope.match("NG patients improved", source_text=source) == "in_scope"


def test_an_abbreviation_the_paper_defines_for_the_disease_counts() -> None:
    scope = _scope()
    source = "Background: non-alcoholic glomerulitis (NAGN) has no therapy."

    assert scope.match("NAGN risk was lower in carriers", source_text=source) == "in_scope"
    assert scope.match("NAGN risk was lower in carriers") == "none"
    other = "Background: alcoholic nephritis (NAGN) is common."
    assert scope.match("NAGN risk was lower in carriers", source_text=other) == "none"


def test_subtypes_are_in_scope_parents_related_and_ancestors_neither() -> None:
    scope = _scope(
        descendant_names=["early-onset non-alcoholic glomerulitis"],
        narrow_names=["juvenile glomerulitis"],
        parent_names=["glomerular disorder"],
        ancestor_names=["kidney disorder"],
    )

    assert scope.match("early onset non alcoholic glomerulitis") == "in_scope"
    assert scope.match("juvenile glomerulitis") == "in_scope"
    assert scope.match("a glomerular disorder registry") == "related"
    assert scope.match("kidney disorder") == "none"


def test_an_endpoint_counts_the_disease_when_it_or_an_ancestor_is_among_its_counts() -> None:
    vocabulary = (
        Endpoint(name="organ failure", patterns=[r"\borgan failure\b"], counts=["kidney disorder"]),
        Endpoint(name="fractures", patterns=[r"\bfractures?\b"], counts=["bone disorder"]),
    )
    scope = build_disease_scope(
        "non-alcoholic glomerulitis",
        names=["non-alcoholic glomerulitis"],
        ancestor_names=["kidney disorder"],
        endpoints=vocabulary,
    )

    assert scope.outcome_counts("Time to organ failure or death")
    assert scope.outcome_counts("Remission of non-alcoholic glomerulitis")
    assert not scope.outcome_counts("New vertebral fractures")


def test_a_counted_term_matches_the_last_words_of_a_name() -> None:
    vocabulary = (Endpoint(name="x", patterns=[r"\bdialysis\b"], counts=["glomerulitis"]),)
    scope = build_disease_scope(
        "non-alcoholic glomerulitis", names=["non-alcoholic glomerulitis"], endpoints=vocabulary
    )

    assert scope.outcome_counts("Start of dialysis")


def test_without_an_ontology_the_scope_is_the_disease_string_marked_partial() -> None:
    scope = scope_from_ontology("disease a", None)

    assert scope.partial
    assert scope.match("patients with disease a") == "in_scope"
    assert scope.match("patients with disease b") == "none"


def test_the_endpoint_vocabulary_is_well_formed_and_sourced() -> None:
    endpoints = load_endpoints()

    assert endpoints
    assert all(e.source and e.patterns and e.counts for e in endpoints)


def test_a_coronary_outcome_counts_for_coronary_artery_disease() -> None:
    """An outcome trial counting "major coronary events" names no disease; the standard
    definition links it to coronary artery disease."""
    scope = build_disease_scope(
        "coronary artery disease",
        names=["coronary artery disorder", "coronary artery disease"],
        ancestor_names=["heart disorder", "arterial disorder"],
    )

    assert scope.outcome_counts("Number of Participants With Major Coronary Event")
    assert not scope.outcome_counts("Change in LDL cholesterol")


def test_name_forms_cover_written_variants() -> None:
    forms = name_forms("Non-Alcoholic Glomerulitis")

    assert {"non alcoholic glomerulitis", "nonalcoholic glomerulitis"} <= forms


def test_a_parents_abbreviation_makes_a_text_related_and_inverted_headings_give_no_head() -> None:
    scope = build_disease_scope(
        "non-alcoholic glomerulitis",
        names=["non-alcoholic glomerulitis", "Glomerulitis, Coronary"],
        parent_names=["glomerular disorder", "GLOMD"],
        endpoints=(),
    )

    assert scope.match("GLOMD cohort") == "related"
    assert scope.match("coronary events") == "none"  # not a head noun of "Coronary Glomerulitis"


def test_a_short_ontology_abbreviation_counts_in_a_document_naming_the_disease() -> None:
    scope = _scope(names=["non-alcoholic glomerulitis", "NG"])

    assert scope.match("NG risk fell") == "none"
    source = "Non-alcoholic glomerulitis cohort study."
    assert scope.match("NG risk fell", source_text=source) == "in_scope"
