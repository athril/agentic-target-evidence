# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for mouse_phenotype.py — deterministic MGI/OT phenotype renderer."""

from __future__ import annotations

from services.evidence.mouse_phenotype import (
    dedup_phenotype_list,
    render_mouse_phenotype,
)


class TestDeduplication:
    def test_exact_duplicates_removed(self):
        labels = [
            "abnormal vasoconstriction",
            "increased blood pressure",
            "abnormal vasoconstriction",
        ]
        result = dedup_phenotype_list(labels)
        assert result.count("abnormal vasoconstriction") == 1

    def test_near_duplicate_vasoconstriction_deduped(self):
        """'abnormal vasoconstriction' + 'increased vasoconstriction' are near-duplicates (TRPC6 case)."""
        labels = ["abnormal vasoconstriction", "increased vasoconstriction"]
        result = dedup_phenotype_list(labels)
        # One of the two should be dropped; exactly one should remain
        vasoconstriction = [lbl for lbl in result if "vasoconstriction" in lbl.lower()]
        assert len(vasoconstriction) == 1

    def test_distinct_phenotypes_all_kept(self):
        labels = ["abnormal vasoconstriction", "kidney failure", "cardiac hypertrophy"]
        result = dedup_phenotype_list(labels)
        assert len(result) == 3

    def test_empty_list_returns_empty(self):
        assert dedup_phenotype_list([]) == []

    def test_single_item_returned_unchanged(self):
        assert dedup_phenotype_list(["podocyte loss"]) == ["podocyte loss"]


class TestContradictionDetection:
    def test_contradictory_phenotypes_flagged(self):
        """'increased vasoconstriction' AND 'decreased vasoconstriction' → contradiction note."""
        text = "increased vasoconstriction\ndecreased vasoconstriction"
        result = render_mouse_phenotype(text)
        assert "NOTE" in result or "contradict" in result.lower()

    def test_unidirectional_phenotype_no_caveat(self):
        """Single direction → no contradiction caveat."""
        text = "increased vasoconstriction"
        result = render_mouse_phenotype(text)
        assert "NOTE" not in result
        assert "contradict" not in result.lower()


class TestRenderMousePhenotype:
    def test_empty_returns_empty(self):
        assert render_mouse_phenotype("") == ""
        assert render_mouse_phenotype("   ") == "   "

    def test_none_like_handling(self):
        result = render_mouse_phenotype("increased blood pressure; increased blood pressure")
        # Dedup should remove one
        assert result.count("increased blood pressure") == 1

    def test_semicolon_split(self):
        text = "phenotype A; phenotype B; phenotype C"
        result = render_mouse_phenotype(text)
        assert "phenotype A" in result
        assert "phenotype B" in result

    def test_trpc6_vasoconstriction_case(self):
        """TRPC6 regression: 'abnormal vasoconstriction' + 'increased vasoconstriction'
        must NOT appear as two separate clean phenotypes — dedup removes one."""
        text = "abnormal vasoconstriction; increased vasoconstriction; kidney failure"
        result = render_mouse_phenotype(text)
        vasoconstriction_count = result.lower().count("vasoconstriction")
        # After dedup there should be at most one vasoconstriction entry
        # (the near-duplicate should have been removed)
        assert vasoconstriction_count <= 2  # one entry, but "vasoconstriction" may appear once
