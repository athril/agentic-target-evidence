# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Structural tests for the Investigator's place in the challenge/synthesis seam.

No live graph execution here (that needs Ollama + the MCP gateway) — these
checks confirm the graph is *wired* correctly: routing, restart hygiene, and
report payload construction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from capabilities.target_validation.workflow import (
    CLEAR_FROM_NODE,
    build_graph,
)


def test_report_clear_resets_investigation_summary():
    """Restarting from report or investigator must not leak a stale investigation note."""
    assert CLEAR_FROM_NODE["report"]["investigation_summary"] == ""
    assert CLEAR_FROM_NODE["investigator"]["investigation_summary"] == ""


def test_investigator_node_sits_between_challenge_and_report():
    graph = build_graph(MagicMock())
    nodes = graph.get_graph().nodes
    edges = {(e.source, e.target) for e in graph.get_graph().edges}

    assert "investigator" in nodes
    assert "gap_detection" not in nodes
    for upstream in ("critic", "reviewer", "reconciler"):
        assert (upstream, "investigator") in edges
    assert ("investigator", "report") in edges
    # No loop back: nothing downstream of the lenses returns to hitl_gate
    downstream = {"experiment", "critic", "reviewer", "reconciler", "investigator", "report"}
    assert not any(src in downstream and dst == "hitl_gate" for src, dst in edges)


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
