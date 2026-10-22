# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The safety lens's per-liability statuses reach the score (workflow._liability_statuses),
and human exposure counts only drugs acting in the requested direction
(workflow._human_exposure)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from capabilities.target_validation.workflow import _human_exposure, _liability_statuses
from schemas.verdicts import LensVerdict


def _safety(*statuses: str) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="safety",
        target_gene="GENE1",
        disease="disease one",
        liability_statuses=[
            {"index": i, "status": st, "claim_ids": []} for i, st in enumerate(statuses, 1)
        ],
    )


def test_statuses_come_back_in_liability_order():
    verdict = _safety("unresolved", "resolved_by_human_data")
    rows = list(reversed(verdict.liability_statuses))
    state = {"lens_verdicts": [verdict.model_copy(update={"liability_statuses": rows})]}
    assert _liability_statuses(state, 2) == ["unresolved", "resolved_by_human_data"]


def test_no_statuses_or_a_different_list_gives_none():
    assert _liability_statuses({"lens_verdicts": []}, 1) is None
    assert _liability_statuses({"lens_verdicts": [_safety()]}, 1) is None
    assert _liability_statuses({"lens_verdicts": [_safety("unresolved")]}, 2) is None


def _state(direction: str, *drugs: tuple[str, str]) -> dict:
    known = [{"drug_name": n, "action_type": a, "is_approved": True} for n, a in drugs]
    return {
        "direction": direction,
        "opentargets_evidence": [SimpleNamespace(extra={"known_drugs": known})],
        "screened_evidence": [],
    }


@pytest.mark.parametrize(
    ("direction", "drugs", "exposed"),
    [
        ("inhibit", [("DRUG-A", "INHIBITOR")], True),
        ("activate", [("DRUG-A", "INHIBITOR")], False),  # an inhibitor says nothing of raising
        ("activate", [("DRUG-B", "AGONIST")], True),
        ("degrade", [("DRUG-A", "INHIBITOR")], True),  # both lower the target
        ("inhibit", [("DRUG-C", "BINDING AGENT")], False),  # unknown action does not count
        ("unspecified", [("DRUG-C", "BINDING AGENT")], True),  # no requested change
    ],
)
def test_exposure_counts_only_drugs_acting_in_the_requested_direction(direction, drugs, exposed):
    assert _human_exposure(_state(direction, *drugs)) is exposed


def _report(trial: str, *categories: str) -> dict:
    return {
        "trial_id": trial,
        "stage": 2.0,
        "status": "TERMINATED",
        "categories": list(categories),
        "why": "w",
    }


async def test_safety_stops_come_from_the_stop_category_in_this_indication_only():
    """The category decides, not the text; another indication's stop is listed."""
    from capabilities.target_validation.workflow import _safety_stops

    known = [
        {
            "drug_name": "DRUG-A",
            "action_type": "INHIBITOR",
            "disease_name": "disease one",
            "stop_reports": [_report("NCT1", "Safety_Sideeffects"), _report("NCT2", "Negative")],
        },
        {
            "drug_name": "DRUG-B",
            "action_type": "INHIBITOR",
            "disease_name": "disease two",
            "stop_reports": [_report("NCT3", "Safety_Sideeffects", "Business_Administrative")],
        },
        {
            "drug_name": "DRUG-C",
            "action_type": "AGONIST",
            "disease_name": "disease one",
            "stop_reports": [_report("NCT4", "Safety_Sideeffects")],
        },
    ]
    state = {
        "direction": "inhibit",
        "disease": "disease one",
        "opentargets_evidence": [SimpleNamespace(extra={"known_drugs": known})],
    }

    here, elsewhere = await _safety_stops(state)

    assert list(here) == ["DRUG-A"] and here["DRUG-A"][0].startswith("NCT1 (phase 2")
    assert len(elsewhere) == 1 and elsewhere[0].startswith("drug-b in disease two: NCT3")


def _fact(status: str, phase: int, enrollment: int | None) -> dict:
    return {
        "matched_by": "drug:DRUG-A",
        "phase_numbers": [phase],
        "status": status,
        "enrollment": enrollment,
    }


@pytest.mark.parametrize(
    ("fact", "exposed"),
    [
        (_fact("COMPLETED", 2, 300), True),
        (_fact("TERMINATED", 3, 1900), True),  # stopped after dosing its patients
        (_fact("ACTIVE_NOT_RECRUITING", 3, 500), True),
        (_fact("TERMINATED", 3, 0), False),  # stopped before anyone enrolled
        (_fact("WITHDRAWN", 3, 0), False),
        (_fact("RECRUITING", 3, 200), False),
        (_fact("COMPLETED", 1, 40), False),  # phase 1 is not counted
    ],
)
def test_exposure_counts_phase_2_trials_that_dosed_people(monkeypatch, fact, exposed):
    """An unapproved requested-direction drug counts as given to people when a phase 2+
    trial of it completed, or was terminated or closed after enrolling."""
    import capabilities.target_validation.workflow as wf

    monkeypatch.setattr(wf, "_trial_facts_json", lambda _ev: [fact])
    monkeypatch.setattr(wf, "_keep_evidence", lambda _s: [])
    known = [{"drug_name": "DRUG-A", "action_type": "INHIBITOR", "is_approved": False}]
    state = {
        "direction": "inhibit",
        "opentargets_evidence": [SimpleNamespace(extra={"known_drugs": known})],
    }
    assert _human_exposure(state) is exposed


def test_worsening_on_two_compounds_is_harm_in_people_and_on_one_is_not():
    from capabilities.target_validation.workflow import _worsened_programmes

    def reading(*worsened: bool) -> dict:
        return {
            "programmes": [{"drug": f"DRUG-{i}", "worsened": w} for i, w in enumerate(worsened)]
        }

    assert _worsened_programmes(reading(True, False)) == []
    assert [s for s, _ in _worsened_programmes(reading(True, True, False))] == [
        "publications (drug-0)",
        "publications (drug-1)",
    ]
    assert _worsened_programmes(None) == []
