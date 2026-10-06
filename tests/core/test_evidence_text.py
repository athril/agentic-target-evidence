# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for core.evidence_text.screenable_text."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from core.evidence_text import is_target_drug_trial, mentions_term, screenable_text
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance


def _make_evidence(evidence_type: EvidenceType, extra: dict) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="TRPC6",
        disease="focal segmental glomerulosclerosis",
        evidence_type=evidence_type,
        scope="abstract",
        source="NCT05213624",
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
