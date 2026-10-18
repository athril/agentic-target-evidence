# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Where the therapeutic-hypothesis precedent acts: Open Targets per-indication stages,
the suitability gates, the clinical/regulatory axis rule, trial indication tagging."""

from __future__ import annotations

import uuid
from typing import Any

from agents.interpretation._lens_precedent import apply_precedent
from capabilities.target_validation.workflow import _current_opentargets_rows
from mcp_servers.opentargets.tools import _per_indication
from schemas.evidence import Direction
from schemas.verdicts import AxisVerdict, LensVerdict
from services.decision.suitability import ScoringConfig, compute_suitability
from services.evidence.clinical_trial_interpret import (
    TrialFact,
    key_trials_prompt,
    mark_indication,
    trial_count_prompt,
)
from services.evidence.programme_outcomes import classify_programmes

# --------------------------------------------------------------- Open Targets rows


def _report(stage: str, diseases: list[str], **kw: Any) -> dict[str, Any]:
    return {
        "id": kw.get("id", "nct0"),
        "clinicalStage": stage,
        "trialOverallStatus": kw.get("status", "COMPLETED"),
        "trialWhyStopped": kw.get("why"),
        "trialStopReasonCategories": kw.get("categories", []),
        "diseases": [{"disease": {"id": d, "name": d.lower()}} for d in diseases],
    }


def test_each_indication_gets_its_own_stage_not_the_drugs_best() -> None:
    """A drug's phase 4 trials in one disease used to read as phase 4 in every disease."""
    row = {
        "maxClinicalStage": "PHASE_4",
        "diseases": [
            {"disease": {"id": "D1", "name": "d1"}},
            {"disease": {"id": "D2", "name": "d2"}},
        ],
        "clinicalReports": [
            _report("PHASE_4", ["D1"]),
            _report("APPROVAL", ["D1"]),
            _report(
                "PHASE_2",
                ["D2"],
                id="nct9",
                status="TERMINATED",
                why="futility criterion was met",
                categories=["Negative"],
            ),
        ],
    }
    out = _per_indication(row)
    assert (out["D1"].stage, out["D1"].approved) == (4.0, True)
    assert (out["D2"].stage, out["D2"].approved, out["D2"].status) == (2.0, False, "TERMINATED")
    assert out["D2"].stopped_for_cause == [
        "nct9 (phase 2, terminated): futility criterion was met [Negative]"
    ]


def test_enrolment_stops_are_not_failures() -> None:
    row = {
        "clinicalReports": [
            _report("PHASE_3", ["D1"], status="TERMINATED", categories=["Insufficient_Enrollment"])
        ]
    }
    assert _per_indication(row)["D1"].stopped_for_cause == []


def test_rows_without_report_diseases_fall_back_to_the_row_stage() -> None:
    row = {
        "maxClinicalStage": "PHASE_3",
        "diseases": [{"disease": {"id": "D1", "name": "d1"}}],
        "clinicalReports": [{"trialOverallStatus": "COMPLETED"}],
    }
    assert _per_indication(row)["D1"].stage == 3.0


# ------------------------------------------------------------------ score gates


def _verdicts(sign: str, conf: float) -> list[dict[str, Any]]:
    lenses = ("genetics", "clinical", "safety", "biology", "regulatory", "commercial")
    return [{"lens": lens, "overall_verdict": sign, "confidence": conf} for lens in lenses]


def test_opposed_precedent_caps_a_supportive_score() -> None:
    s = compute_suitability(_verdicts("support", 0.8), precedent_status="opposed")
    assert s.score == 30 and s.band == "DEPRIORITISE"
    assert s.precedent_gate == "cap 30"
    assert any("opposite direction" in n for n in s.notes)


def test_failed_precedent_caps_below_investigate() -> None:
    s = compute_suitability(_verdicts("support", 0.8), precedent_status="failed")
    assert s.score == 44 and s.band == "DEPRIORITISE"


def test_validated_precedent_floors_a_mixed_score() -> None:
    liabilities = [{"severity": "high"}] * 5
    s = compute_suitability(
        _verdicts("support", 0.3), liabilities=liabilities, precedent_status="validated"
    )
    assert s.score == 75 and s.band == "PROCEED"


def test_gates_do_not_move_a_score_already_inside_them() -> None:
    s = compute_suitability(_verdicts("oppose", 0.8), precedent_status="opposed")
    assert s.score < 30 and s.precedent_gate == ""


def test_no_status_no_gate() -> None:
    base = compute_suitability(_verdicts("support", 0.8))
    assert compute_suitability(_verdicts("support", 0.8), precedent_status="").score == base.score


def test_gates_come_from_config() -> None:
    cfg = ScoringConfig(precedent_gates={"opposed": {"max": 10}})
    s = compute_suitability(_verdicts("support", 0.8), precedent_status="opposed", config=cfg)
    assert s.score == 10


# --------------------------------------------------------------- lens axis rule


def _lens(lens: str, overall: str, axes: dict[str, bool | None]) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens=lens,  # type: ignore[arg-type]
        target_gene="GENEX",
        disease="disease one",
        direction=Direction.ACTIVATE,
        overall_verdict=overall,  # type: ignore[arg-type]
        confidence=0.8,
        axes=[AxisVerdict(axis=a, verdict=v, confidence=0.8) for a, v in axes.items()],
        rationale="Approved drugs validate the target.",
    )


def test_opposed_precedent_turns_clinical_support_into_opposition() -> None:
    v = _lens("clinical", "support", {"clinical_precedent": True, "clinical_validation": True})
    out = apply_precedent(v, {"status": "opposed", "summary": "Inhibitors are approved here."})
    assert out.overall_verdict == "oppose"
    assert {a.axis: a.verdict for a in out.axes} == {
        "clinical_precedent": False,
        "clinical_validation": False,
    }
    assert out.validation_flags[-1].rule_id == "precedent.opposed"
    assert out.confidence > 0


def test_failed_precedent_sets_validation_false_and_opposes() -> None:
    v = _lens("clinical", "support", {"clinical_precedent": True, "clinical_validation": True})
    out = apply_precedent(v, {"status": "failed", "summary": "Stopped for futility."})
    assert out.overall_verdict == "oppose"
    assert {a.axis: a.verdict for a in out.axes}["clinical_validation"] is False


def test_validated_precedent_adds_a_missing_regulatory_axis() -> None:
    v = _lens("regulatory", "neutral", {"label_safety": None})
    out = apply_precedent(v, {"status": "validated", "summary": "Approved here."})
    assert out.overall_verdict == "support"
    assert {a.axis: a.verdict for a in out.axes}["approval_precedent"] is True


def test_agreeing_axes_are_lifted_to_the_rules_confidence() -> None:
    """A validated precedent's axes the lens already called favourable, but scored low
    because the landmark trials were not among its claims, get the rule's confidence."""
    v = _lens("clinical", "support", {"clinical_precedent": True, "clinical_validation": True})
    v = v.model_copy(update={"axes": [a.model_copy(update={"confidence": 0.48}) for a in v.axes]})
    out = apply_precedent(v, {"status": "validated", "summary": "Approved here."})
    assert [a.confidence for a in out.axes] == [0.85, 0.85]
    assert out.overall_verdict == "support" and out.validation_flags == []
    already = apply_precedent(out, {"status": "validated", "summary": "Approved here."})
    assert already is out


def test_non_decisive_status_leaves_the_verdict_alone() -> None:
    v = _lens("clinical", "support", {"clinical_validation": True})
    assert apply_precedent(v, {"status": "in_clinic", "summary": ""}) is v
    assert apply_precedent(v, None) is v


def test_stalled_phase_3_attrition_makes_the_clinical_lens_oppose() -> None:
    """Phase 3 reached, nothing approved, nothing running: the lens called it neutral."""
    v = _lens("clinical", "neutral", {"clinical_precedent": True, "clinical_validation": None})
    out = apply_precedent(v, {"status": "stalled", "summary": "late-stage attrition"})
    assert out.overall_verdict == "oppose"
    assert next(a for a in out.axes if a.axis == "clinical_validation").verdict is False


def test_failed_precedent_removes_approval_precedent_from_regulatory() -> None:
    v = _lens("regulatory", "neutral", {"approval_precedent": True, "label_safety": None})
    out = apply_precedent(v, {"status": "failed", "summary": "stopped for futility"})
    assert next(a for a in out.axes if a.axis == "approval_precedent").verdict is False
    assert out.overall_verdict == "neutral"  # the lens's own verdict is kept


# ------------------------------------------------------ trials in the indication


def _trial(nct: str, phase: str, conditions: list[str]) -> TrialFact:
    return TrialFact(
        nct_id=nct,
        phase=phase,
        status="COMPLETED",
        phase_numbers=(int(phase[-1]),),
        conditions=tuple(conditions),
        title=f"Trial {nct}",
    )


def test_trials_in_other_conditions_are_listed_apart() -> None:
    facts = mark_indication(
        [
            _trial("NCT00000001", "PHASE3", ["Plaque Psoriasis"]),
            _trial("NCT00000002", "PHASE3", ["Psoriatic Arthritis"]),
            _trial("NCT00000003", "PHASE2", ["Crohn's Disease", "Inflammatory Bowel Disease"]),
        ],
        "Crohn disease",
    )
    assert [f.in_indication for f in facts] == [False, False, True]
    block = key_trials_prompt(facts, disease="Crohn disease")
    here, other = block.split("In OTHER conditions")
    assert "NCT00000003" in here and "NCT00000001" not in here
    assert "never count them as clinical validation" in other
    counts = trial_count_prompt(facts, "Crohn disease")
    assert "of these, studying Crohn disease: one Phase 2 trial (NCT00000003" in counts


def test_tumour_nouns_are_folded_when_matching_the_indication() -> None:
    facts = mark_indication(
        [_trial("NCT00000004", "PHASE3", ["Breast Neoplasms"])], "breast cancer"
    )
    assert facts[0].in_indication is True


# ------------------------------------------------- failed programmes and the clinical lens


def _programme(strength: float, *, gate: int | None, available: bool = True) -> dict[str, Any]:
    return {
        "available": available,
        "falsification": strength,
        "gate": {"min_strength": 0.3, "max": gate, "reason": "r"} if gate else None,
    }


def test_a_weak_failure_does_not_set_clinical_validation_false() -> None:
    """One molecule's stalled phase 3 made the clinical-validation axis false and the lens
    oppose; the programme reading says it does not falsify the mechanism."""
    v = _lens("clinical", "neutral", {"clinical_precedent": True, "clinical_validation": None})
    out = apply_precedent(
        v, {"status": "stalled", "summary": "late-stage attrition"}, _programme(0.18, gate=None)
    )
    assert out is v


def test_a_strong_failure_still_sets_clinical_validation_false() -> None:
    v = _lens("clinical", "neutral", {"clinical_precedent": True, "clinical_validation": None})
    out = apply_precedent(v, {"status": "failed", "summary": "futility"}, _programme(0.67, gate=44))
    assert out.overall_verdict == "oppose"
    assert next(a for a in out.axes if a.axis == "clinical_validation").verdict is False


def test_a_class_with_a_positive_programme_is_not_called_a_failure_on_its_account() -> None:
    """A transfer-protein inhibitor met its endpoint and was never filed: with one weak
    failure beside it, the strength stays under the gate and the lens is left alone."""
    reading = classify_programmes(
        [
            _ot_row("drug a", [_rep(3, "COMPLETED", trial="n1")]),
            _ot_row("drug b", [_rep(3, "TERMINATED", trial="n2")]),
        ],
        [
            {
                "nct_id": "n1",
                "phase_numbers": [3],
                "in_indication": True,
                "matched_by": "drug:DRUG A",
                "interventions": ["drug a"],
                "arm_results": [{"arm": "drug a", "p_value": 0.01, "p_text": "0.01"}],
            }
        ],
        direction="inhibit",
        disease="disease one",
        scope_ids=frozenset({"MONDO_1"}),
    )
    assert [p.outcome for p in reading.programmes] == ["positive_unfiled", "stalled_unexplained"]
    assert reading.gate is None
    v = _lens("clinical", "support", {"clinical_validation": True})
    out = apply_precedent(
        v, {"status": "stalled", "summary": "attrition"}, reading.model_dump(mode="json")
    )
    assert out is v


def test_without_a_usable_reading_failed_and_stalled_keep_their_old_effect() -> None:
    v = _lens("clinical", "support", {"clinical_validation": True})
    for programme in (None, _programme(0.0, gate=None, available=False)):
        out = apply_precedent(v, {"status": "failed", "summary": "futility"}, programme)
        assert out.overall_verdict == "oppose"


def test_the_regulatory_lens_ignores_the_programme_reading() -> None:
    """No same-direction drug is approved here whatever the strength of the failures."""
    v = _lens("regulatory", "neutral", {"approval_precedent": True, "label_safety": None})
    out = apply_precedent(v, {"status": "failed", "summary": "stopped"}, _programme(0.1, gate=None))
    assert next(a for a in out.axes if a.axis == "approval_precedent").verdict is False


def test_decisive_statuses_other_than_failed_ignore_the_programme_reading() -> None:
    v = _lens("clinical", "support", {"clinical_precedent": True, "clinical_validation": True})
    out = apply_precedent(
        v, {"status": "opposed", "summary": "other direction"}, _programme(0.0, gate=None)
    )
    assert out.overall_verdict == "oppose"


# ------------------------------------------------------------ cached Open Targets rows


def _ot_evidence(known: list[dict[str, Any]]) -> Any:
    from schemas.evidence import Evidence

    return Evidence.model_construct(extra={"known_drugs": known})


def test_open_targets_rows_without_stop_reports_are_a_cache_miss() -> None:
    old = _ot_evidence([{"drug_name": "x", "stopped_for_cause": []}])
    assert _current_opentargets_rows([old]) == []


def test_current_open_targets_rows_are_kept_beside_old_ones() -> None:
    """The evidence cache keeps every past retrieval: the old row must not force a refetch
    on every run once a current one exists."""
    old = _ot_evidence([{"drug_name": "x"}])
    new = _ot_evidence([{"drug_name": "x", "stop_reports": []}])
    assert _current_opentargets_rows([old, new]) == [new]


def test_a_target_without_known_drugs_is_current() -> None:
    row = _ot_evidence([])
    assert _current_opentargets_rows([row]) == [row]


def _rep(stage: float, status: str, *, trial: str = "nct0") -> dict[str, Any]:
    return {"trial_id": trial, "stage": stage, "status": status, "categories": [], "why": ""}


def _ot_row(drug: str, reports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "drug_name": drug,
        "action_type": "INHIBITOR",
        "max_phase": max(r["stage"] for r in reports),
        "disease_id": "MONDO_1",
        "disease_name": "disease one",
        "approved_in_indication": False,
        "is_approved": False,
        "trial_status": reports[0]["status"],
        "stopped_for_cause": [],
        "stop_reports": reports,
    }
