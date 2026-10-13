# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Trial attribution: a result figure stated for a trial must come from that trial's
own sources (its registry record, or a claim naming its registry id)."""

from __future__ import annotations

from services.evidence.clinical_trial_interpret import (
    TrialFact,
    check_trial_attribution,
    trial_reported_values,
)


def _fact(rid: str, **kw) -> TrialFact:
    return TrialFact(nct_id=rid, phase="PHASE3", status="COMPLETED", phase_numbers=(3,), **kw)


def test_reported_values_come_from_the_trial_and_claims_naming_it_only() -> None:
    facts = [
        _fact("NCT01111111", enrollment=4200),
        _fact("NCT02222222"),
    ]
    claims = [
        ("paper-1", "NCT01111111: DRUG-X reduced the composite endpoint (hazard ratio 0.79)."),
        ("reg-1", "NCT01111111 primary outcome: HR 0.80 (0.70 to 0.91)."),
        ("paper-9", "DRUG-X lowered the biomarker by 55%."),  # unrelated study
    ]
    values = trial_reported_values(facts, claims)
    assert {0.79, 0.80, 0.70, 0.91, 4200.0} <= set(values["NCT01111111"])
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


def test_diagnostic_studies_are_not_counted_as_therapy_trials() -> None:
    from services.evidence.clinical_trial_interpret import (
        build_trial_facts,
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
