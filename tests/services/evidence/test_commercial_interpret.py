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
    TargetTrial,
    apply_commercial_guards,
    interpret_competitive_landscape,
    target_programmes,
)


class TestInterpretCompetitiveLandscape:
    def test_states_distinctions_regardless_of_counts(self):
        txt = interpret_competitive_landscape(0, 0, 0, 0)
        assert "preclinical" in txt.lower()
        assert "target-level whitespace is not indication-level whitespace" in txt.lower()
        # No known drugs -> must not invite a "no drugs exist" claim.
        assert "not proof of none" in txt.lower()

    def test_known_drugs_warns_against_no_drugs_claim(self):
        txt = interpret_competitive_landscape(
            approved_count=2, phase3_count=1, known_drugs_count=5, trial_count=3
        )
        assert "5 known drug" in txt
        assert "2 approved" in txt
        assert "contradicts" in txt.lower()

    def test_target_programmes_count_as_clinical_stage(self):
        """Open Targets listed no drugs for TRPC6 while BI 764198 was in a phase 3 trial;
        the framing used to tell the lens no clinical-stage drug existed."""
        trials = target_programmes(
            [],
            [TargetTrial("NCT07220083", "PHASE3", "RECRUITING", ("BI 764198", "Placebo"))],
            "inhibit",
        )
        txt = interpret_competitive_landscape(
            0,
            0,
            0,
            4,
            indication_approved_drug_count=1,
            indication_active_trial_count=39,
            indication_total_trial_count=120,
            gene="TRPC6",
            disease="FSGS",
            target_programmes=trials,
        )
        assert "NCT07220083 (PHASE3, RECRUITING): BI 764198" in txt
        assert "No approved or clinical-stage drugs targeting this gene were retrieved" not in txt
        assert "underserved at the target level" not in txt

    def test_no_indication_counts_keeps_legacy_caveat(self):
        txt = interpret_competitive_landscape(0, 0, 0, 0)
        assert "this target-centric retrieval does not enumerate" in txt.lower()
        assert "indication-level competition (target-agnostic)" not in txt.lower()

    def test_indication_counts_cite_real_numbers(self):
        txt = interpret_competitive_landscape(
            0,
            0,
            0,
            0,
            indication_approved_drug_count=4,
            indication_active_trial_count=25,
            indication_phase3_trial_count=6,
            indication_total_trial_count=80,
        )
        assert "indication-level competition (target-agnostic)" in txt.lower()
        assert "4 approved drug" in txt
        assert "25/80 active trial" in txt
        assert "6 in phase 3" in txt.lower()
        assert "never call the competitive field" in txt.lower()
        assert "decided from the programmes against the target" in txt.lower()
        assert "verdict: the indication is contested" not in txt.lower()


class TestNoSentenceToCopy:
    """The block gave the model a ready-to-quote sentence ("<indication> itself is
    contested …"), which it copied as the competitive axis's rationale and set the axis
    unfavourable for every treated indication; the axis is now computed instead."""

    _COUNTS = dict(
        indication_approved_drug_count=1,
        indication_active_trial_count=38,
        indication_phase3_trial_count=2,
        indication_total_trial_count=60,
    )

    def test_the_landscape_block_states_counts_and_the_wording_rule_only(self):
        txt = interpret_competitive_landscape(
            0, 0, 0, 0, gene="GENE-A", disease="disease a", **self._COUNTS
        )
        assert "1 approved drug(s) and 38/60 active trial(s)" in txt
        assert "itself is contested" not in txt
        assert "in this form" not in txt
        assert "'underserved'" in txt  # the wording rule stays

    def test_original_trpc6_output_is_still_flagged(self):
        text = (
            "The competitive field is underserved with no approved or clinical-stage drugs "
            "targeting this gene, though there are 38 active trials in Focal Segmental "
            "Glomerulosclerosis."
        )
        out = apply_commercial_guards(
            text, indication_approved_drug_count=1, indication_active_trial_count=38, gene="TRPC6"
        )
        assert "demonstrably contested" in out


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

    def test_indication_counts_cited_as_direct_contradiction(self):
        text = "The competitive field appears underserved for this indication."
        out = apply_commercial_guards(
            text, indication_approved_drug_count=3, indication_active_trial_count=40
        )
        assert "COMMERCIAL GUARD" in out
        assert "3 approved drug" in out
        assert "40 active trial" in out
        assert "demonstrably" in out.lower()

    def test_zero_indication_counts_falls_back_to_generic_caution(self):
        text = "The competitive field appears underserved for this indication."
        out = apply_commercial_guards(
            text, indication_approved_drug_count=0, indication_active_trial_count=0
        )
        assert "COMMERCIAL GUARD" in out
        assert "demonstrably" not in out.lower()
        assert "indication-level whitespace" in out.lower()


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

    def test_sized_from_gbd_phrasing_not_flagged(self):
        # Same source-scoped exemption, for GBD's whole-population prevalence.
        text = (
            "The addressable population could not be sized from GBD; external "
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


def _drug(name, *, phase=3.0, approved=False, status="", action="INHIBITOR"):
    return {
        "drug_name": name,
        "drug_type": "Small molecule",
        "max_phase": phase,
        "is_approved": approved,
        "action_type": action,
        "trial_status": status,
    }


class TestTargetProgrammes:
    def test_one_line_per_drug_however_many_trials_name_it(self):
        """447 trial lines overflowed the commercial prompt (PCSK9)."""
        trials = [
            TargetTrial(f"NCT{i:08d}", "PHASE3", "COMPLETED", ("DRUGA 140 mg",)) for i in range(300)
        ]
        lines = target_programmes([_drug("DRUGA", approved=True)], trials, "inhibit")
        assert len(lines) == 1
        assert lines[0].startswith(
            "DRUGA (small molecule, inhibitor, same direction as requested): approved"
        )
        assert "300 retrieved trial(s) name it" in lines[0]

    def test_a_programme_with_no_running_trial_is_precedent_not_competition(self):
        """Terminated phase 3 programmes were counted as a crowded field (BACE1, CETP)."""
        trials = [TargetTrial("NCT1", "PHASE3", "TERMINATED", ("drugb",))]
        [line] = target_programmes([_drug("DRUGB")], trials, "inhibit")
        assert "no trial running and not approved" in line
        assert "not a current competitor" in line
        assert "1 stopped)" in line

    def test_a_running_trial_keeps_a_programme_active(self):
        trials = [TargetTrial("NCT1", "PHASE2", "RECRUITING", ("DrugC",))]
        [line] = target_programmes([_drug("DRUGC", phase=2.0)], trials, "inhibit")
        assert "in the clinic" in line

    def test_opposite_direction_drugs_are_marked(self):
        [line] = target_programmes([_drug("DRUGD", approved=True)], [], "activate")
        assert "OPPOSITE direction" in line

    def test_trials_without_a_known_drug_are_summarised_furthest_first(self):
        trials = [
            TargetTrial("NCT1", "PHASE1", "COMPLETED", ("X-1",)),
            TargetTrial("NCT2", "PHASE3", "RECRUITING", ("Y-2", "Placebo")),
        ]
        lines = target_programmes([], trials, "inhibit")
        assert lines[0].startswith(
            "2 other retrieved trial(s) name the target without a known drug (1 running)"
        )
        assert lines[1].strip() == "NCT2 (PHASE3, RECRUITING): Y-2"

    def test_drug_names_match_whole_words_only(self):
        trials = [TargetTrial("NCT1", "PHASE2", "RECRUITING", ("ABCDE-2",))]
        lines = target_programmes([_drug("ABC", phase=1.0)], trials, "inhibit")
        assert "no trial running" in lines[0]

    def test_salt_forms_are_one_programme_and_repeated_trials_count_once(self):
        known = [_drug("DRUGE SODIUM", approved=True), _drug("DRUGE", approved=True)]
        trials = [TargetTrial("NCT1", "PHASE3", "RECRUITING", ("drugE sodium",))] * 2
        [line] = target_programmes(known, trials, "inhibit")
        assert line.startswith("DRUGE (")
        assert "1 retrieved trial(s) name it (1 running" in line


class TestApprovalInThisIndication:
    """A drug approved for another disease is not a competitor in this indication."""

    @staticmethod
    def _here(row: dict) -> bool:
        return row.get("disease_name") == "disease one" and bool(row.get("is_approved"))

    def test_approved_elsewhere_with_nothing_running_is_precedent(self):
        drug = {**_drug("DRUGE", phase=4.0, approved=True), "disease_name": "disease two"}
        [line] = target_programmes([drug], [], "inhibit", in_indication=self._here)
        assert "approved for other indications, not this one" in line
        assert "not a competitor in this indication" in line

    def test_approved_here_is_said_so(self):
        drugs = [
            {**_drug("DRUGF", phase=4.0, approved=True), "disease_name": "disease two"},
            {**_drug("DRUGF", phase=4.0, approved=True), "disease_name": "disease one"},
        ]
        [line] = target_programmes(drugs, [], "inhibit", in_indication=self._here)
        assert "approved in this indication" in line

    def test_without_an_indication_test_the_old_wording_stays(self):
        [line] = target_programmes([_drug("DRUGG", approved=True)], [], "inhibit")
        assert ": approved" in line and "indication" not in line


class TestCompetitivePosition:
    """competitive_opportunity by rule; synthetic programme mixes and indication counts."""

    @staticmethod
    def _position(**kw):
        from services.evidence.commercial_interpret import competitive_position

        base = dict(
            precedent_status="untested",
            programme_outcomes=[],
            indication_approved_drug_count=0,
        )
        return competitive_position(**{**base, **kw})

    def test_an_approved_same_direction_drug_makes_a_follower(self):
        p = self._position(precedent_status="validated", indication_approved_drug_count=5)
        assert (p.value, p.case) == (None, "follower")

    def test_an_opposite_direction_market_or_an_abandoned_class_is_unfavourable(self):
        """PCSK9 · activate (only inhibitors developed) and BACE1 (every programme failed,
        none running) were neutral: the lens could not oppose. Both are product facts the
        score counts once, through its gates."""
        opposite = self._position(precedent_status="opposed", indication_approved_drug_count=2)
        assert (opposite.value, opposite.case) == (False, "opposite_only")
        abandoned = self._position(
            precedent_status="failed", programme_outcomes=["failed_efficacy", "failed_safety"]
        )
        assert (abandoned.value, abandoned.case) == (False, "abandoned")
        assert opposite.reports_precedent and abandoned.reports_precedent

    def test_a_positive_unfiled_programme_keeps_the_class_alive(self):
        p = self._position(
            precedent_status="stalled",
            programme_outcomes=["failed_efficacy", "positive_unfiled"],
            indication_approved_drug_count=3,
        )
        assert (p.value, p.case) == (None, "treated")

    def test_a_failed_precedent_with_a_running_programme_is_a_race(self):
        p = self._position(
            precedent_status="failed", programme_outcomes=["failed_efficacy", "ongoing"]
        )
        assert (p.value, p.case) == (None, "race")

    def test_a_treated_indication_needs_differentiation_and_an_untreated_one_is_open(self):
        treated = self._position(indication_approved_drug_count=3)
        untreated = self._position(indication_approved_drug_count=0)
        unknown = self._position(indication_approved_drug_count=None)
        assert (treated.value, treated.case) == (None, "treated")
        assert (untreated.value, untreated.case) == (True, "first_in_class")
        assert (unknown.value, unknown.case) == (None, "unknown")
