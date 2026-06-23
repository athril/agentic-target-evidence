# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""SPOKE knowledge graph tools — public, no-auth REST API.

SPOKE (https://spoke.rbvi.ucsf.edu) is a precomputed biomedical knowledge
graph spanning genes, diseases, compounds, anatomy, and more. This module
calls the read-only `/neighborhood/{node_type}/{attribute}/{value}` endpoint,
which returns a flat list of `{"data": {...}}` items: node items have no
"source"/"target" keys, edge items do. Both nodes and edges carry their
type in `properties`-sibling key `neo4j_type` (not `name`, despite the
published OpenAPI schema's `JSEdge.name` field — verified against the live
API on 2026-06-17).

Disease nodes use Disease Ontology identifiers (DOID:*), not the EFO/MONDO
convention used elsewhere in this codebase — no ID crosswalk is attempted;
callers that need disease-scoping match on `disease_name` substrings.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_SPOKE_API = "https://spoke.rbvi.ucsf.edu/api/v1"

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0

_GENE_DISEASE_EDGE_TYPE = "ASSOCIATES_DaG"


class SpokeGeneDiseaseAssociation(BaseModel):
    disease_name: str
    disease_identifier: str = ""  # Disease Ontology id, e.g. "DOID:9352"
    edge_sources: list[str] = []  # e.g. ["GWAS"], ["DISEASES"], ["HPO"]
    gwas_pvalue: float | None = None
    diseases_score: float | None = None  # max DISEASES textmining/knowledge/experiments score


class GeneDiseaseBundle(BaseModel):
    gene_symbol: str
    associations: list[SpokeGeneDiseaseAssociation] = []
    source_link: str = ""
    text: str = ""


async def _get(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    """GET with retries on transient transport errors."""
    delay = _RETRY_BASE_DELAY
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await client.get(url, **kwargs)
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(delay)
                delay *= 2
    raise MCPToolError(
        f"Request to {url} failed after {_MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc


async def _neighborhood(
    node_type: str,
    attribute: str,
    value: str,
    *,
    node_filters: list[str] | None = None,
    edge_filters: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Call SPOKE's `/neighborhood/{node_type}/{attribute}/{value}` and return the raw graph."""
    params: dict[str, list[str]] = {}
    if node_filters:
        params["node_filters"] = node_filters
    if edge_filters:
        params["edge_filters"] = edge_filters
    url = f"{_SPOKE_API}/neighborhood/{node_type}/{attribute}/{value}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _get(client, url, params=params)
    if resp.status_code != 200:
        raise MCPToolError(f"SPOKE API returned HTTP {resp.status_code} for {url}")
    return list(resp.json())


def _split_graph(
    graph: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Split a SPOKE graph response into {node_id: node_data} and a list of edge data dicts."""
    nodes: dict[int, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for item in graph:
        data = item.get("data", {})
        if "source" in data:
            edges.append(data)
        else:
            nodes[data["id"]] = data
    return nodes, edges


def _parse_score(raw: Any) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _endpoint_node(nodes: dict[int, dict[str, Any]], edge: dict[str, Any]) -> dict[str, Any] | None:
    """Look up an edge's source/target node, tolerating a missing/non-int id."""
    for key in ("source", "target"):
        node_id = edge.get(key)
        if isinstance(node_id, int) and node_id in nodes:
            return nodes[node_id]
    return None


async def get_gene_disease_associations(gene_symbol: str) -> GeneDiseaseBundle:
    """Fetch SPOKE Disease-ASSOCIATES-Gene edges for a gene symbol.

    Returns every disease association SPOKE has for the gene, unfiltered by
    indication — callers disease-scope by matching `disease_name` substrings.
    """
    graph = await _neighborhood(
        "Gene",
        "name",
        gene_symbol,
        node_filters=["Disease"],
        edge_filters=[_GENE_DISEASE_EDGE_TYPE],
    )
    nodes, edges = _split_graph(graph)

    associations: list[SpokeGeneDiseaseAssociation] = []
    for edge in edges:
        if edge.get("neo4j_type") != _GENE_DISEASE_EDGE_TYPE:
            continue
        disease_node = _endpoint_node(nodes, edge)
        if disease_node is None or disease_node.get("neo4j_type") != "Disease":
            continue
        props = edge.get("properties") or {}
        disease_props = disease_node.get("properties") or {}

        edge_sources = props.get("sources") or ([props["source"]] if props.get("source") else [])
        scores = [
            s
            for s in (_parse_score(v) for v in (props.get("diseases_scores") or []))
            if s is not None
        ]

        associations.append(
            SpokeGeneDiseaseAssociation(
                disease_name=disease_props.get("name", ""),
                disease_identifier=str(disease_props.get("identifier", "")),
                edge_sources=edge_sources,
                gwas_pvalue=props.get("gwas_pvalue"),
                diseases_score=max(scores) if scores else None,
            )
        )

    return GeneDiseaseBundle(
        gene_symbol=gene_symbol,
        associations=associations,
        source_link=f"{_SPOKE_API}/neighborhood/Gene/name/{gene_symbol}",
        text=f"SPOKE: {len(associations)} disease association edge(s) for {gene_symbol}.",
    )


_ANATOMY_EDGE_TYPES = frozenset({"EXPRESSES_AeG", "UPREGULATES_AuG", "DOWNREGULATES_AdG"})


class SpokeAnatomyExpression(BaseModel):
    anatomy_name: str
    anatomy_identifier: str = ""  # UBERON id, e.g. "UBERON:0002107"
    edge_type: str = ""  # EXPRESSES_AeG / UPREGULATES_AuG / DOWNREGULATES_AdG


class AnatomyExpressionBundle(BaseModel):
    gene_symbol: str
    expressions: list[SpokeAnatomyExpression] = []
    source_link: str = ""
    text: str = ""


async def get_anatomy_expression(gene_symbol: str) -> AnatomyExpressionBundle:
    """Fetch SPOKE Anatomy-Gene expression edges for a gene (UBERON anatomy terms).

    SPOKE's Anatomy nodes use UBERON terms, distinct from GTEx tissue codes —
    no crosswalk is attempted; results are kept as an independently-labeled
    corroborating source.
    """
    graph = await _neighborhood(
        "Gene",
        "name",
        gene_symbol,
        node_filters=["Anatomy"],
        edge_filters=list(_ANATOMY_EDGE_TYPES),
    )
    nodes, edges = _split_graph(graph)

    expressions: list[SpokeAnatomyExpression] = []
    for edge in edges:
        if edge.get("neo4j_type") not in _ANATOMY_EDGE_TYPES:
            continue
        anatomy_node = _endpoint_node(nodes, edge)
        if anatomy_node is None or anatomy_node.get("neo4j_type") != "Anatomy":
            continue
        props = anatomy_node.get("properties") or {}
        expressions.append(
            SpokeAnatomyExpression(
                anatomy_name=props.get("name", ""),
                anatomy_identifier=str(props.get("identifier", "")),
                edge_type=edge.get("neo4j_type", ""),
            )
        )

    return AnatomyExpressionBundle(
        gene_symbol=gene_symbol,
        expressions=expressions,
        source_link=f"{_SPOKE_API}/neighborhood/Gene/name/{gene_symbol}",
        text=f"SPOKE: {len(expressions)} anatomy expression edge(s) for {gene_symbol}.",
    )
