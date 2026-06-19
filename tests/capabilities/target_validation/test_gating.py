# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for graph/gating.py (MP-44)."""

from __future__ import annotations

import uuid

from capabilities.target_validation.gating import validate_transition


def _state(**kwargs):
    """Build a minimal PipelineState-like dict for testing."""
    base = {
        "run_id": uuid.uuid4(),
        "target_gene": "BRCA1",
        "disease": "breast cancer",
        "population": None,
        "tissue": None,
        "literature_evidence": [],
        "patent_evidence": [],
        "trial_evidence": [],
        "opentargets_evidence": [],
        "genetics_evidence": [],
        "omics_evidence": [],
        "screened_evidence": [],
        "hypotheses": [],
        "experiment_results": [],
        "competitive_landscape": None,
        "critiques": [],
        "review_gaps": [],
        "report_uri": None,
        "step_budget_remaining": 100,
        "loop_counters": {},
        "hitl_approved": False,
        "hitl_overrides": {},
        "messages": [],
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# HITL gate
# ---------------------------------------------------------------------------


def test_hitl_blocks_hypothesis_when_not_approved():
    state = _state(hitl_approved=False)
    assert not validate_transition(state, "hitl_gate", "hypothesis")


def test_hitl_blocks_experiment():
    state = _state(hitl_approved=False)
    assert not validate_transition(state, "hypothesis", "experiment")


def test_hitl_blocks_competitive():
    state = _state(hitl_approved=False)
    assert not validate_transition(state, "hitl_gate", "competitive")


def test_hitl_blocks_critic():
    state = _state(hitl_approved=False)
    assert not validate_transition(state, "experiment", "critic")


def test_hitl_blocks_report():
    state = _state(hitl_approved=False)
    assert not validate_transition(state, "critic", "report")


def test_hitl_allows_post_hitl_when_approved():
    state = _state(hitl_approved=True)
    assert validate_transition(state, "hitl_gate", "hypothesis")
    assert validate_transition(state, "hypothesis", "experiment")
    assert validate_transition(state, "experiment", "critic")
    assert validate_transition(state, "critic", "report")


def test_hitl_does_not_block_preprocessing_nodes():
    """Pre-HITL nodes should always be allowed regardless of hitl_approved."""
    state = _state(hitl_approved=False)
    assert validate_transition(state, "START", "literature")
    assert validate_transition(state, "literature", "screening_first")
    assert validate_transition(state, "screening_first", "knowledge_extraction")
    assert validate_transition(state, "knowledge_extraction", "screening_second")
    assert validate_transition(state, "screening_second", "hitl_gate")


# ---------------------------------------------------------------------------
# Step budget
# ---------------------------------------------------------------------------


def test_step_budget_zero_blocks_all_transitions():
    state = _state(step_budget_remaining=0)
    assert not validate_transition(state, "literature", "screening_first")
    assert not validate_transition(state, "hitl_gate", "hypothesis")


def test_step_budget_negative_blocks_all_transitions():
    state = _state(step_budget_remaining=-1)
    assert not validate_transition(state, "literature", "screening_first")


def test_step_budget_positive_allows_transition():
    state = _state(step_budget_remaining=1, hitl_approved=True)
    assert validate_transition(state, "hitl_gate", "hypothesis")


# ---------------------------------------------------------------------------
# Literature retry cap
# ---------------------------------------------------------------------------


def test_literature_retry_allowed_below_cap():
    state = _state(loop_counters={"literature_retry": 2})
    assert validate_transition(state, "literature", "literature")


def test_literature_retry_blocked_at_cap():
    state = _state(loop_counters={"literature_retry": 3})
    assert not validate_transition(state, "literature", "literature")


def test_literature_retry_cap_does_not_block_forward_edge():
    """Cap only applies to self-loops, not literature → screening_first."""
    state = _state(loop_counters={"literature_retry": 5})
    assert validate_transition(state, "literature", "screening_first")
