# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Statement assembly (services/evidence/statements.py): the model selects mentions and
code assembles the statement, so a biomarker level or another gene's knockout cannot be
read as a change of the target."""

from __future__ import annotations

from services.evidence.grounding import GroundedSentence, Mention
from services.evidence.statements import (
    Selection,
    assemble,
    design_from_metadata,
    parse,
    render,
)

_TARGET = Mention(number=1, kind="gene", text="ABC1", is_target=True)
_OTHER = Mention(number=2, kind="gene", text="XYZ2")
_TRUNCATING = Mention(
    number=3, kind="variant", text="Y74*", is_target=True, hgvs="p.Y74*", allele="*"
)
_MISSENSE = Mention(
    number=4, kind="variant", text="R46L", is_target=True, rsid="rs1", hgvs="p.R46L", allele="L"
)
_DRUG = Mention(number=5, kind="chemical", text="drugx", drug="DRUGX")
_DISEASE = Mention(number=6, kind="disease", text="DISEASE-A", id="D1", in_scope=True)
_BIOMARKER = Mention(number=7, kind="chemical", text="cholesterol", id="D2")
_SPECIES = Mention(number=8, kind="species", text="mice", id="10090")
_SENTENCE = GroundedSentence(
    source="PMID:1",
    index=3,
    text="A sentence.",
    mentions=[_TARGET, _OTHER, _TRUNCATING, _MISSENSE, _DRUG, _DISEASE, _BIOMARKER, _SPECIES],
)


def _read(**kw) -> Selection:
    return Selection(id="S1", **kw)


def test_a_truncating_variant_lowers_the_target_by_its_consequence():
    st = assemble(
        _SENTENCE,
        _read(exposure=3, exposure_kind="variant", outcome=6, outcome_direction="lower"),
    )

    assert (st.exposure_kind, st.effect_on_target, st.effect_source) == (
        "variant",
        "decrease",
        "consequence",
    )
    assert (st.outcome_kind, st.outcome_direction, st.in_scope) == ("disease", "lower", True)


def test_a_missense_variant_has_no_effect_without_data():
    st = assemble(_SENTENCE, _read(exposure=4, exposure_kind="variant", outcome=6))

    assert (st.exposure_id, st.allele, st.effect_on_target) == ("rs1", "L", "none")


def test_a_drug_takes_its_action_from_drug_data():
    st = assemble(
        _SENTENCE,
        _read(exposure=5, exposure_kind="drug", outcome=6, outcome_direction="lower"),
        drug_effects={"DRUGX": "decrease"},
    )

    assert (st.effect_on_target, st.effect_source, st.exposure_id) == ("decrease", "drug", "DRUGX")


def test_an_observed_level_never_changes_the_target():
    st = assemble(
        _SENTENCE,
        _read(exposure=1, exposure_kind="observed_level", change="decrease", outcome=6),
    )

    assert st.exposure_kind == "observed_level"
    assert (st.effect_on_target, st.effect_source) == ("none", "none")


def test_another_genes_knockout_is_not_the_targets():
    st = assemble(
        _SENTENCE, _read(exposure=2, exposure_kind="genetic_perturbation", change="decrease")
    )

    assert (st.exposure_kind, st.effect_on_target) == ("none", "none")


def test_the_targets_knockout_takes_its_change_from_the_sentence():
    st = assemble(
        _SENTENCE,
        _read(
            exposure=1,
            exposure_kind="genetic_perturbation",
            change="decrease",
            outcome=6,
            outcome_direction="higher",
        ),
    )

    assert (st.effect_on_target, st.effect_source, st.species) == ("decrease", "text", "10090")


def test_a_kind_that_does_not_fit_the_mention_is_none():
    as_drug = assemble(_SENTENCE, _read(exposure=1, exposure_kind="drug"))
    as_variant = assemble(_SENTENCE, _read(exposure=5, exposure_kind="variant"))

    assert as_drug.exposure_kind == as_variant.exposure_kind == "none"


def test_a_chemical_outcome_is_a_biomarker_and_a_gene_is_no_outcome():
    biomarker = assemble(_SENTENCE, _read(outcome=7, outcome_direction="higher"))
    gene = assemble(_SENTENCE, _read(outcome=2, outcome_direction="higher"))

    assert (biomarker.outcome_kind, biomarker.outcome_id) == ("biomarker", "D2")
    assert gene.outcome_kind == "none"


def test_the_design_comes_from_pubmed_terms_before_the_model():
    sel = _read(design="observational")

    assert assemble(_SENTENCE, sel, design="rct").design == "rct"
    assert assemble(_SENTENCE, sel).design == "observational"
    assert assemble(_SENTENCE, _read(design="anecdote")).design == "other"


def test_design_from_metadata_reads_nlm_terms_in_priority_order():
    assert (
        design_from_metadata(
            ["Journal Article"], [{"descriptor": "Mendelian Randomization Analysis"}]
        )
        == "mendelian_randomisation"
    )
    assert design_from_metadata(["Randomized Controlled Trial", "Meta-Analysis"], []) == (
        "meta_analysis"
    )
    assert design_from_metadata([], ["Animals", "Mice"]) == "preclinical"
    assert design_from_metadata([], ["Animals", "Humans", "Cohort Studies"]) == "observational"
    assert design_from_metadata(["Journal Article"], []) is None


def test_render_numbers_the_mentions_and_parse_maps_ids_back():
    text = render([_SENTENCE])

    assert text.startswith("S1: A sentence.")
    assert '[2] "XYZ2" — gene, another gene' in text
    assert '[4] "R46L" — variant of the target, rs1' in text
    parsed = parse('{"sentences": [{"id": "S1", "exposure": 1}, {"id": "S9"}]}', 1)
    assert parsed is not None and list(parsed) == [0] and parsed[0].exposure == 1
    assert parse("not json", 1) is None
