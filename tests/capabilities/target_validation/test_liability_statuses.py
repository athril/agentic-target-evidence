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


def _safety(question_results: list[dict]) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="safety",
        target_gene="GENE1",
        disease="disease one",
        question_results=question_results,
    )


def _answer(*statuses: str) -> dict:
    return {
        "question_id": "liability_resolution",
        "answer": {
            "liabilities": [
                {"index": i, "status": st, "claim_ids": []} for i, st in enumerate(statuses, 1)
            ]
        },
    }


def test_statuses_come_back_in_liability_order():
    rows = _answer("unresolved", "resolved_by_human_data")
    rows["answer"]["liabilities"].reverse()
    state = {"lens_verdicts": [_safety([rows])]}
    assert _liability_statuses(state, 2) == ["unresolved", "resolved_by_human_data"]


def test_no_split_answer_or_a_different_list_gives_none():
    assert _liability_statuses({"lens_verdicts": []}, 1) is None
    assert _liability_statuses({"lens_verdicts": [_safety([])]}, 1) is None
    state = {"lens_verdicts": [_safety([_answer("unresolved")])]}
    assert _liability_statuses(state, 2) is None


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
