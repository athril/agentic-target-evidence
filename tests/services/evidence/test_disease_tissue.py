# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for disease_tissue.py — disease->tissue resolver + deterministic relevance framing.

Regression lock for the TRPC6 x FSGS report tissue-relevance error: the biology and
safety lenses called Lung (23.2 TPM) and Esophagus_Muscularis (19.0 TPM) "relevant
tissues for FSGS" purely because they ranked highest by bulk GTEx TPM, and called
Kidney_Cortex (1.2 TPM) "high" expression. These tests lock that the deterministic
grounding helpers never reproduce that error.
"""

from __future__ import annotations

from services.evidence.disease_tissue import (
    DiseaseTissueInfo,
    build_disease_tissue_expression_note,
    extract_tissue_tpm,
    reload_disease_tissue_map,
    resolve_disease_tissue,
)

# Mirrors results/data/TRPC6/EFO_0004236/inhibit/omics/TRPC6_gtex_hpa.json (top tissues).
_TRPC6_GTEX = [
    {"tissue": "Lung", "median_tpm": 23.2145},
    {"tissue": "Esophagus_Muscularis", "median_tpm": 18.9787},
    {"tissue": "Thyroid", "median_tpm": 12.0154},
    {"tissue": "Esophagus_Gastroesophageal_Junction", "median_tpm": 9.48724},
    {"tissue": "Adipose_Subcutaneous", "median_tpm": 7.89558},
    {"tissue": "Kidney_Medulla", "median_tpm": 1.3459},
    {"tissue": "Kidney_Cortex", "median_tpm": 1.17262},
    {"tissue": "Liver", "median_tpm": 0.0440401},
]


class TestResolveDiseaseTissue:
    def setup_method(self):
        reload_disease_tissue_map()

    def test_known_disease_resolves(self):
        info = resolve_disease_tissue("EFO_0004236")
        assert info is not None
        assert "Kidney_Cortex" in info.gtex_tissues
        assert "podocyte" in info.cell_types

    def test_unknown_disease_returns_none(self):
        assert resolve_disease_tissue("EFO_9999999") is None

    def test_empty_disease_id_returns_none(self):
        assert resolve_disease_tissue("") is None
        assert resolve_disease_tissue(None) is None


class TestExtractTissueTpm:
    def test_finds_tissue_and_rank(self):
        tpm, rank, total = extract_tissue_tpm(_TRPC6_GTEX, "Kidney_Cortex")
        assert tpm == 1.17262
        assert rank == 7  # 7th highest of 8
        assert total == 8

    def test_top_ranked_tissue_is_rank_1(self):
        tpm, rank, total = extract_tissue_tpm(_TRPC6_GTEX, "Lung")
        assert tpm == 23.2145
        assert rank == 1

    def test_missing_tissue_returns_none_tpm(self):
        tpm, rank, total = extract_tissue_tpm(_TRPC6_GTEX, "Pancreas")
        assert tpm is None
        assert rank is None
        assert total == 8

    def test_empty_list_returns_all_none(self):
        assert extract_tissue_tpm([], "Kidney_Cortex") == (None, None, None)


class TestBuildDiseaseTissueExpressionNote:
    def test_empty_gtex_returns_empty_string(self):
        assert build_disease_tissue_expression_note([], None, "FSGS") == ""

    def test_unknown_disease_states_mapping_unknown_not_tpm_rank(self):
        note = build_disease_tissue_expression_note(_TRPC6_GTEX, None, "some rare disease")
        assert "MAPPING UNKNOWN" in note
        assert "Do NOT infer disease relevance from this TPM ranking" in note

    def test_known_disease_names_kidney_not_lung_as_relevant(self):
        info = DiseaseTissueInfo(
            disease_id="EFO_0004236",
            disease_name="Focal Segmental Glomerulosclerosis",
            gtex_tissues=["Kidney_Cortex", "Kidney_Medulla"],
            cell_types=["podocyte"],
            note="FSGS is driven by podocyte injury.",
        )
        note = build_disease_tissue_expression_note(_TRPC6_GTEX, info, "FSGS")
        assert "Kidney_Cortex" in note
        assert "LOW" in note
        assert "rank 7/8" in note
        assert "podocyte" in note
        assert "NOT necessarily relevant" in note

    def test_high_tpm_in_disease_tissue_is_not_labelled_low(self):
        info = DiseaseTissueInfo(disease_id="X", gtex_tissues=["Lung"], cell_types=[])
        note = build_disease_tissue_expression_note(_TRPC6_GTEX, info, "lung disease")
        assert "Lung: 23.21 TPM (HIGH" in note
