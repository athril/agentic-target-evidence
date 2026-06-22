# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for disease_class.py — disease-class taxonomy resolution."""

from __future__ import annotations

from services.evidence.disease_class import (
    DiseaseClass,
    reload_disease_class_map,
    resolve_disease_class,
)


class TestResolveDiseaseClass:
    def setup_method(self):
        reload_disease_class_map()

    def test_oncology_therapeutic_area_resolves_oncology(self):
        classes = resolve_disease_class(None, {"MONDO_0045024"}, None)
        assert classes == {DiseaseClass.ONCOLOGY}

    def test_unmapped_therapeutic_area_falls_back_to_other(self):
        classes = resolve_disease_class(None, {"EFO_9999999"}, None)
        assert classes == {DiseaseClass.OTHER}

    def test_no_inputs_falls_back_to_other(self):
        assert resolve_disease_class(None, None, None) == {DiseaseClass.OTHER}

    def test_efo_override_returns_multiple_non_exclusive_classes(self):
        # NASH/MASH — curated override, not derivable from a single therapeutic area.
        classes = resolve_disease_class("EFO_0003095", set(), None)
        assert classes == {DiseaseClass.METABOLIC, DiseaseClass.FIBROSIS}

    def test_efo_override_combines_with_therapeutic_area_match(self):
        classes = resolve_disease_class("EFO_0003095", {"EFO_0000540"}, None)
        assert classes == {
            DiseaseClass.METABOLIC,
            DiseaseClass.FIBROSIS,
            DiseaseClass.AUTOIMMUNE,
        }

    def test_rare_mendelian_inferred_from_floor_signals_regardless_of_area(self):
        floor_signals = {
            "high_star_plp": 2,
            "plp_count": 3,
            "clingen_classification": None,
            "graph_association": None,
        }
        classes = resolve_disease_class(None, {"MONDO_0045024"}, floor_signals)
        assert classes == {DiseaseClass.ONCOLOGY, DiseaseClass.RARE_MENDELIAN}

    def test_weak_floor_signals_do_not_trigger_rare_mendelian(self):
        floor_signals = {
            "high_star_plp": 0,
            "plp_count": 1,
            "clingen_classification": None,
            "graph_association": None,
        }
        classes = resolve_disease_class(None, set(), floor_signals)
        assert classes == {DiseaseClass.OTHER}

    def test_clingen_definitive_alone_triggers_rare_mendelian(self):
        floor_signals = {
            "high_star_plp": 0,
            "plp_count": 0,
            "clingen_classification": "Definitive",
            "graph_association": None,
        }
        classes = resolve_disease_class(None, set(), floor_signals)
        assert classes == {DiseaseClass.RARE_MENDELIAN}

    def test_empty_therapeutic_areas_iterable_is_safe(self):
        assert resolve_disease_class("EFO_NOPE", [], {}) == {DiseaseClass.OTHER}
