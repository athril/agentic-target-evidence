# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for disease_class_rules.py — disease-class x lens guidance matrix."""

from __future__ import annotations

from services.evidence.disease_class import DiseaseClass
from services.evidence.disease_class_rules import (
    build_disease_class_note,
    reload_disease_class_rules,
)


class TestBuildDiseaseClassNote:
    def setup_method(self):
        reload_disease_class_rules()

    def test_empty_disease_classes_returns_empty_string(self):
        assert build_disease_class_note((), "genetics") == ""

    def test_oncology_genetics_returns_germline_gwas_guidance(self):
        note = build_disease_class_note([DiseaseClass.ONCOLOGY], "genetics")
        assert "ONCOLOGY" in note
        assert "germline GWAS" in note

    def test_accepts_plain_strings_not_just_enum_members(self):
        note = build_disease_class_note(["oncology"], "genetics")
        assert "ONCOLOGY" in note

    def test_lens_with_no_matching_rule_returns_empty_string(self):
        # rare_mendelian has no curated "biology" line in the config.
        assert build_disease_class_note([DiseaseClass.RARE_MENDELIAN], "biology") == ""

    def test_oncology_excludes_non_oncology_fallback(self):
        note = build_disease_class_note([DiseaseClass.ONCOLOGY], "safety")
        assert "embryonic-lethal" not in note

    def test_non_oncology_class_injects_non_oncology_safety_fallback(self):
        note = build_disease_class_note([DiseaseClass.METABOLIC], "safety")
        assert "embryonic-lethal" in note

    def test_multiple_classes_union_without_duplicates(self):
        # MASH-style overlap: metabolic + fibrosis both have a `biology` line.
        note = build_disease_class_note([DiseaseClass.METABOLIC, DiseaseClass.FIBROSIS], "biology")
        assert "METABOLIC" in note
        assert "FIBROSIS" in note
        assert note.count("- ") == 2

    def test_header_lists_resolved_classes_sorted(self):
        note = build_disease_class_note([DiseaseClass.FIBROSIS, DiseaseClass.METABOLIC], "biology")
        assert note.startswith("Disease-class context (fibrosis, metabolic):")
