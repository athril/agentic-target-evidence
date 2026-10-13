# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The disease-tissue mapping resolves for any disease, not only curated ones.

config/disease_tissue.yaml used to hold a single curated entry (FSGS under its EFO id),
so the grounding note and the tissue-relevance guard were unarmed for every other
disease — and for FSGS itself once runs resolved it to a MONDO id. Keyword rules over
the disease name and its therapeutic areas now give an (inferred) mapping everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.evidence.disease_tissue import (
    build_disease_tissue_expression_note,
    resolve_disease_tissue,
)


@pytest.mark.parametrize(
    "disease,expected_first",
    [
        ("focal segmental glomerulosclerosis", "Kidney_Cortex"),
        ("myocardial infarction", "Heart_Left_Ventricle"),
        ("Parkinson disease", "Brain_Substantia_nigra"),
        ("Alzheimer disease", "Brain_Cortex"),
        ("type 2 diabetes mellitus", "Pancreas"),
        ("Crohn disease", "Colon_Transverse"),
        ("non-small cell lung carcinoma", "Lung"),
        ("psoriasis", "Skin_Sun_Exposed_Lower_leg"),
        ("non-alcoholic steatohepatitis", "Liver"),
        ("familial hypercholesterolemia", "Liver"),
        ("Duchenne muscular dystrophy", "Muscle_Skeletal"),
        ("rheumatoid arthritis", "Whole_Blood"),
        ("multiple sclerosis", "Brain_Cortex"),
        ("spinocerebellar ataxia", "Brain_Cerebellum"),
        ("glioblastoma", "Brain_Cortex"),
        ("long QT syndrome", "Heart_Left_Ventricle"),
        ("hereditary hemochromatosis", "Liver"),
        ("Pompe disease", "Muscle_Skeletal"),
        ("gout", "Kidney_Cortex"),
    ],
)
def test_keyword_mapping_covers_many_diseases(disease, expected_first):
    info = resolve_disease_tissue("MONDO_9999999", disease_name=disease)
    assert info is not None and info.inferred
    assert info.gtex_tissues[0] == expected_first


def test_curated_entry_wins_and_is_not_inferred():
    info = resolve_disease_tissue("MONDO_0100313", disease_name="anything")
    assert info is not None and not info.inferred
    assert "podocyte" in info.cell_types


def test_therapeutic_area_is_the_fallback():
    info = resolve_disease_tissue(
        "MONDO_9999998", disease_name="xyz syndrome", area_names={"cardiovascular disease"}
    )
    assert info is not None and info.gtex_tissues[0] == "Artery_Coronary"


def test_unmatched_disease_stays_unknown():
    assert resolve_disease_tissue("MONDO_9999997", disease_name="xyz syndrome") is None


def test_inferred_mapping_is_labelled_in_the_note():
    info = resolve_disease_tissue("MONDO_9999999", disease_name="myocardial infarction")
    gtex = [
        {"tissue": "Liver", "median_tpm": 25.5},
        {"tissue": "Heart_Left_Ventricle", "median_tpm": 0.1},
    ]
    note = build_disease_tissue_expression_note(gtex, info, "myocardial infarction")
    assert "Heart_Left_Ventricle: 0.10 TPM (LOW; rank 2/2)" in note
    assert "inferred from the disease name" in note


_COVERAGE = [
    line.strip()
    for line in Path("tests/fixtures/disease_names_coverage.txt").read_text("utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]


def test_name_rules_cover_diseases_across_therapeutic_areas():
    missed = [d for d in _COVERAGE if resolve_disease_tissue("", disease_name=d) is None]
    assert len(missed) <= len(_COVERAGE) // 20, missed  # ≥ 95 %


@pytest.mark.parametrize(
    "disease,expected_first,forbidden",
    [
        # Head noun wins over the modifier's organ.
        ("diabetic nephropathy", "Kidney_Cortex", {"Pancreas"}),
        ("pancreatic ductal adenocarcinoma", "Pancreas", {"Liver", "Muscle_Skeletal"}),
        # "segmental" is not "mental"; "cardiomyopathy" is not a skeletal myopathy.
        ("focal segmental glomerulosclerosis", "Kidney_Cortex", {"Brain_Cortex"}),
        ("hypertrophic cardiomyopathy", "Heart_Left_Ventricle", {"Muscle_Skeletal"}),
        # A platelet disorder is haematological, not vascular.
        ("immune thrombocytopenia", "Whole_Blood", {"Artery_Coronary"}),
        ("coronary artery disease", "Artery_Coronary", set()),
        ("Huntington disease", "Brain_Caudate_basal_ganglia", set()),
    ],
)
def test_head_noun_and_word_boundaries(disease, expected_first, forbidden):
    info = resolve_disease_tissue("", disease_name=disease)
    assert info is not None
    assert info.gtex_tissues[0] == expected_first
    assert not forbidden & set(info.gtex_tissues)


def test_systemic_rule_is_kept_next_to_the_head_organ():
    info = resolve_disease_tissue("", disease_name="lupus nephritis")
    assert info is not None
    assert info.gtex_tissues[0] == "Kidney_Cortex" and "Whole_Blood" in info.gtex_tissues


def test_melanoma_is_mapped_to_melanocytes_not_keratinocytes():
    info = resolve_disease_tissue("", disease_name="melanoma")
    assert info is not None and info.cell_types == ["melanocyte"]


def test_tissue_without_gtex_sample_still_names_the_cell_type():
    info = resolve_disease_tissue("", disease_name="age-related macular degeneration")
    assert info is not None and not info.gtex_tissues
    assert "photoreceptor" in info.cell_types
    note = build_disease_tissue_expression_note(
        [{"tissue": "Lung", "median_tpm": 9.0}], info, "age-related macular degeneration"
    )
    assert "has no GTEx sample" in note and "photoreceptor" in note
    assert "MAPPING UNKNOWN" not in note
