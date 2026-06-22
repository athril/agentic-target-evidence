# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for commercial_interpret.py — competitive-landscape framing + output guards.

Regression lock for the TRPC6 x FSGS commercial-lens errors:
  C1: "no known drugs targeting TRPC6 for FSGS" — too strong (approved vs.
      clinical vs. preclinical collapsed).
  C2: "competitive field appears underserved" — target-level whitespace stated as
      indication-level whitespace.
  C3: market size declared "unknown" from Orphanet silence alone.
"""

from __future__ import annotations

from services.evidence.commercial_interpret import (
    apply_commercial_guards,
    interpret_competitive_landscape,
)


class TestInterpretCompetitiveLandscape:
    def test_states_distinctions_regardless_of_counts(self):
        txt = interpret_competitive_landscape(0, 0, 0, 0)
        assert "preclinical" in txt.lower()
        assert "target-level whitespace is not indication-level whitespace" in txt.lower()
        # No known drugs -> must not invite a "no drugs exist" claim.
        assert "not proof of none" in txt.lower()

    def test_known_drugs_warns_against_no_drugs_claim(self):
        txt = interpret_competitive_landscape(approved_count=2, phase3_count=1, known_drugs_count=5, trial_count=3)
        assert "5 known drug" in txt
        assert "2 approved" in txt
        assert "contradicts" in txt.lower()


class TestNoDrugsGuard:
    def test_flags_blanket_no_drugs_claim(self):
        text = "Overall, there are no known drugs targeting TRPC6 for FSGS."
        out = apply_commercial_guards(text, known_drugs_count=0, approved_count=0)
        assert "COMMERCIAL GUARD" in out
        assert "approved" in out.lower()

    def test_accurate_no_approved_form_is_not_flagged(self):
        text = "There are currently no approved TRPC6-targeted therapies for FSGS."
        out = apply_commercial_guards(text, known_drugs_count=0, approved_count=0)
        assert out == text

    def test_contradiction_called_out_when_drugs_known(self):
        text = "There are no drugs targeting this gene."
        out = apply_commercial_guards(text, known_drugs_count=4, approved_count=1)
        assert "COMMERCIAL GUARD" in out
        assert "contradicts the retrieved data" in out

    def test_marketed_qualifier_also_exempt(self):
        text = "No marketed therapies target this gene yet."
        assert apply_commercial_guards(text) == text


class TestUnderservedGuard:
    def test_flags_underserved_field(self):
        text = "The competitive field appears underserved for this indication."
        out = apply_commercial_guards(text)
        assert "COMMERCIAL GUARD" in out
        assert "indication-level whitespace" in out.lower()

    def test_flags_uncrowded_market(self):
        text = "This is a largely uncrowded market with little competition."
        out = apply_commercial_guards(text)
        assert "COMMERCIAL GUARD" in out

    def test_no_scope_noun_not_flagged(self):
        # "underserved" with no field/market/indication scope noun nearby.
        text = "Patients remain underserved by current standards of care everywhere."
        out = apply_commercial_guards(text)
        assert "COMMERCIAL GUARD" not in out


class TestMarketUnknownGuard:
    def test_flags_market_size_unknown(self):
        text = "The market size is unknown for this disorder."
        out = apply_commercial_guards(text)
        assert "COMMERCIAL GUARD" in out
        assert "orphanet" in out.lower()

    def test_flags_prevalence_could_not_be_sized(self):
        text = "Disease prevalence could not be determined from the available data."
        out = apply_commercial_guards(text)
        assert "COMMERCIAL GUARD" in out

    def test_sized_from_orphanet_phrasing_not_flagged(self):
        # Recommended phrasing: the subject noun IS tracked, but it is scoped to
        # Orphanet, so the source-scoped exemption must suppress the guard.
        text = (
            "The addressable population could not be sized from Orphanet; external "
            "prevalence estimates may exist."
        )
        out = apply_commercial_guards(text)
        assert "COMMERCIAL GUARD" not in out


class TestGuardComposition:
    def test_empty_text_is_safe(self):
        assert apply_commercial_guards("") == ""

    def test_clean_text_unchanged(self):
        text = (
            "TRPC6 has one approved drug and two Phase 3 candidates; the target itself is "
            "relatively uncontested, though FSGS overall is commercially competitive."
        )
        assert apply_commercial_guards(text, known_drugs_count=3, approved_count=1) == text

    def test_multiple_distinct_notes_accumulate(self):
        text = (
            "There are no known drugs targeting TRPC6. The competitive field appears "
            "underserved. The market size is unknown."
        )
        out = apply_commercial_guards(text)
        assert out.count("COMMERCIAL GUARD") == 3
