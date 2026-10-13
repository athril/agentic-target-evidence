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


def _kept(n: int) -> list:
    import uuid
    from datetime import UTC, datetime

    from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance

    return [
        Evidence(
            evidence_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            gene="GENE-A",
            disease="DISEASE-A",
            evidence_type=EvidenceType.ARTICLE,
            scope="abstract",
            source=f"PMID:{i}",
            source_link="https://example.org",
            classification=DataClass.NON_SENSITIVE,
            provenance=Provenance(
                agent_name="t", tool_name="t", timestamp=datetime.now(UTC), trace_id="t"
            ),
            extra={"screening_verdict": {"verdict": "keep"}},
        )
        for i in range(n)
    ]


async def test_resumed_run_reuses_carried_source_quality(compiled_graph, monkeypatch):
    """A resume keeps the scores of the run it continues; rescoring the same sources would
    repeat every journal lookup against external quotas (even under force_refresh)."""
    from unittest.mock import AsyncMock

    import capabilities.target_validation.workflow as wf

    kept = _kept(3)
    carried = {str(e.evidence_id): {"sjr_score": 0.6} for e in kept}
    run = AsyncMock(side_effect=AssertionError("must not rescore"))
    monkeypatch.setattr(wf.SourceQualityAgent, "run", run)
    state = {
        "target_gene": "GENE-A",
        "disease": "DISEASE-A",
        "direction": "inhibit",
        "force_refresh": True,
        "screened_evidence": kept,
        "source_quality": carried,
    }
    out = await compiled_graph.nodes["source_quality"].ainvoke(state)
    assert out["source_quality"] == carried
    run.assert_not_called()


async def test_new_sources_are_scored(compiled_graph, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    import capabilities.target_validation.workflow as wf

    kept = _kept(2)
    fresh = {str(e.evidence_id): {"sjr_score": 0.4} for e in kept}
    run = AsyncMock(return_value=MagicMock(payload={"source_quality": fresh}))
    monkeypatch.setattr(wf.SourceQualityAgent, "run", run)
    state = {
        "target_gene": "GENE-A",
        "disease": "DISEASE-A",
        "direction": "inhibit",
        "force_refresh": True,
        "screened_evidence": kept,
        "source_quality": {str(kept[0].evidence_id): {"sjr_score": 0.6}},  # one missing
        "run_id": kept[0].run_id,
    }
    out = await compiled_graph.nodes["source_quality"].ainvoke(state)
    assert out["source_quality"] == fresh
    run.assert_called_once()
