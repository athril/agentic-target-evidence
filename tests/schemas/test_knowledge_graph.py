# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Round-trip tests for schemas/knowledge_graph.py (Step 4b)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from schemas.evidence import Provenance
from schemas.knowledge_graph import (
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeType,
)


def _prov() -> Provenance:
    return Provenance(
        agent_name="test",
        tool_name="t",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        trace_id="kg-test",
    )


def test_graph_node_round_trip():
    node = GraphNode(
        id="ENSG00000139618",
        node_type=NodeType.GENE,
        label="BRCA2",
        aliases=["FANCD1"],
        external_ids={"hgnc": "1101", "omim": "600185"},
    )
    data = node.model_dump()
    restored = GraphNode.model_validate(data)
    assert restored.id == node.id
    assert restored.node_type == NodeType.GENE
    assert restored.schema_version == "1.0"


def test_graph_node_json_serialisable():
    node = GraphNode(id="MONDO:0007254", node_type=NodeType.DISEASE, label="breast cancer")
    payload = json.loads(node.model_dump_json())
    assert payload["node_type"] == "DISEASE"


def test_graph_edge_round_trip():
    edge = GraphEdge(
        subject_id="ENSG00000139618",
        predicate=EdgeType.ASSOCIATED_WITH,
        object_id="MONDO:0007254",
        direction="positive",
        confidence=0.87,
        provenance=_prov(),
    )
    data = edge.model_dump()
    restored = GraphEdge.model_validate(data)
    assert restored.predicate == EdgeType.ASSOCIATED_WITH
    assert restored.confidence == pytest.approx(0.87)
    assert restored.schema_version == "1.0"


def test_graph_edge_json_serialisable():
    edge = GraphEdge(
        subject_id="ENSG000",
        predicate=EdgeType.INHIBITS,
        object_id="DRUG:123",
        provenance=_prov(),
    )
    payload = json.loads(edge.model_dump_json())
    assert payload["predicate"] == "INHIBITS"
    assert payload["direction"] == "unknown"


def test_node_type_enum_values():
    assert NodeType.GENE == "GENE"
    assert NodeType.DISEASE == "DISEASE"
    assert NodeType.PUBLICATION == "PUBLICATION"


def test_edge_type_enum_values():
    assert EdgeType.CAUSES == "CAUSES"
    assert EdgeType.EXPRESSED_IN == "EXPRESSED_IN"
    assert EdgeType.CONTRADICTS == "CONTRADICTS"


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        GraphEdge(
            subject_id="A",
            predicate=EdgeType.CAUSES,
            object_id="B",
            confidence=1.5,
            provenance=_prov(),
        )
