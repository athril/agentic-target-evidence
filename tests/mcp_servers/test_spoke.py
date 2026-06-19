# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for SPOKE knowledge graph MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.spoke.tools import (
    AnatomyExpressionBundle,
    GeneDiseaseBundle,
    SpokeAnatomyExpression,
    SpokeGeneDiseaseAssociation,
    get_anatomy_expression,
    get_gene_disease_associations,
)

_BASE = "https://spoke.rbvi.ucsf.edu/api/v1"

# Shape verified live against https://spoke.rbvi.ucsf.edu/api/v1/neighborhood/Gene/name/PTPN1
# on 2026-06-17: items are {"data": {...}}; edges carry "source"/"target" node ids that
# match a node's top-level "id"; node/edge type lives in "neo4j_type" (not "name").
_GENE_NODE = {
    "data": {
        "id": 2494182,
        "neo4j_type": "Gene",
        "neo4j_root": 1,
        "properties": {"name": "PTPN1", "identifier": 5770},
    }
}

_DISEASE_NODE_T2D = {
    "data": {
        "id": 150700,
        "neo4j_type": "Disease",
        "neo4j_root": 0,
        "properties": {
            "name": "type 2 diabetes mellitus",
            "identifier": "DOID:9352",
            "source": "Disease Ontology",
        },
    }
}

_DISEASE_NODE_OBESITY = {
    "data": {
        "id": 150800,
        "neo4j_type": "Disease",
        "neo4j_root": 0,
        "properties": {"name": "obesity", "identifier": "DOID:9351", "source": "Disease Ontology"},
    }
}

_EDGE_GWAS = {
    "data": {
        "id": 900001,
        "source": 150700,
        "target": 2494182,
        "neo4j_type": "ASSOCIATES_DaG",
        "properties": {"sources": ["GWAS"], "gwas_pvalue": 8e-09},
    }
}

_EDGE_DISEASES_TEXTMINING = {
    "data": {
        "id": 900002,
        "source": 150800,
        "target": 2494182,
        "neo4j_type": "ASSOCIATES_DaG",
        "properties": {
            "sources": ["DISEASES"],
            "diseases_scores": ["6.291"],
            "diseases_sources": ["textmining"],
            "diseases_confidences": [3.145],
        },
    }
}

_EDGE_HPO_NO_SCORE = {
    "data": {
        "id": 900003,
        "source": 150800,
        "target": 2494182,
        "neo4j_type": "ASSOCIATES_DaG",
        "properties": {"source": "HPO"},
    }
}

# Unrelated edge type that must be filtered out client-side even if the API
# returns it (defends against a future server-side filter regression).
_EDGE_ENCODES = {
    "data": {
        "id": 900004,
        "source": 2494182,
        "target": 777,
        "neo4j_type": "ENCODES_GeP",
        "properties": {"source": "UniProt"},
    }
}


@respx.mock
async def test_get_gene_disease_associations_returns_bundle() -> None:
    respx.get(f"{_BASE}/neighborhood/Gene/name/PTPN1").mock(
        return_value=httpx.Response(
            200,
            json=[
                _GENE_NODE,
                _DISEASE_NODE_T2D,
                _DISEASE_NODE_OBESITY,
                _EDGE_GWAS,
                _EDGE_DISEASES_TEXTMINING,
            ],
        )
    )

    bundle = await get_gene_disease_associations("PTPN1")

    assert isinstance(bundle, GeneDiseaseBundle)
    assert bundle.gene_symbol == "PTPN1"
    assert len(bundle.associations) == 2
    by_disease = {a.disease_name: a for a in bundle.associations}

    gwas = by_disease["type 2 diabetes mellitus"]
    assert isinstance(gwas, SpokeGeneDiseaseAssociation)
    assert gwas.disease_identifier == "DOID:9352"
    assert gwas.edge_sources == ["GWAS"]
    assert gwas.gwas_pvalue == pytest.approx(8e-09)
    assert gwas.diseases_score is None

    diseases = by_disease["obesity"]
    assert diseases.disease_identifier == "DOID:9351"
    assert diseases.edge_sources == ["DISEASES"]
    assert diseases.gwas_pvalue is None
    assert diseases.diseases_score == pytest.approx(6.291)

    assert "PTPN1" in bundle.source_link
    assert "2" in bundle.text


@respx.mock
async def test_get_gene_disease_associations_handles_source_only_edge() -> None:
    """An edge with only a singular 'source' string (no 'sources' list, no score) must
    still produce an association with edge_sources populated and both scores None."""
    respx.get(f"{_BASE}/neighborhood/Gene/name/PTPN1").mock(
        return_value=httpx.Response(
            200, json=[_GENE_NODE, _DISEASE_NODE_OBESITY, _EDGE_HPO_NO_SCORE]
        )
    )

    bundle = await get_gene_disease_associations("PTPN1")

    assert len(bundle.associations) == 1
    assoc = bundle.associations[0]
    assert assoc.edge_sources == ["HPO"]
    assert assoc.gwas_pvalue is None
    assert assoc.diseases_score is None


@respx.mock
async def test_get_gene_disease_associations_ignores_non_disease_edges() -> None:
    """Edge types other than ASSOCIATES_DaG must never produce an association,
    even if the graph response includes them."""
    respx.get(f"{_BASE}/neighborhood/Gene/name/PTPN1").mock(
        return_value=httpx.Response(200, json=[_GENE_NODE, _EDGE_ENCODES])
    )

    bundle = await get_gene_disease_associations("PTPN1")

    assert bundle.associations == []


@respx.mock
async def test_get_gene_disease_associations_no_hits() -> None:
    respx.get(f"{_BASE}/neighborhood/Gene/name/UNKNOWNGENE").mock(
        return_value=httpx.Response(200, json=[_GENE_NODE])
    )

    bundle = await get_gene_disease_associations("UNKNOWNGENE")

    assert bundle.associations == []
    assert "0" in bundle.text


@respx.mock
async def test_get_gene_disease_associations_raises_on_http_error() -> None:
    respx.get(f"{_BASE}/neighborhood/Gene/name/PTPN1").mock(return_value=httpx.Response(503))

    with pytest.raises(MCPToolError, match="HTTP 503"):
        await get_gene_disease_associations("PTPN1")


_ANATOMY_NODE_LIVER = {
    "data": {
        "id": 4001,
        "neo4j_type": "Anatomy",
        "neo4j_root": 0,
        "properties": {"name": "liver", "identifier": "UBERON:0002107", "source": "Uberon"},
    }
}

_ANATOMY_NODE_KIDNEY = {
    "data": {
        "id": 4002,
        "neo4j_type": "Anatomy",
        "neo4j_root": 0,
        "properties": {"name": "kidney", "identifier": "UBERON:0002113", "source": "Uberon"},
    }
}

_EDGE_EXPRESSES_LIVER = {
    "data": {
        "id": 910001,
        "source": 4001,
        "target": 2494182,
        "neo4j_type": "EXPRESSES_AeG",
        "properties": {"sources": ["BGee"]},
    }
}

_EDGE_UPREGULATES_KIDNEY = {
    "data": {
        "id": 910002,
        "source": 4002,
        "target": 2494182,
        "neo4j_type": "UPREGULATES_AuG",
        "properties": {"sources": ["BGee"]},
    }
}


@respx.mock
async def test_get_anatomy_expression_returns_bundle() -> None:
    respx.get(f"{_BASE}/neighborhood/Gene/name/PTPN1").mock(
        return_value=httpx.Response(
            200,
            json=[
                _GENE_NODE,
                _ANATOMY_NODE_LIVER,
                _ANATOMY_NODE_KIDNEY,
                _EDGE_EXPRESSES_LIVER,
                _EDGE_UPREGULATES_KIDNEY,
            ],
        )
    )

    bundle = await get_anatomy_expression("PTPN1")

    assert isinstance(bundle, AnatomyExpressionBundle)
    assert bundle.gene_symbol == "PTPN1"
    assert len(bundle.expressions) == 2
    by_name = {e.anatomy_name: e for e in bundle.expressions}
    assert isinstance(by_name["liver"], SpokeAnatomyExpression)
    assert by_name["liver"].anatomy_identifier == "UBERON:0002107"
    assert by_name["liver"].edge_type == "EXPRESSES_AeG"
    assert by_name["kidney"].edge_type == "UPREGULATES_AuG"
    assert "PTPN1" in bundle.source_link
    assert "2" in bundle.text


@respx.mock
async def test_get_anatomy_expression_no_hits() -> None:
    respx.get(f"{_BASE}/neighborhood/Gene/name/UNKNOWNGENE").mock(
        return_value=httpx.Response(200, json=[_GENE_NODE])
    )

    bundle = await get_anatomy_expression("UNKNOWNGENE")

    assert bundle.expressions == []
    assert "0" in bundle.text


@respx.mock
async def test_get_anatomy_expression_raises_on_http_error() -> None:
    respx.get(f"{_BASE}/neighborhood/Gene/name/PTPN1").mock(return_value=httpx.Response(503))

    with pytest.raises(MCPToolError, match="HTTP 503"):
        await get_anatomy_expression("PTPN1")
