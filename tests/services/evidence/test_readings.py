# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for banded quantitative readings and the generic quantity-wording guard."""

from __future__ import annotations

import pytest

from services.evidence.readings import (
    METRICS,
    build_dependency_reading,
    check_quantity_wording,
    dependency_band_prompt,
    dependency_is_uninformative,
    make_reading,
    trial_count_readings,
)

_PS_TRPC6 = {"num_fitness_lines": 2, "total_lines": 512, "bf_scaled_mean": 3.485}
_DM_TRPC6 = {"num_dependent_lines": 2, "total_lines": 1208, "gene_effect_mean": -0.05}


class TestBands:
    @pytest.mark.parametrize(
        ("value", "band"),
        [
            (0.0, "none"),
            (0.0039, "none"),
            (0.01, "rare"),
            (0.09, "rare"),
            (0.3, "subset"),
            (0.5, "broad"),
            (0.95, "broad"),
        ],
    )
    def test_dependency_bands(self, value, band):
        assert METRICS["cell_line_dependency"].band_for(value).key == band

    @pytest.mark.parametrize(("count", "band"), [(0, "none"), (1, "single"), (2, "multiple")])
    def test_trial_count_bands(self, count, band):
        assert METRICS["phase3_trial_count"].band_for(count).key == band

    def test_reading_label_leads_with_band_description(self):
        r = make_reading("cell_line_dependency", 0.004, "2/512 lines")
        assert r.label == "not a cell-line dependency — 2/512 lines"
        assert r.band == "none"


class TestDependencyReading:
    def test_depmap_preferred_over_project_score(self):
        r = build_dependency_reading(_DM_TRPC6, _PS_TRPC6)
        assert r is not None and r.source == "DepMap"
        assert (r.n_dependent, r.n_total) == (2, 1208)

    def test_falls_back_to_project_score_when_depmap_has_no_counts(self):
        r = build_dependency_reading(
            {"text": "DepMap: TRPC6 dependent in unknown lines."}, _PS_TRPC6
        )
        assert r is not None and r.source == "Project Score"
        assert "2/512" in r.label
        assert r.reading.band == "none"

    def test_none_when_neither_source_has_counts(self):
        assert build_dependency_reading({}, {"sidg_id": "SIDG1"}) is None
        assert build_dependency_reading(None, None) is None

    def test_uninformative_for_non_oncology_near_zero_dependency(self):
        r = build_dependency_reading(None, _PS_TRPC6)
        assert dependency_is_uninformative(r, is_oncology=False)
        assert not dependency_is_uninformative(r, is_oncology=True)

    def test_missing_reading_is_not_uninformative(self):
        """Absent data is 'not evaluated', never 'uninformative'."""
        assert not dependency_is_uninformative(None, is_oncology=False)

    def test_common_essential_is_never_uninformative(self):
        r = build_dependency_reading(
            {"num_dependent_lines": 5, "total_lines": 1000, "is_common_essential": True}, None
        )
        assert not dependency_is_uninformative(r, is_oncology=False)

    def test_band_prompt_quotes_the_label(self):
        r = build_dependency_reading(None, _PS_TRPC6)
        assert r is not None
        assert r.label in dependency_band_prompt(r)


class TestQuantityWording:
    def _trpc6(self):
        r = build_dependency_reading(None, _PS_TRPC6)
        assert r is not None
        return [r.reading]

    def test_vague_quantifier_flagged_against_none_band(self):
        found = check_quantity_wording(
            "CRISPR data indicates dependency across some cancer cell lines.", self._trpc6()
        )
        assert [v.rule_id for v in found] == ["quantity.cell_line_dependency"]
        assert "2/512" in found[0].note

    def test_exact_number_is_never_flagged(self):
        text = "TRPC6 is a fitness gene in 2/512 Sanger cell lines (0.4%)."
        assert check_quantity_wording(text, self._trpc6()) == []

    def test_clause_without_topic_is_ignored(self):
        assert check_quantity_wording("Several studies link TRPC6 to FSGS.", self._trpc6()) == []

    def test_understatement_flagged_for_broad_band(self):
        r = build_dependency_reading(
            {"num_dependent_lines": 820, "total_lines": 1100, "gene_effect_mean": -1.1}, None
        )
        assert r is not None
        found = check_quantity_wording("KRAS is non-essential in cancer cell lines.", [r.reading])
        assert found and found[0].kind == "contradiction"

    def test_trial_readings_flag_plural_for_single_trial_phase(self):
        readings = trial_count_readings({2: 1, 3: 1}, {2: ["NCT05213624"], 3: ["NCT07220083"]})
        found = check_quantity_wording("Multiple Phase 2 trials tested BI 764198.", readings)
        assert [v.rule_id for v in found] == ["quantity.phase2_trial_count"]
        assert "NCT05213624" in found[0].note

    def test_trial_readings_allow_plural_when_count_supports_it(self):
        readings = trial_count_readings({3: 3}, {3: ["NCT1", "NCT2", "NCT3"]})
        assert check_quantity_wording("Several Phase 3 trials are ongoing.", readings) == []
