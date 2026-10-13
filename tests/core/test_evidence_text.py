# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for core.evidence_text.screenable_text."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from core.evidence_text import (
    is_on_target_fda_label,
    is_on_target_patent,
    is_target_drug_trial,
    label_indications,
    mentions_term,
    screenable_text,
)
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance


def _make_evidence(
    evidence_type: EvidenceType, extra: dict, source: str = "NCT05213624"
) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="TRPC6",
        disease="focal segmental glomerulosclerosis",
        evidence_type=evidence_type,
        scope="abstract",
        source=source,
        source_link="https://clinicaltrials.gov/study/NCT05213624",
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="test",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            trace_id="trace-test",
        ),
        extra=extra,
    )


class TestClinicalTrialScreenableText:
    def test_includes_eligibility_criteria_gene_mention(self):
        ev = _make_evidence(
            EvidenceType.CLINICAL_TRIAL,
            {
                "brief_summary": "A study of BI 764198 in patients with FSGS.",
                "interventions": ["BI 764198"],
                "participation_criteria": {
                    "eligibility_criteria": (
                        "Inclusion: documented (TRPC6) gene mutation causing FSGS."
                    ),
                },
            },
        )
        text = screenable_text(ev)
        assert "TRPC6" in text
        assert "BI 764198" in text

    def test_interventions_appear_before_brief_summary(self):
        ev = _make_evidence(
            EvidenceType.CLINICAL_TRIAL,
            {
                "interventions": ["apecotrep"],
                "brief_summary": "Summary text.",
            },
        )
        text = screenable_text(ev)
        assert text.index("apecotrep") < text.index("Summary text.")

    def test_conditions_included(self):
        ev = _make_evidence(
            EvidenceType.CLINICAL_TRIAL,
            {
                "conditions": ["Focal Segmental Glomerulosclerosis"],
                "brief_summary": "Study overview.",
            },
        )
        text = screenable_text(ev)
        assert "Focal Segmental Glomerulosclerosis" in text

    def test_missing_fields_tolerated(self):
        ev = _make_evidence(
            EvidenceType.CLINICAL_TRIAL,
            {"brief_summary": "Minimal trial record."},
        )
        text = screenable_text(ev)
        assert "Minimal trial record." in text

    def test_empty_extra_returns_empty_string(self):
        ev = _make_evidence(EvidenceType.CLINICAL_TRIAL, {})
        assert screenable_text(ev) == ""

    def test_design_details_included(self):
        ev = _make_evidence(
            EvidenceType.CLINICAL_TRIAL,
            {
                "brief_summary": "Phase 2 study.",
                "design_details": "Randomized, double-blind, parallel-group.",
            },
        )
        text = screenable_text(ev)
        assert "Randomized" in text


class TestNonClinicalTrialScreenableText:
    def test_article_uses_abstract(self):
        ev = _make_evidence(
            EvidenceType.ARTICLE,
            {
                "abstract": "This study shows TRPC6 is relevant.",
                "brief_summary": "should not use this",
            },
        )
        assert screenable_text(ev) == "This study shows TRPC6 is relevant."

    def test_falls_back_to_brief_summary(self):
        ev = _make_evidence(
            EvidenceType.ARTICLE,
            {"brief_summary": "Fallback summary."},
        )
        assert screenable_text(ev) == "Fallback summary."

    def test_falls_back_to_assoc_text(self):
        ev = _make_evidence(
            EvidenceType.ARTICLE,
            {"assoc_text": "Association text."},
        )
        assert screenable_text(ev) == "Association text."

    def test_empty_returns_empty_string(self):
        ev = _make_evidence(EvidenceType.ARTICLE, {})
        assert screenable_text(ev) == ""


# Shape of NCT07220083 (Phase 3 BI 764198): the gene first appears ~605 chars into eligibility.
_LATE_ELIGIBILITY = (
    "Inclusion criteria: " + "x" * 560 + " 4. Genetic FSGS resulting from a gain-of-function "
    "mutation in the TRPC6 gene (based on historical genetic test). Exclusion criteria: ..."
)


class TestLateGeneMention:
    def test_late_mention_reaches_screenable_text(self) -> None:
        ev = _make_evidence(
            EvidenceType.CLINICAL_TRIAL,
            {"participation_criteria": {"eligibility_criteria": _LATE_ELIGIBILITY}},
        )
        assert _LATE_ELIGIBILITY.find("TRPC6") > 600
        text = screenable_text(ev)
        assert "TRPC6 gene" in text
        assert text.count("TRPC6") == 1

    def test_early_mention_is_not_duplicated(self) -> None:
        elig = "Inclusion: TRPC6 gain-of-function variant carriers. " + "y" * 900
        ev = _make_evidence(
            EvidenceType.CLINICAL_TRIAL, {"participation_criteria": {"eligibility_criteria": elig}}
        )
        assert screenable_text(ev).count("TRPC6") == 1


class TestIsTargetDrugTrial:
    def _trial(self, phase: str, eligibility: str = _LATE_ELIGIBILITY, **extra) -> Evidence:
        return _make_evidence(
            EvidenceType.CLINICAL_TRIAL,
            {
                "title": "A Study to Test BI 764198 in People With FSGS",
                "phase": phase,
                "interventions": ["BI 764198", "Placebo"],
                "participation_criteria": {"eligibility_criteria": eligibility},
                **extra,
            },
        )

    def test_phased_trial_naming_target_anywhere(self) -> None:
        assert is_target_drug_trial(self._trial("PHASE3"))
        assert is_target_drug_trial(self._trial("PHASE2/PHASE3"))
        assert is_target_drug_trial(self._trial("EARLY_PHASE1"))

    def test_observational_or_unphased_trials_are_left_to_the_llm(self) -> None:
        assert not is_target_drug_trial(self._trial(""))
        assert not is_target_drug_trial(self._trial("NA"))

    def test_phased_trial_not_naming_target(self) -> None:
        assert not is_target_drug_trial(self._trial("PHASE3", eligibility="Adults with FSGS."))

    def test_non_trial_evidence(self) -> None:
        ev = _make_evidence(EvidenceType.ARTICLE, {"phase": "PHASE3", "title": "TRPC6"})
        assert not is_target_drug_trial(ev)


def test_mentions_term_whole_token() -> None:
    assert mentions_term("ANTI-PCSK9 antibodies", "PCSK9")
    assert mentions_term("in the trpc6 gene", "TRPC6")
    assert not mentions_term("PCSK90 variants", "PCSK9")
    assert not mentions_term("anything", "")


# Abridged from the archived sparsentan label of a TRPC6 × FSGS run.
_SPARSENTAN_LABEL = {
    "drug_name": "SPARSENTAN",
    "mechanism_of_action": "Sparsentan is a single molecule with antagonism of the endothelin "
    "type A receptor and the angiotensin II type 1 receptor.",
    "indications_and_usage": "To reduce proteinuria in adult and pediatric patients aged 8 "
    "years and older with focal segmental glomerulosclerosis (FSGS).",
    "text": "Drug: SPARSENTAN. MoA: Sparsentan is a single molecule with antagonism of ...",
}
_PRIMIDONE_LABEL = {
    "drug_name": "PRIMIDONE",
    "mechanism_of_action": "",
    "indications_and_usage": "Primidone is indicated in the control of grand mal, "
    "psychomotor, and focal epileptic seizures.",
    "text": "Drug: PRIMIDONE. Indication: ... focal epileptic seizures.",
}


class TestRegulatoryScreenableText:
    def test_returns_rendered_label_text(self):
        """openFDA rows carry no abstract; without `text` they were pre-dropped as empty."""
        ev = _make_evidence(EvidenceType.REGULATORY, _SPARSENTAN_LABEL, "fda:label:NDA216403")
        assert screenable_text(ev) == _SPARSENTAN_LABEL["text"]

    def test_returns_faers_text(self):
        extra = {"drug_name": "SPARSENTAN", "text": "SPARSENTAN: 5866 FAERS reports."}
        ev = _make_evidence(EvidenceType.REGULATORY, extra, "fda:faers:SPARSENTAN")
        assert screenable_text(ev) == "SPARSENTAN: 5866 FAERS reports."


class TestIsOnTargetFdaLabel:
    def test_disease_in_indication(self):
        ev = _make_evidence(EvidenceType.REGULATORY, _SPARSENTAN_LABEL, "fda:label:NDA216403")
        assert is_on_target_fda_label(ev)

    def test_gene_in_moa(self):
        extra = {"mechanism_of_action": "A selective TRPC6 channel inhibitor."}
        ev = _make_evidence(EvidenceType.REGULATORY, extra, "fda:label:NDA000001")
        assert is_on_target_fda_label(ev)

    def test_single_shared_word_is_not_a_match(self):
        ev = _make_evidence(EvidenceType.REGULATORY, _PRIMIDONE_LABEL, "fda:label:ANDA040586")
        assert not is_on_target_fda_label(ev)

    def test_faers_bundle_is_not_a_label(self):
        ev = _make_evidence(EvidenceType.REGULATORY, _SPARSENTAN_LABEL, "fda:faers:SPARSENTAN")
        assert not is_on_target_fda_label(ev)


class TestDruggabilityScreenableText:
    def test_uses_prerendered_text(self):
        """Druggability rows carry no abstract; without this they were dropped unscreened."""
        ev = _make_evidence(
            EvidenceType.DRUGGABILITY,
            {"text": "UniProt Q9Y210: TRPC6. Localization: Cell membrane."},
            source="uniprot:Q9Y210",
        )
        assert screenable_text(ev) == "UniProt Q9Y210: TRPC6. Localization: Cell membrane."


class TestIsOnTargetPatent:
    def test_gene_in_title(self):
        ev = _make_evidence(
            EvidenceType.PATENT, {"title": "INHIBITORS OF TRPC6", "abstract": ""}, source="1"
        )
        assert is_on_target_patent(ev)

    def test_gene_in_abstract(self):
        ev = _make_evidence(
            EvidenceType.PATENT,
            {"title": "Method of treating FSGS", "abstract": "Administering a TRPC6 antagonist."},
            source="2",
        )
        assert is_on_target_patent(ev)

    def test_disease_only_patent_is_not_on_target(self):
        ev = _make_evidence(
            EvidenceType.PATENT,
            {"title": "METHOD OF TREATING FOCAL SEGMENTAL GLOMERULOSCLEROSIS", "abstract": ""},
            source="3",
        )
        assert not is_on_target_patent(ev)

    def test_other_evidence_types_never_match(self):
        ev = _make_evidence(EvidenceType.ARTICLE, {"title": "TRPC6 in FSGS"}, source="PMID:1")
        assert not is_on_target_patent(ev)


class TestLabelIndications:
    _FILSPARI = (
        "1 INDICATIONS AND USAGE FILSPARI is an endothelin and angiotensin II receptor "
        "antagonist indicated: • To slow kidney function decline in adults with primary "
        "immunoglobulin A nephropathy (IgAN) who are at risk for disease progression ( 1.1 , "
        "12.1 , 14.1 ). • To reduce proteinuria in adult and pediatric patients aged 8 years "
        "and older with focal segmental glomerulosclerosis (FSGS) without nephrotic syndrome "
        "( 1.2 , 12.1 , 14.2 ). 1.1 Immunoglobulin A Nephropathy FILSPARI is indicated to slow"
    )

    def test_every_bullet_is_returned_without_section_refs(self):
        assert label_indications(self._FILSPARI) == [
            "To slow kidney function decline in adults with primary immunoglobulin A "
            "nephropathy (IgAN) who are at risk for disease progression",
            "To reduce proteinuria in adult and pediatric patients aged 8 years and older "
            "with focal segmental glomerulosclerosis (FSGS) without nephrotic syndrome",
        ]

    def test_label_without_bullets_returns_its_statement(self):
        text = "1 INDICATIONS AND USAGE DrugX is indicated for hypertension. 1.1 Adults"
        assert label_indications(text) == ["DrugX is indicated for hypertension."]

    def test_empty(self):
        assert label_indications("") == []


# ---------------------------------------------------------------------------
# Typography, eponyms, negated indications, trait scoping, registry ids
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

from core.evidence_text import (  # noqa: E402
    disease_name_variants,
    fold_name,
    indicates_disease,
    indicates_term,
    mentions_disease,
    normalize_typography,
    registry_ids,
    trait_matches,
)


@pytest.mark.parametrize(
    ("text", "disease"),
    [
        ("indicated for the treatment of Crohn’s disease", "Crohn disease"),  # U+2019
        ("for Alzheimerʼs disease dementia", "Alzheimer disease"),  # U+02BC
        ("signs of Huntington′s chorea", "Huntington chorea"),  # U+2032 prime
        ("in Graves’ disease", "Graves disease"),  # bare-apostrophe possessive
        ("non‑small cell lung cancer", "non-small cell lung cancer"),  # U+2011 hyphen
        ("non small cell lung cancer", "non-small cell lung cancer"),  # hyphen as space
        ("type 2 diabetes mellitus", "type 2 diabetes mellitus"),  # no-break space
    ],
)
def test_mentions_disease_tolerates_typographic_variants(text: str, disease: str) -> None:
    assert mentions_disease(text, disease)


def test_disease_name_variants_from_any_eponym_form() -> None:
    # A possessive input yields the bare form too, whatever apostrophe it was typed with.
    assert "Addison disease" in disease_name_variants("Addison’s disease")
    assert "Hodgkin's lymphoma" in disease_name_variants("Hodgkin lymphoma")
    assert "Graves' disease" in disease_name_variants("Graves disease")
    assert disease_name_variants("chronic kidney disease") == ["chronic kidney disease"]


def test_mentions_term_still_whole_token() -> None:
    assert not mentions_disease("drug-induced parkinsonism", "Parkinson disease")
    assert not mentions_disease("Wolff-Parkinson-White syndrome", "Parkinson disease")
    assert normalize_typography("a’b–c d") == "a'b-c d"


@pytest.mark.parametrize(
    "label",
    [
        # Denials and limitations of use — the disease is named, but not as an indication.
        "Indicated for spasticity. The efficacy in stroke and Crohn's disease has not been "
        "established and, therefore, it is not recommended.",
        "Limitations of Use: not indicated for the treatment of ulcerative colitis.",
        "Limitations of Use: X has not been studied in patients with ulcerative colitis.",
        "Use in ulcerative colitis is contraindicated.",
        "The safety and effectiveness of X in patients with ulcerative colitis are unknown.",
        "X should not be used in patients with ulcerative colitis.",
    ],
)
def test_indicates_disease_rejects_negated_mentions(label: str) -> None:
    disease = "Crohn disease" if "Crohn" in label else "ulcerative colitis"
    assert mentions_disease(label, disease)
    assert not indicates_disease(label, disease)


@pytest.mark.parametrize(
    ("label", "disease"),
    [
        ("X is indicated for the treatment of ulcerative colitis.", "ulcerative colitis"),
        # An unrelated denial elsewhere does not cancel a real indication.
        (
            "X is indicated for moderate to severe ulcerative colitis. Safety and "
            "effectiveness in pediatric patients have not been established.",
            "ulcerative colitis",
        ),
        (
            "X is indicated for Crohn’s disease in adults who have not responded to "
            "conventional therapy; it is not indicated for fistula closure.",
            "Crohn disease",
        ),
        ("To reduce the risk of stroke in adults with atrial fibrillation.", "stroke"),
    ],
)
def test_indicates_disease_accepts_real_indications(label: str, disease: str) -> None:
    assert indicates_disease(label, disease)


def test_indicates_term_on_a_single_word() -> None:
    assert indicates_term("For relief of migraine attacks.", "migraine")
    assert not indicates_term("Efficacy in migraine has not been demonstrated.", "migraine")


def test_trait_matches_folds_possessives_and_typography() -> None:
    assert trait_matches("Alzheimer’s disease (late onset)", ["Alzheimer disease"])
    assert trait_matches("Type-2 Diabetes", ["type 2 diabetes"])
    assert not trait_matches("Wolff-Parkinson-White syndrome", ["Parkinson disease"])
    assert fold_name("Behçet’s  Syndrome") == "behçet syndrome"


def test_registry_ids_across_registries() -> None:
    text = (
        "Registered at ClinicalTrials.gov (NCT01234567), ISRCTN12345678, EudraCT "
        "2014-001234-56, ChiCTR2000034567, ACTRN12619000123456 and CTRI/2020/05/025123."
    )
    assert registry_ids(text) == {
        "NCT01234567",
        "ISRCTN12345678",
        "2014-001234-56",
        "CHICTR2000034567",
        "ACTRN12619000123456",
        "CTRI/2020/05/025123",
    }
    assert registry_ids("GENE1234567 and NCT123") == set()


def test_diagnostic_study_is_not_a_target_drug_trial() -> None:
    extra = {
        "phase": "PHASE2",
        "title": "Imaging biomarker study in GENE-A mutation carriers",
        "interventions": ["radiotracer scan"],
        "study_plan": {"primary_purpose": "DIAGNOSTIC"},
    }
    ev = _make_evidence(EvidenceType.CLINICAL_TRIAL, extra).model_copy(update={"gene": "GENE-A"})
    assert not is_target_drug_trial(ev)
    treat = {**extra, "study_plan": {"primary_purpose": "TREATMENT"}}
    ev2 = _make_evidence(EvidenceType.CLINICAL_TRIAL, treat).model_copy(update={"gene": "GENE-A"})
    assert is_target_drug_trial(ev2)


@pytest.mark.parametrize(
    ("purpose", "interventions", "diagnostic"),
    [
        ("DIAGNOSTIC", ["Drug X"], True),
        ("OTHER", ["DaTscan"], True),
        ("BASIC_SCIENCE", ["PET with the tracer 18F-ABC-123", "PET with the tracer 11C-XYZ"], True),
        ("OTHER", ["[18F]TRACER-9"], True),
        ("", ["99mTc-labelled agent", "Placebo"], True),
        ("OTHER", ["drug-x (new manufacturing process)", "drug-x"], False),  # bioequivalence
        ("TREATMENT", ["Lu-177 radioligand therapy"], False),  # stated purpose wins
        ("TREATMENT", ["Drug Y", "Placebo"], False),
        ("", ["Drug Z 11 mg"], False),
    ],
)
def test_is_diagnostic_study(purpose: str, interventions: list[str], diagnostic: bool) -> None:
    from core.evidence_text import is_diagnostic_study

    assert is_diagnostic_study(purpose, interventions) is diagnostic
