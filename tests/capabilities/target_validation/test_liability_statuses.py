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
