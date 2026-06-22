# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for evidence_hierarchy.py — deterministic, disease-class-conditional
structured-evidence weights."""

from __future__ import annotations

from schemas.evidence import EvidenceType
from services.evidence.disease_class import DiseaseClass
from services.evidence.evidence_hierarchy import (
    evidence_weight,
    infer_evidence_subtype,
    llm_prior_weight,
    reload_evidence_hierarchy,
)


class TestInferEvidenceSubtype:
    def setup_method(self):
        reload_evidence_hierarchy()

    def test_depmap_token_matches_functional_genomics_subtype(self):
        subtype = infer_evidence_subtype(
            EvidenceType.FUNCTIONAL_GENOMICS, "DepMap Chronos score of -0.8 in NCI-H460"
        )
        assert subtype == "depmap"

    def test_impc_token_matches_viability_subtype(self):
        subtype = infer_evidence_subtype(
            EvidenceType.FUNCTIONAL_GENOMICS, "IMPC knockout mouse shows embryonic lethal phenotype"
        )
        assert subtype == "impc_viability"

    def test_no_token_match_returns_none(self):
        assert infer_evidence_subtype(EvidenceType.FUNCTIONAL_GENOMICS, "shRNA knockdown reduced viability") is None

    def test_evidence_type_with_no_subtypes_configured_returns_none(self):
        assert infer_evidence_subtype(EvidenceType.GENETICS, "anything") is None

    def test_empty_claim_text_returns_none(self):
        assert infer_evidence_subtype(EvidenceType.FUNCTIONAL_GENOMICS, "") is None


class TestEvidenceWeight:
    def setup_method(self):
        reload_evidence_hierarchy()

    def test_genetics_weighs_top_of_hierarchy(self):
        assert evidence_weight(EvidenceType.GENETICS, None, ()) == 1.0

    def test_depmap_outside_oncology_is_low_weight(self):
        weight = evidence_weight(EvidenceType.FUNCTIONAL_GENOMICS, "depmap", [DiseaseClass.METABOLIC])
        assert weight == 0.25

    def test_depmap_in_oncology_is_high_weight(self):
        weight = evidence_weight(EvidenceType.FUNCTIONAL_GENOMICS, "depmap", [DiseaseClass.ONCOLOGY])
        assert weight == 1.0

    def test_depmap_with_no_disease_classes_uses_default(self):
        weight = evidence_weight(EvidenceType.FUNCTIONAL_GENOMICS, "depmap", ())
        assert weight == 0.25

    def test_generic_functional_genomics_subtype_unaffected_by_oncology(self):
        # Subtype-less functional_genomics claim must not pick up the depmap
        # disease-class override.
        weight = evidence_weight(EvidenceType.FUNCTIONAL_GENOMICS, None, [DiseaseClass.ONCOLOGY])
        assert weight == 0.6

    def test_tumor_expression_outside_oncology_is_low_weight(self):
        weight = evidence_weight(EvidenceType.EXPRESSION, "tumor_expression", [DiseaseClass.METABOLIC])
        assert weight == 0.25

    def test_tumor_expression_in_oncology_is_informative(self):
        weight = evidence_weight(EvidenceType.EXPRESSION, "tumor_expression", [DiseaseClass.ONCOLOGY])
        assert weight == 0.8

    def test_unconfigured_subtype_falls_back_to_evidence_type_default(self):
        weight = evidence_weight(EvidenceType.FUNCTIONAL_GENOMICS, "made_up_subtype", ())
        assert weight == 0.6

    def test_accepts_plain_strings_not_just_enum_members(self):
        weight = evidence_weight(EvidenceType.FUNCTIONAL_GENOMICS, "depmap", ["oncology"])
        assert weight == 1.0


class TestLlmPriorWeight:
    def setup_method(self):
        reload_evidence_hierarchy()

    def test_is_lowest_weight_in_the_hierarchy(self):
        prior = llm_prior_weight()
        assert prior == 0.05
        assert prior < evidence_weight(EvidenceType.PATENT, None, ())
