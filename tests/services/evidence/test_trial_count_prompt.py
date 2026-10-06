# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-phase trial counts handed to the clinical lens to quote verbatim."""

from __future__ import annotations

from services.evidence.clinical_trial_interpret import (
    TrialFact,
    check_clinical_phase_guard,
    normalize_phase,
    trial_count_phrases,
    trial_count_prompt,
)
from services.evidence.readings import check_quantity_wording, trial_count_readings


def _fact(nct: str, phase: str, status: str) -> TrialFact:
    return TrialFact(nct_id=nct, phase=phase, status=status, phase_numbers=normalize_phase(phase))


def test_prompt_lists_each_phase_count_and_the_missing_phases() -> None:
    prompt = trial_count_prompt(
        [_fact("NCT05213624", "PHASE2", "COMPLETED"), _fact("NCT07220083", "PHASE3", "RECRUITING")]
    )
    assert "• one Phase 2 trial (NCT05213624, completed)" in prompt
    assert "• one Phase 3 trial (NCT07220083, recruiting)" in prompt
    assert "• no Phase 1 or Phase 4 trial retrieved" in prompt
    assert "multiple Phase 2 and Phase 3 trials" in prompt  # named as the forbidden pattern


def test_counts_above_one_are_spelled_and_pluralised() -> None:
    facts = [
        _fact("NCT00000001", "PHASE3", "COMPLETED"),
        _fact("NCT00000002", "PHASE3", "TERMINATED"),
    ]
    assert trial_count_phrases(facts) == [
        "two Phase 3 trials (NCT00000001, completed; NCT00000002, terminated)"
    ]


def test_multi_phase_trial_counts_in_each_phase() -> None:
    assert trial_count_phrases([_fact("NCT00000003", "PHASE2|PHASE3", "RECRUITING")]) == [
        "one Phase 2 trial (NCT00000003, recruiting)",
        "one Phase 3 trial (NCT00000003, recruiting)",
    ]


def test_active_not_recruiting_is_not_worded_as_recruiting() -> None:
    facts = [_fact("NCT00000004", "PHASE2", "ACTIVE_NOT_RECRUITING")]
    phrase = trial_count_phrases(facts)[0]
    assert phrase == "one Phase 2 trial (NCT00000004, active, enrollment closed)"
    assert not check_clinical_phase_guard(f"The drug has {phrase}.", facts)


def test_quoted_phrases_pass_both_trial_guards() -> None:
    facts = [
        _fact("NCT00000001", "PHASE1", "COMPLETED"),
        _fact("NCT00000002", "PHASE2", "NOT_YET_RECRUITING"),
        _fact("NCT00000003", "PHASE2", "COMPLETED"),
        _fact("NCT00000004", "PHASE3", "RECRUITING"),
    ]
    text = f"The target has {'; '.join(trial_count_phrases(facts))}."
    counts = {1: 1, 2: 2, 3: 1, 4: 0}
    assert not check_clinical_phase_guard(text, facts)
    assert not check_quantity_wording(text, trial_count_readings(counts, {}))


def test_unphased_trials_produce_no_prompt() -> None:
    assert trial_count_prompt([_fact("NCT00000005", "NA", "COMPLETED")]) == ""
    assert trial_count_prompt([]) == ""
