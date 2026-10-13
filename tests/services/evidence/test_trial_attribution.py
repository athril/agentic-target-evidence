# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Trial attribution: a result figure stated for a trial must come from that trial's
own sources (registry record, or a retrieved publication naming its registry id)."""

from __future__ import annotations

from services.evidence.clinical_trial_interpret import (
    ArmResult,
    TrialFact,
    check_trial_attribution,
    link_trial_publications,
    trial_reported_values,
)


def _fact(rid: str, **kw) -> TrialFact:
    return TrialFact(nct_id=rid, phase="PHASE3", status="COMPLETED", phase_numbers=(3,), **kw)


def test_publications_are_linked_by_any_registry_id() -> None:
    facts = [_fact("NCT01111111"), _fact("ISRCTN22222222"), _fact("NCT03333333")]
    docs = [
        ("paper-1", "Main results … (Funded by X; ClinicalTrials.gov number, NCT01111111.)"),
        ("paper-2", "Secondary analysis of NCT01111111 and ISRCTN22222222."),
        ("paper-3", "A cohort study with no registry id."),
    ]
    linked = {f.nct_id: f.publication_ids for f in link_trial_publications(facts, docs)}
    assert linked == {
        "NCT01111111": ("paper-1", "paper-2"),
        "ISRCTN22222222": ("paper-2",),
        "NCT03333333": (),
    }


def test_reported_values_come_from_the_trial_and_its_publications_only() -> None:
    facts = [
        _fact(
            "NCT01111111",
            enrollment=4200,
            arm_results=(ArmResult(arm="DRUG-X 10 mg", p_value=0.003, p_text="0.003"),),
            publication_ids=("paper-1",),
        ),
        _fact("NCT02222222"),
    ]
    claims = [
        ("paper-1", "DRUG-X reduced the composite endpoint (hazard ratio 0.79)."),
        ("reg-1", "NCT01111111 primary outcome: HR 0.80 (0.70 to 0.91)."),
        ("paper-9", "DRUG-X lowered the biomarker by 55%."),  # unrelated study
    ]
    values = trial_reported_values(facts, claims)
    assert {0.79, 0.80, 0.70, 0.91, 0.003, 4200.0, 10.0} <= set(values["NCT01111111"])
    assert 55.0 not in values["NCT01111111"]
    assert values["NCT02222222"] == ()


def test_check_flags_a_figure_from_another_source() -> None:
    values = {"NCT01111111": (0.70, 0.79, 0.80, 0.91, 4200.0)}
    bad = check_trial_attribution(
        "NCT01111111 lowered the biomarker by 55% and the composite endpoint by 21%.", values
    )
    assert len(bad) == 1 and "55%" in bad[0].note and "21%" not in bad[0].note
    # 21% is the hazard ratio 0.79 as a relative reduction — derived, so grounded.


def test_check_ignores_unverifiable_trials_and_unattributed_figures() -> None:
    values = {"NCT01111111": (0.79,), "NCT02222222": ()}
    assert check_trial_attribution("NCT02222222 cut events by 40%.", values) == []
    assert check_trial_attribution("The drug cut events by 40%.", values) == []
    # counts, doses and phases are not result figures
    assert (
        check_trial_attribution("NCT01111111 randomised 4 arms at 10 mg in phase 3.", values) == []
    )


def test_diagnostic_studies_are_not_counted_or_ranked_as_therapy_trials() -> None:
    from services.evidence.clinical_trial_interpret import (
        build_trial_facts,
        key_trials_prompt,
        trial_count_prompt,
        trial_phase_counts,
    )

    class _Ev:
        def __init__(self, source: str, purpose: str, phase: str = "PHASE2") -> None:
            self.source = source
            self.extra = {
                "phase": phase,
                "status": "COMPLETED",
                "title": f"study {source}",
                "study_plan": {"primary_purpose": purpose},
            }

    facts = build_trial_facts(
        [_Ev("NCT01111111", "TREATMENT"), _Ev("NCT02222222", "DIAGNOSTIC"), _Ev("NCT03333333", "")]
    )
    assert [f.is_therapeutic for f in facts] == [True, False, True]
    assert trial_phase_counts(facts)[2] == 2
    assert "NCT02222222" not in key_trials_prompt(facts)
    prompt = trial_count_prompt(facts)
    assert "two Phase 2 trials" in prompt
    assert "not counted: 1 diagnostic/screening study" in prompt and "NCT02222222" in prompt
