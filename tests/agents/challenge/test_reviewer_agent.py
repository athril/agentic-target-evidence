# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ReviewerAgent — deterministic per-stage gaps from run facts."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.challenge.reviewer.agent import ReviewerAgent, review_gaps
from tests.agents.conftest import make_task_msg

_LENSES = ["genetics", "biology", "safety", "clinical", "commercial", "regulatory"]


def _facts(**over) -> dict:
    facts = {
        "literature": {"retrieved": 140, "kept": 120},
        "genetics": {"retrieved": 26, "kept": 20},
        "clinical": {"retrieved": 4, "kept": 2, "with_results": 1},
        "screened": 196,
        "claims": 186,
        "lenses": {n: {"verdict": "support", "open_checks": []} for n in _LENSES},
        "expected_lenses": _LENSES,
        "experiments": 1,
    }
    facts.update(over)
    return facts


def _by_stage(facts: dict) -> dict:
    return {g["stage"]: g for g in review_gaps(facts)}


def test_complete_run_reports_no_gaps():
    gaps = review_gaps(_facts())
    assert [g["stage"] for g in gaps] == [
        "sources",
        "literature",
        "genetics",
        "clinical",
        "screening",
        "extraction",
        "lenses",
        "experiment",
    ]
    assert all(g["missing_aspects"] == [] and g["completeness_score"] == 100 for g in gaps)


def test_retrieved_trials_are_never_reported_absent():
    """TRPC6: the LLM reviewer wrote "No clinical trials" with four trials retrieved."""
    clinical = _by_stage(_facts())["clinical"]
    assert not any("no clinical trial" in m.lower() for m in clinical["missing_aspects"])


@pytest.mark.parametrize(
    "clinical,expected",
    [
        ({"retrieved": 0, "kept": 0, "with_results": 0}, "No registry trials retrieved"),
        ({"retrieved": 4, "kept": 0, "with_results": 0}, "None of the 4 retrieved"),
        ({"retrieved": 4, "kept": 2, "with_results": 0}, "No kept trial has posted results"),
    ],
)
def test_clinical_gaps(clinical, expected):
    gap = _by_stage(_facts(clinical=clinical))["clinical"]
    assert gap["missing_aspects"][0].startswith(expected)
    assert gap["completeness_score"] < 100


def test_sparse_literature():
    gap = _by_stage(_facts(literature={"retrieved": 10, "kept": 3}))["literature"]
    assert gap["missing_aspects"] == ["Only 3 literature source(s) passed screening"]


def test_lens_gaps_name_failed_insufficient_and_flagged_lenses():
    lenses = {n: {"verdict": "support", "open_checks": []} for n in _LENSES if n != "safety"}
    lenses["regulatory"] = {"verdict": "insufficient_evidence", "open_checks": []}
    lenses["biology"] = {"verdict": "support", "open_checks": ["numeric_grounding"]}
    gap = _by_stage(_facts(lenses=lenses))["lenses"]
    assert gap["missing_aspects"] == [
        "biology lens: open check(s) numeric_grounding",
        "safety lens failed to produce a verdict",
        "regulatory lens: insufficient evidence",
    ]
    assert gap["completeness_score"] == round(100 * 4 / 6)


async def test_agent_returns_gaps_without_calling_a_model(run_id, trace_id):
    from harness.context import RunContext

    router = MagicMock()
    ctx = RunContext(run_id=run_id, trace_id=trace_id, router=router)
    msg = make_task_msg(
        "reviewer",
        {"target_gene": "TRPC6", "disease": "FSGS", "stage_facts": _facts()},
        run_id,
        trace_id,
    )
    result = await ReviewerAgent().run(msg, ctx)
    assert len(result.payload["review_gaps"]) == 8
    router.select.assert_not_called()


def test_failed_source_is_a_gap_not_a_clean_bill():
    """A run that lost its literature node (NCBI 429) reported every stage complete."""
    gaps = {g["stage"]: g for g in review_gaps({"failed_sources": ["literature"]})}
    assert gaps["sources"]["missing_aspects"] == [
        "literature retrieval failed — its evidence is absent from this report"
    ]
    assert gaps["sources"]["completeness_score"] < 100


def test_approved_target_without_late_phase_trials_is_flagged():
    """PCSK9: approved drugs, yet the outcome trials behind them were never retrieved."""
    facts = {
        "clinical": {"retrieved": 50, "kept": 40, "with_results": 5},
        "approved_drugs": ["ALIROCUMAB", "EVOLOCUMAB"],
        "late_phase_trials_kept": 0,
    }
    gap = {g["stage"]: g for g in review_gaps(facts)}["clinical"]
    assert any("outcome trials behind the approvals" in a for a in gap["missing_aspects"])
    assert gap["completeness_score"] <= 50


def test_lens_without_a_required_input_is_reported():
    """A genetics lens that never saw gnomAD read as "No issue detected: Genetics"."""
    lenses = {n: {"verdict": "support", "open_checks": []} for n in _LENSES}
    lenses["genetics"]["missing_inputs"] = ["gnomAD constraint (not retrieved)"]
    gap = _by_stage(_facts(lenses=lenses))["lenses"]
    assert gap["missing_aspects"] == [
        "genetics lens ran without: gnomAD constraint (not retrieved)"
    ]
