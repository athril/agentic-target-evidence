# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for source_quality node wiring in the compiled pipeline graph."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from capabilities.target_validation.workflow import (
    _REQUIRED_UPSTREAM,
    CLEAR_FROM_NODE,
    build_graph,
)
from core.routing.router import Router


@pytest.fixture(scope="module")
def compiled_graph():
    router = MagicMock(spec=Router)
    return build_graph(router, checkpointer=None)


def test_source_quality_node_present(compiled_graph):
    assert "source_quality" in compiled_graph.get_graph().nodes


def test_claim_extraction_routes_through_source_quality_to_hitl_gate(compiled_graph):
    edges = {(e.source, e.target) for e in compiled_graph.get_graph().edges}
    assert ("claim_extraction", "source_quality") in edges
    assert ("source_quality", "hitl_gate") in edges
    assert ("claim_extraction", "hitl_gate") not in edges


def test_screening_first_restart_clears_source_quality():
    assert CLEAR_FROM_NODE["screening_first"]["source_quality"] == {}


def test_hitl_gate_restart_does_not_clear_source_quality():
    assert "source_quality" not in CLEAR_FROM_NODE["hitl_gate"]


def test_hitl_gate_requires_source_quality_upstream():
    assert "source_quality" in _REQUIRED_UPSTREAM["hitl_gate"]
