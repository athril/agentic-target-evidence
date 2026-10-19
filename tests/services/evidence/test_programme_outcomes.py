# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Programme outcomes (services/evidence/programme_outcomes.py).

Synthetic known-drug rows in the shape of ``KnownDrug.model_dump()`` with ``stop_reports``;
the molecule names are placeholders, so the cases test the rules, not any one target. The
"recorded" cases reproduce the shape of the Open Targets rows of the benchmark panel
(2026-10-16): a secretase inhibitor class that failed with no safety signal, a transfer-
protein inhibitor class with mixed outcomes, and a single futility-stopped phase 2.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.evidence.programme_outcomes import (
    Programme,
    ProgrammeConfig,
    ProgrammeGate,
    classify_programmes,
    falsification_strength,
    load_programme_config,
    noisy_or,
    programme_contribution,
    render_programme_block,
    report_classes,
    select_gate,
    with_strength,
)

_SCOPE = frozenset({"MONDO_1", "MONDO_1A"})
_CFG = load_programme_config()


def _report(
    stage: float,
    status: str = "COMPLETED",
    *,
    categories: list[str] | None = None,
    why: str = "",
    trial: str = "nct0",
) -> dict[str, Any]:
    return {
        "trial_id": trial,
        "stage": stage,
        "status": status,
        "categories": categories or [],
        "why": why,
    }


def _row(
    drug: str,
    reports: list[dict[str, Any]],
    *,
    action: str = "INHIBITOR",
    approved_here: bool = False,
    disease_id: str = "MONDO_1",
    disease: str = "disease one",
) -> dict[str, Any]:
    stage = max((r["stage"] for r in reports), default=0.0)
    return {
        "drug_name": drug,
        "action_type": action,
        "max_phase": 4.0 if approved_here else stage,
        "disease_id": disease_id,
        "disease_name": disease,
        "approved_in_indication": approved_here,
        "is_approved": approved_here,
        "trial_status": reports[0]["status"] if reports else "",
        "stopped_for_cause": [],
        "stop_reports": reports,
    }


def _fact(
    nct: str, drug: str, *, p: float | None, phase: int = 3, in_indication: bool | None = True
) -> dict[str, Any]:
    return {
        "nct_id": nct,
        "phase_numbers": [phase],
        "in_indication": in_indication,
        "matched_by": f"drug:{drug}",
        "interventions": [drug, "Placebo"],
        "arm_results": [] if p is None else [{"arm": drug, "p_value": p, "p_text": str(p)}],
    }


def _read(
    rows: list[dict[str, Any]],
    facts: list[dict[str, Any]] | None = None,
    direction: str = "inhibit",
) -> Any:
    return classify_programmes(
        rows,
        facts or [],
        direction=direction,
        disease="disease one",
        scope_ids=_SCOPE,
        config=_CFG,
    )


def _outcome(rows: list[dict[str, Any]], facts: list[dict[str, Any]] | None = None) -> str:
    reading = _read(rows, facts)
    assert len(reading.programmes) == 1
    return str(reading.programmes[0].outcome)


# ------------------------------------------------------------ stop-reason vocabulary


def test_every_category_the_live_api_returned_is_classified() -> None:
    """A category missing from the config reads as unknown: it must not be silent drift."""
    live = {
        "Negative",
        "Safety_Sideeffects",
        "Business_Administrative",
        "Logistics_Resources",
        "Study_Staff_Moved",
        "Another_Study",
        "Covid19",
        "Regulatory",
        "Insufficient_Enrollment",
        "Study_Design",
        "Invalid_Reason",
        "Uncategorised",
        "No_Context",
    }
    assert live <= set(_CFG.stop_classes)


def test_only_negative_and_safety_categories_count_against_the_mechanism() -> None:
    failing = {c for c, k in _CFG.stop_classes.items() if k in ("efficacy", "safety")}
    assert failing == {"Negative", "Safety_Sideeffects"}


def test_an_unlisted_category_never_counts_as_a_failure() -> None:
    r = _report(3, "TERMINATED", categories=["Brand_New_Category"])
    assert report_classes(r, _CFG.stop_classes) == {"unknown"}


def test_a_completed_trial_has_no_stop_class() -> None:
    assert report_classes(_report(3, "COMPLETED"), _CFG.stop_classes) == frozenset()


def test_a_stopped_trial_without_a_category_is_stopped_for_an_unknown_reason() -> None:
    assert report_classes(_report(3, "TERMINATED"), _CFG.stop_classes) == {"unknown"}


# ------------------------------------------------------------------ outcome table


def test_approved_in_the_indication() -> None:
    assert _outcome([_row("drug a", [_report(4, trial="nct1")], approved_here=True)]) == "approved"


def test_approval_outranks_an_old_failed_trial() -> None:
    reports = [_report(2, "TERMINATED", categories=["Negative"]), _report(4)]
    assert _outcome([_row("drug a", reports, approved_here=True)]) == "approved"


def test_a_running_trial_makes_the_programme_ongoing() -> None:
    """A programme with a running phase 3 is not a failure yet, even after an earlier stop."""
    reports = [_report(2, "TERMINATED", categories=["Negative"]), _report(3, "RECRUITING")]
    assert _outcome([_row("drug a", reports)]) == "ongoing"


def test_a_phase_3_that_met_its_endpoint_is_positive_not_a_failure() -> None:
    """A transfer-protein inhibitor met its primary endpoint and was never filed."""
    rows = [_row("drug a", [_report(3, trial="nct5")])]
    assert _outcome(rows, [_fact("nct5", "DRUG A", p=0.004)]) == "positive_unfiled"


def test_positive_beats_failure_reports_of_the_same_molecule() -> None:
    reports = [_report(3, trial="nct5"), _report(2, "TERMINATED", categories=["Negative"])]
    assert _outcome([_row("drug a", reports)], [_fact("nct5", "DRUG A", p=0.01)]) == (
        "positive_unfiled"
    )


@pytest.mark.parametrize(
    ("p", "phase", "in_indication"),
    [(0.3, 3, True), (0.01, 2, True), (0.01, 3, False), (0.01, 3, None)],
)
def test_a_positive_needs_a_significant_phase_3_in_the_indication(
    p: float, phase: int, in_indication: bool | None
) -> None:
    rows = [_row("drug a", [_report(3, trial="nct5")])]
    facts = [_fact("nct5", "DRUG A", p=p, phase=phase, in_indication=in_indication)]
    assert _outcome(rows, facts) != "positive_unfiled"


def test_a_significant_trial_of_another_drug_is_not_this_drugs_endpoint() -> None:
    rows = [_row("drug a", [_report(3, trial="nct5")])]
    assert _outcome(rows, [_fact("nct9", "OTHER DRUG", p=0.001)]) != "positive_unfiled"


def test_a_trial_stopped_for_harm_is_a_safety_failure() -> None:
    reports = [_report(3, "TERMINATED", categories=["Safety_Sideeffects"], why="liver injury")]
    reading = _read([_row("drug a", reports)])
    assert reading.programmes[0].outcome == "failed_safety"
    assert reading.programmes[0].reasons == ["liver injury"]


def test_a_trial_stopped_for_futility_is_an_efficacy_failure() -> None:
    reports = [_report(3, "TERMINATED", categories=["Negative"], why="futility", trial="nct7")]
    reading = _read([_row("drug a", reports)])
    assert reading.programmes[0].outcome == "failed_efficacy"
    assert reading.programmes[0].trials == ["nct7"]
    assert reading.programmes[0].failure_stage == 3.0


def test_harm_outranks_futility_for_the_same_molecule() -> None:
    reports = [
        _report(2, "TERMINATED", categories=["Negative"]),
        _report(3, "TERMINATED", categories=["Safety_Sideeffects"]),
    ]
    assert _outcome([_row("drug a", reports)]) == "failed_safety"


@pytest.mark.parametrize(
    "category", ["Business_Administrative", "Insufficient_Enrollment", "Study_Design"]
)
def test_stops_that_say_nothing_about_the_mechanism_are_not_failures(category: str) -> None:
    """A sponsor ending a phase 3 for funding or enrolment is not evidence against the target."""
    reports = [_report(3, "TERMINATED", categories=[category])]
    assert _outcome([_row("drug a", reports)]) == "discontinued_business"


def test_a_business_stop_next_to_an_unexplained_stop_is_not_business() -> None:
    reports = [
        _report(2, "TERMINATED", categories=["Business_Administrative"]),
        _report(3, "TERMINATED"),
    ]
    assert _outcome([_row("drug a", reports)]) == "stalled_unexplained"


def test_phase_3_that_ended_without_a_reason_is_stalled_unexplained() -> None:
    assert _outcome([_row("drug a", [_report(3, "COMPLETED")])]) == "stalled_unexplained"


def test_a_terminated_phase_2_3_without_a_reason_is_stalled_not_early() -> None:
    """The registry gives most old terminated trials no reason: a secretase inhibitor's
    phase 2/3, stopped for futility, read as early-stage because it was not 'completed'."""
    reports = [_report(3, "", trial="pipeline"), _report(2.5, "TERMINATED", trial="nct1")]
    assert _outcome([_row("drug a", reports)]) == "stalled_unexplained"


def test_a_phase_2_programme_without_a_reason_is_early() -> None:
    assert _outcome([_row("drug a", [_report(2, "COMPLETED")])]) == "early"


def test_only_same_direction_molecules_are_programmes() -> None:
    rows = [
        _row("drug a", [_report(3, "TERMINATED", categories=["Negative"])]),
        _row("drug b", [_report(3, "TERMINATED", categories=["Negative"])], action="AGONIST"),
        _row("drug c", [_report(3, "TERMINATED", categories=["Negative"])], action="MODULATOR"),
    ]
    assert [p.drug for p in _read(rows).programmes] == ["DRUG A"]


def test_other_indications_are_not_this_indications_programmes() -> None:
    rows = [
        _row(
            "drug a",
            [_report(3, "TERMINATED", categories=["Negative"])],
            disease_id="MONDO_9",
            disease="a different disorder",
        )
    ]
    reading = _read(rows)
    assert reading.programmes == [] and reading.falsification == 0.0


def test_salt_forms_are_one_molecule() -> None:
    rows = [
        _row("drug a sodium", [_report(3, "TERMINATED", categories=["Negative"], trial="n1")]),
        _row("drug a", [_report(3, "TERMINATED", categories=["Negative"], trial="n2")]),
    ]
    assert len(_read(rows).programmes) == 1


def test_one_molecule_failing_in_two_trials_counts_once() -> None:
    reports = [
        _report(3, "TERMINATED", categories=["Negative"], trial="n1"),
        _report(3, "TERMINATED", categories=["Negative"], trial="n2"),
    ]
    assert _read([_row("drug a", reports)]).falsification == pytest.approx(0.3)


def test_the_requested_direction_picks_the_programmes() -> None:
    rows = [_row("drug b", [_report(3, "TERMINATED", categories=["Negative"])], action="AGONIST")]
    assert _read(rows, direction="inhibit").programmes == []
    assert len(_read(rows, direction="activate").programmes) == 1


def test_an_undefined_direction_classifies_nothing() -> None:
    rows = [_row("drug a", [_report(3, "TERMINATED", categories=["Negative"])])]
    reading = _read(rows, direction="modulate")
    assert reading.programmes == [] and reading.falsification == 0.0


def test_rows_without_stop_reports_are_unavailable_not_zero() -> None:
    """A cached Open Targets row from before stop reports must not read as 'no failures'."""
    row = _row("drug a", [_report(3, "TERMINATED", categories=["Negative"])])
    del row["stop_reports"]
    reading = _read([row])
    assert reading.available is False


# ------------------------------------------------------------------- strength


def _p(outcome: str, stage: float, engagement: str = "not_assessed") -> Programme:
    return Programme(
        drug=f"D{outcome}{stage}{engagement}",
        effect="decrease",
        stage=stage,
        outcome=outcome,  # type: ignore[arg-type]
        failure_stage=stage,
        engagement=engagement,  # type: ignore[arg-type]
    )


def test_noisy_or() -> None:
    assert noisy_or([]) == 0.0
    assert noisy_or([0.5, 0.5]) == pytest.approx(0.75)
    assert noisy_or([1.5, -1.0]) == pytest.approx(1.0)  # clamped


@pytest.mark.parametrize(
    ("outcome", "stage", "expected"),
    [
        ("failed_efficacy", 3.0, 0.5 * 0.6),
        ("failed_efficacy", 2.0, 0.3 * 0.6),
        ("failed_efficacy", 2.5, 0.3 * 0.6),  # a seamless phase 2/3 counts as phase 2
        ("failed_efficacy", 1.0, 0.1 * 0.6),
        ("failed_efficacy", 0.0, 0.0),
        ("failed_safety", 3.0, 0.35 * 0.6),
        ("failed_safety", 2.0, 0.25 * 0.6),
        ("stalled_unexplained", 3.0, 0.3 * 0.6),
        ("approved", 4.0, 0.0),
        ("ongoing", 3.0, 0.0),
        ("discontinued_business", 3.0, 0.0),
        ("positive_unfiled", 3.0, 0.0),
    ],
)
def test_a_programmes_contribution_follows_outcome_and_phase(
    outcome: str, stage: float, expected: float
) -> None:
    assert programme_contribution(_p(outcome, stage), _CFG) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("engagement", "factor"),
    [("shown", 1.0), ("not_reported", 0.6), ("not_assessed", 0.6), ("not_shown", 0.3)],
)
def test_target_engagement_scales_an_efficacy_failure(engagement: str, factor: float) -> None:
    """A drug that never moved its target's marker says little about the mechanism."""
    got = programme_contribution(_p("failed_efficacy", 3.0, engagement), _CFG)
    assert got == pytest.approx(0.5 * factor)


def test_safety_failures_ignore_the_engagement_answer() -> None:
    """On-target and off-target harm cannot be told apart from the registry."""
    shown = programme_contribution(_p("failed_safety", 3.0, "shown"), _CFG)
    not_shown = programme_contribution(_p("failed_safety", 3.0, "not_shown"), _CFG)
    assert shown == not_shown == pytest.approx(0.35 * 0.6)


def test_strength_combines_molecules_as_a_noisy_or() -> None:
    items = [_p("failed_efficacy", 3.0, "shown"), _p("failed_efficacy", 2.0, "shown")]
    assert falsification_strength(items, _CFG) == pytest.approx(1 - 0.5 * 0.7)


def test_a_positive_unfiled_programme_offsets_the_failures() -> None:
    failures = [_p("failed_efficacy", 3.0, "shown"), _p("failed_efficacy", 3.0, "not_shown")]
    base = falsification_strength(failures, _CFG)
    relieved = falsification_strength([*failures, _p("positive_unfiled", 3.0)], _CFG)
    assert relieved == pytest.approx(base * 0.7, abs=1e-3)
    twice = falsification_strength(
        [*failures, _p("positive_unfiled", 3.0), _p("positive_unfiled", 2.9)], _CFG
    )
    assert twice == pytest.approx(base * 0.7 * 0.7, abs=1e-3)


def test_an_approval_in_the_indication_ends_the_falsification() -> None:
    """Two approved degraders beside one failed sibling read "falsification 0.30 —
    programmes against the target stalled or failed"; the approval shows the mechanism
    works, so the sibling's failure is its own."""
    failures = [_p("failed_efficacy", 3.0, "not_reported"), _p("stalled_unexplained", 3.0)]
    assert falsification_strength(failures, _CFG) > 0.3
    assert falsification_strength([*failures, _p("approved", 4.0)], _CFG) == 0.0
    partial = ProgrammeConfig(weights=_CFG.weights, approved_relief=0.5)
    assert falsification_strength([*failures, _p("approved", 4.0)], partial) == pytest.approx(
        falsification_strength(failures, partial) * 0.5, abs=1e-3
    )


def test_no_failures_means_no_strength_even_with_relief() -> None:
    assert falsification_strength([_p("positive_unfiled", 3.0)], _CFG) == 0.0


def test_relief_and_weights_come_from_config() -> None:
    cfg = ProgrammeConfig(
        weights={"failed_efficacy": {"phase3": 1.0}},
        engagement_factor={"not_assessed": 1.0},
        positive_unfiled_relief=0.5,
    )
    items = [_p("failed_efficacy", 3.0), _p("positive_unfiled", 3.0)]
    assert falsification_strength(items, cfg) == pytest.approx(0.5)


# ----------------------------------------------------------------------- gates


def test_gate_selection_takes_the_highest_gate_reached() -> None:
    gates = [
        ProgrammeGate(min_strength=0.3, max=59, reason="weak"),
        ProgrammeGate(min_strength=0.6, max=44, reason="strong"),
    ]
    assert select_gate(0.29, gates) is None
    assert select_gate(0.3, gates).max == 59  # type: ignore[union-attr]
    assert select_gate(0.59, gates).max == 59  # type: ignore[union-attr]
    assert select_gate(0.6, gates).max == 44  # type: ignore[union-attr]
    assert select_gate(1.0, gates).max == 44  # type: ignore[union-attr]


def test_repo_gates_are_the_documented_ones() -> None:
    assert [(g.min_strength, g.max) for g in sorted(_CFG.gates, key=lambda g: g.min_strength)] == [
        (0.3, 59),
        (0.6, 44),
    ]


# ------------------------------------------------- recorded rows of the panel runs


def _bace_like() -> list[dict[str, Any]]:
    """Four molecules of one class: two phase 3 futility stops, a phase 2/3 stopped for
    benefit-risk, and a phase 3 programme whose registry entries carry no reason."""
    return [
        _row(
            "class drug 1",
            [_report(3, "TERMINATED", categories=["Negative"], why="no evidence of efficacy")],
        ),
        _row(
            "class drug 2",
            [_report(3, "TERMINATED", categories=["Negative"], why="not likely to meet endpoint")],
        ),
        _row(
            "class drug 3",
            [_report(2.5, "TERMINATED", categories=["Negative"], why="benefit-risk profile")],
        ),
        _row("class drug 4", [_report(3, ""), _report(2.5, "TERMINATED", trial="nct1")]),
    ]


def test_a_class_that_failed_in_several_phase_3_programmes_reaches_the_strong_gate() -> None:
    """Secretase-inhibitor-like: four molecules, none with a safety stop or an approval."""
    reading = _read(_bace_like())
    assert reading.falsification >= 0.6
    assert reading.gate is not None and reading.gate.max == 44
    assert "failed for lack of efficacy" in reading.summary


def test_mixed_class_outcomes_land_between_the_gates() -> None:
    """Transfer-protein-inhibitor-like: two unexplained phase 3 stops and a business stop."""
    rows = [
        _row("class drug 1", [_report(3, "COMPLETED", trial="n1")]),
        _row("class drug 2", [_report(3, "COMPLETED", trial="n2")]),
        _row(
            "class drug 3",
            [_report(3, "TERMINATED", categories=["Business_Administrative"], trial="n3")],
        ),
    ]
    reading = _read(rows)
    assert 0.3 <= reading.falsification < 0.6
    assert reading.gate is not None and reading.gate.max == 59


def test_a_single_futility_stopped_phase_2_does_not_bound_the_score() -> None:
    """An antibody class with one phase 2 stopped for futility in a neighbouring disease."""
    rows = [_row("drug a", [_report(2, "TERMINATED", categories=["Negative"])])]
    reading = _read(rows)
    assert reading.falsification < 0.3 and reading.gate is None


def test_one_unexplained_phase_3_does_not_bound_the_score() -> None:
    reading = _read([_row("drug a", [_report(3, "COMPLETED")])])
    assert reading.falsification < 0.3 and reading.gate is None


# ------------------------------------------------------------------------ text


def test_summary_names_the_outcomes_and_the_strength() -> None:
    reading = _read(_bace_like())
    assert "Same-direction programmes in disease one" in reading.summary
    assert f"{reading.falsification:.2f}" in reading.summary


def test_prompt_block_lists_each_programme_and_the_rule() -> None:
    block = render_programme_block(_read(_bace_like()))
    assert "class drug 1" in block and "failed for lack of efficacy" in block
    assert "never call it a failure" in block


def test_programme_table_lists_each_molecules_trials_with_their_results() -> None:
    rows = [_row("DRUG-A", [_report(3, trial="nct00000001")])]
    outcome_trial = _hard_fact(
        "NCT00000001", "DRUG-A", primary_p=0.002, hard_not_met=True, deaths=(138, 439, 40, 222)
    )
    outcome_trial.update(status="COMPLETED", enrollment=666)
    running = _fact("NCT00000002", "DRUG-A", p=None, phase=2)
    running["status"] = "RECRUITING"
    facts = [outcome_trial, running]

    block = render_programme_block(_read(rows, facts), facts)

    assert (
        "NCT00000001 — phase 3, completed, 666 enrolled; primary: met (p 0.002); hard "
        "endpoints: Overall survival not met; more deaths on the drug arms (138/439 vs 40/222"
    ) in block
    assert "NCT00000002 — phase 2, recruiting; primary: not posted" in block
    assert "has no result to report" in block


def test_programme_table_keeps_trials_with_their_own_molecule_and_indication() -> None:
    rows = [_row("DRUG-A", [_report(3, trial="nct00000003")])]
    other_drug = _fact("NCT00000001", "DRUG-B", p=0.01)
    elsewhere = _fact("NCT00000002", "DRUG-A", p=0.01, in_indication=False)

    block = render_programme_block(_read(rows, [other_drug, elsewhere]), [other_drug, elsewhere])

    assert "NCT00000001" not in block and "NCT00000002" not in block
    # An Open Targets trial id with no retrieved registry record is still named.
    assert "also on record (no registry details retrieved): NCT00000003" in block


def test_a_posted_primary_without_a_comparison_is_not_called_unposted() -> None:
    rows = [_row("DRUG-A", [_report(3, trial="nct1")])]
    fact = _fact("NCT00000001", "DRUG-A", p=None)
    fact["primary_outcomes"] = ["Objective response"]

    assert "primary: posted without a comparison" in render_programme_block(
        _read(rows, [fact]), [fact]
    )


def test_prompt_block_is_empty_without_programmes() -> None:
    assert render_programme_block(_read([])) == ""


def test_prompt_block_is_empty_when_unavailable() -> None:
    row = _row("drug a", [_report(3, "TERMINATED", categories=["Negative"])])
    del row["stop_reports"]
    assert render_programme_block(_read([row])) == ""


def test_with_strength_recomputes_after_the_engagement_changes() -> None:
    reading = _read([_row("drug a", [_report(3, "TERMINATED", categories=["Negative"])])])
    assert reading.falsification == pytest.approx(0.3)
    shown = reading.model_copy(
        update={"programmes": [reading.programmes[0].model_copy(update={"engagement": "shown"})]}
    )
    assert with_strength(shown, _CFG, "disease one").falsification == pytest.approx(0.5)


# ------------------------------------------------------------------ the report table


def test_report_table_lists_each_molecule_with_what_it_counted() -> None:
    from agents.synthesis.report.agent import _programme_table

    reading = _read(_bace_like()).model_dump(mode="json")
    table = _programme_table(reading)
    assert "Falsification strength **0.67**" in table
    assert (
        "| class drug 1 | phase 3 | failed — efficacy (target engagement: not assessed) | 0.30 |"
        in table
    )
    assert "reached phase 3, no result or reason on record" in table


def test_report_table_is_empty_without_a_usable_reading() -> None:
    from agents.synthesis.report.agent import _programme_table

    assert _programme_table(None) == ""
    assert _programme_table(_read([]).model_dump(mode="json")) == ""


# ------------------------------------------------------------ hard endpoints and deaths


def _hard_fact(
    nct: str,
    drug: str,
    *,
    primary_p: float | None,
    hard_not_met: bool | None = None,
    deaths: tuple[int, int, int, int] | None = None,
    phase: int = 3,
) -> dict[str, Any]:
    from services.evidence.clinical_trial_interpret import death_imbalance

    fact = _fact(nct, drug, p=primary_p, phase=phase)
    if hard_not_met is not None:
        fact["hard_endpoints"] = [
            {"measure": "Overall survival", "not_met": hard_not_met, "p_text": "0.76"}
        ]
    if deaths:
        imbalance = death_imbalance(
            [
                {"group": drug, "deaths": deaths[0], "deaths_at_risk": deaths[1]},
                {"group": "Placebo", "deaths": deaths[2], "deaths_at_risk": deaths[3]},
            ]
        )
        fact["death_imbalance"] = imbalance.model_dump() if imbalance else None
    return fact


def test_a_surrogate_win_with_a_failed_survival_secondary_is_a_hard_endpoint_failure() -> None:
    """A phase 3 that met its response-rate primary and failed overall survival used to read
    as "ended with no result" (or as met), too weak to bound the score."""
    rows = [_row("DRUG-A", [_report(3, trial="nct1")])]
    facts = [_hard_fact("nct1", "DRUG-A", primary_p=0.002, hard_not_met=True)]

    reading = _read(rows, facts)

    [p] = reading.programmes
    assert p.outcome == "failed_hard_endpoint"
    assert p.failure_stage == 3
    assert p.reasons and "Overall survival not met" in p.reasons[0]
    # An efficacy failure on the decisive endpoint, engagement not yet assessed (0.6).
    assert p.contribution == pytest.approx(0.30)
    assert reading.gate is not None and reading.gate.min_strength == 0.3


def test_excess_deaths_on_the_drug_arm_are_a_hard_endpoint_failure_on_their_own() -> None:
    rows = [_row("DRUG-A", [_report(3, trial="nct1")])]
    facts = [_hard_fact("nct1", "DRUG-A", primary_p=None, deaths=(138, 439, 40, 222))]

    assert _outcome(rows, facts) == "failed_hard_endpoint"


def test_a_met_primary_with_a_significant_or_unanalysed_secondary_stays_positive() -> None:
    rows = [_row("DRUG-A", [_report(3, trial="nct1")])]
    facts = [_hard_fact("nct1", "DRUG-A", primary_p=0.002, deaths=(10, 400, 11, 400))]

    assert _outcome(rows, facts) == "positive_unfiled"


def test_a_met_primary_whose_secondaries_were_never_read_is_not_a_positive() -> None:
    """Results cached before hard-endpoint secondaries were kept (and not refetched) show
    only the primary: a met response rate there read as a positive phase 3, though the
    same trial failed overall survival."""
    rows = [_row("DRUG-A", [_report(3, trial="nct1")])]
    fact = _fact("nct1", "DRUG-A", p=0.002)
    fact["secondaries_read"] = False

    assert _outcome(rows, [fact]) == "stalled_unexplained"


def test_a_hard_endpoint_failure_outside_the_indication_does_not_count() -> None:
    rows = [_row("DRUG-A", [_report(3, trial="nct1")])]
    fact = _hard_fact("nct1", "DRUG-A", primary_p=0.002, hard_not_met=True)
    fact["in_indication"] = False

    assert _outcome(rows, [fact]) != "failed_hard_endpoint"


def test_a_hard_endpoint_failure_is_asked_whether_the_drug_engaged_the_target() -> None:
    from agents.interpretation.clinical_lens.engagement import programmes_to_ask

    rows = [_row("DRUG-A", [_report(3, trial="nct1")])]
    reading = _read(rows, [_hard_fact("nct1", "DRUG-A", primary_p=0.002, hard_not_met=True)])

    assert [p.drug for p in programmes_to_ask(reading)] == ["DRUG-A"]


def test_a_relevant_outcome_trial_that_met_its_primary_links_to_its_programme() -> None:
    rows = [_row("DRUG-A", [_report(3, trial="nct1")])]
    fact = _fact("nct1", "DRUG-A", p=0.004)
    fact["in_indication"] = False
    fact["relevant_to_indication"] = True  # registered under a broader condition

    assert _outcome(rows, [fact]) == "positive_unfiled"
    fact["relevant_to_indication"] = False
    assert _outcome(rows, [fact]) != "positive_unfiled"


def test_a_posted_primary_that_missed_is_an_efficacy_failure() -> None:
    """A completed phase 3 that posted a non-significant primary read as "ended with no
    result or reason on record"."""
    rows = [_row("DRUG-A", [_report(3, trial="nct1")])]

    reading = _read(rows, [_fact("nct1", "DRUG-A", p=0.61)])

    [p] = reading.programmes
    assert p.outcome == "failed_efficacy"
    assert p.failure_stage == 3
    assert "primary endpoint not met" in p.reasons[0]


def test_a_drug_with_only_a_relevant_registry_trial_becomes_a_programme() -> None:
    """A molecule of the target whose Open Targets row is filed under another disease, but
    whose outcome trial measured this indication, was missing from the programmes."""
    elsewhere = _row(
        "DRUG-B", [_report(3, trial="nct9")], disease_id="MONDO_OTHER", disease="other"
    )
    fact = _fact("nct2", "DRUG-B", p=0.80)
    fact["in_indication"] = False
    fact["relevant_to_indication"] = True
    fact["status"] = "TERMINATED"

    reading = _read([elsewhere], [fact])

    assert [(p.drug, p.outcome, p.stage) for p in reading.programmes] == [
        ("DRUG-B", "failed_efficacy", 3.0)
    ]


def test_a_drug_of_another_target_never_becomes_a_programme() -> None:
    fact = _fact("nct3", "DRUG-Z", p=0.8)
    fact["relevant_to_indication"] = True

    assert _read([], [fact]).programmes == []


def test_a_hard_primary_met_beside_a_missed_hard_secondary_is_not_a_failure() -> None:
    """An outcome trial met its major-events primary and missed a stroke secondary; the
    surrogate rule read that as a hard-endpoint failure of a positive programme."""
    rows = [_row("DRUG-A", [_report(3, trial="nct1")])]
    fact = _hard_fact("nct1", "DRUG-A", primary_p=0.004, hard_not_met=True)
    fact["primary_is_hard"] = True

    assert _outcome(rows, [fact]) == "positive_unfiled"
