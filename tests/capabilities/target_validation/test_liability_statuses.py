# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The safety lens's per-liability statuses reach the score (workflow._liability_statuses)."""

from __future__ import annotations

import uuid

from capabilities.target_validation.workflow import _liability_statuses
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
