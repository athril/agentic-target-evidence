# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for services/knowledge_graph/builder (relocated from test_evidence_services)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from schemas.evidence import CoreClaim, DataClass, Direction, EvidenceType, Provenance
from services.evidence.claim_clustering import cluster_claims
from services.knowledge_graph.builder import EvidenceGraph, build_evidence_graph

_RUN_ID = uuid.uuid4()
_TRACE = "kg-builder-test"


def _prov() -> Provenance:
    return Provenance(
        agent_name="test",
        tool_name="t",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        trace_id=_TRACE,
    )


def _claim(
    *,
    gene: str = "BRCA1",
    et: EvidenceType = EvidenceType.ARTICLE,
    direction: Direction = Direction.UNSPECIFIED,
) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene=gene,
        gene_id="ENSG000",
        disease="breast cancer",
        disease_id="EFO_0000305",
        evidence_type=et,
        claim_text="Test claim.",
        direction=direction,
        confidence=None,
        availability_date=None,
        provenance=_prov(),
        classification=DataClass.NON_SENSITIVE,
    )


def test_build_evidence_graph_creates_nodes():
    claims = [
        _claim(et=EvidenceType.ARTICLE, direction=Direction.INHIBIT),
        _claim(et=EvidenceType.GENETICS, direction=Direction.INHIBIT),
    ]
    clusters = cluster_claims(claims)
    graph = build_evidence_graph(clusters)
    assert isinstance(graph, EvidenceGraph)
    assert len(graph.nodes) == 2


def test_build_evidence_graph_detects_conflict():
    claims = [
        _claim(et=EvidenceType.ARTICLE, direction=Direction.INHIBIT),
        _claim(et=EvidenceType.GENETICS, direction=Direction.ACTIVATE),
    ]
    clusters = cluster_claims(claims)
    graph = build_evidence_graph(clusters)
    conflicts = graph.conflicts()
    assert len(conflicts) == 1


def test_build_evidence_graph_no_conflict_when_same_direction():
    claims = [
        _claim(et=EvidenceType.ARTICLE, direction=Direction.INHIBIT),
        _claim(et=EvidenceType.GENETICS, direction=Direction.INHIBIT),
    ]
    clusters = cluster_claims(claims)
    graph = build_evidence_graph(clusters)
    assert graph.conflicts() == []
