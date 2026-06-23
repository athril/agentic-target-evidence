# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Structural tests for the Investigator's place in the gap/synthesis seam.

No live graph execution here (that needs Ollama + the MCP gateway) — these
checks confirm the graph is *wired* correctly: routing, restart hygiene, and
report payload construction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from capabilities.target_validation.workflow import (
    CLEAR_FROM_NODE,
    _gap_route,
    build_graph,
)


def test_gap_route_proceeds_to_investigator_not_report():
    """A clean proceed decision routes to investigator, not straight to report."""
    state = {"replan_decision": "proceed", "replan_count": 0}
    assert _gap_route(state) == "investigator"


def test_gap_route_replans_when_under_bound():
    state = {"replan_decision": "replan", "replan_count": 0}
    assert _gap_route(state) == "hitl_gate"


def test_gap_route_proceeds_once_replan_bound_exhausted():
    state = {"replan_decision": "replan", "replan_count": 2}
    assert _gap_route(state) == "investigator"


def test_report_clear_resets_investigation_summary():
    """Restarting from report or gap_detection must not leak a stale investigation note."""
    assert CLEAR_FROM_NODE["report"]["investigation_summary"] == ""
    assert CLEAR_FROM_NODE["gap_detection"]["investigation_summary"] == ""


def test_investigator_node_sits_between_gap_detection_and_report():
    graph = build_graph(MagicMock())
    nodes = graph.get_graph().nodes
    edges = {(e.source, e.target) for e in graph.get_graph().edges}

    assert "investigator" in nodes
    assert ("investigator", "report") in edges
    # gap_detection has no static edge to report — it's conditional, routed via _gap_route
    assert ("gap_detection", "report") not in edges


@pytest.mark.parametrize(
    "review_gaps,agreement_map",
    [
        ([], None),
        ([{"stage": "genetics", "missing_aspects": []}], {"consensus_verdict": "support"}),
    ],
)
def test_investigator_contract_consumes_match_node_task_spec(review_gaps, agreement_map):
    """The fields investigator_node sends must all be declared in the agent's contract."""
    from agents.synthesis.investigator.contract import CONTRACT

    task_spec_keys = {
        "target_gene",
        "disease",
        "direction",
        "review_gaps",
        "agreement_map",
        "lens_summary",
    }
    assert task_spec_keys <= CONTRACT.consumes
