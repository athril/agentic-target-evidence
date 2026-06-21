# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""DGIdb (Drug Gene Interaction Database) tools via the GraphQL API.

Endpoint: https://dgidb.org/api/graphql

DGIdb aggregates curated drug-gene interaction claims (mechanism of action,
directionality, normalized interaction score) from dozens of source databases
(DrugBank, ChEMBL, PharmGKB, CIViC, OncoKB, FDA, TTD, ...), plus independent
"gene category" annotations such as DRUGGABLE GENOME, KINASE, and CLINICALLY
ACTIONABLE. This is additive over ChEMBL/UniProt (mcp_servers/chembl, mcp_servers/uniprot)
and Open Targets known-drugs: DGIdb surfaces per-interaction directionality
and per-claim source provenance that those don't carry, and is a primary
source for "is this gene part of the druggable genome" categorization.

Covers:
- Gene-drug interactions, ranked by DGIdb's normalized interaction score
  (get_gene_drug_interactions)
- Druggable-genome / gene-category annotations (get_gene_categories)
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import post_with_retry

_DGIDB_GRAPHQL = "https://dgidb.org/api/graphql"


async def _graphql(query: str, variables: dict) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await post_with_retry(
            client,
            _DGIDB_GRAPHQL,
            json={"query": query, "variables": variables},
            headers={"Content-Type": "application/json"},
        )
    if response.status_code != 200:
        raise MCPToolError(f"DGIdb API returned HTTP {response.status_code}")
    data = response.json()
    if "errors" in data:
        raise MCPToolError(f"DGIdb GraphQL error: {data['errors']}")
    return data.get("data", {})


# ---------------------------------------------------------------------------
# Gene-drug interactions
# ---------------------------------------------------------------------------


class DrugInteraction(BaseModel):
    drug_name: str
    drug_concept_id: str = ""
    approved: bool = False
    interaction_score: float = 0.0
    interaction_types: list[str] = []
    directionality: str = ""
    pmids: list[int] = []
    sources: list[str] = []


class InteractionBundle(BaseModel):
    gene_symbol: str
    gene_concept_id: str = ""
    total_count: int = 0
    interactions: list[DrugInteraction] = []
    text: str = ""


_INTERACTIONS_QUERY = """
query GeneInteractions($names: [String!]) {
  genes(names: $names) {
    nodes {
      name
      conceptId
      interactions {
        drug { name conceptId approved }
        interactionScore
        interactionTypes { type directionality }
        publications { pmid }
        sources { sourceDbName }
      }
    }
  }
}
"""


async def get_gene_drug_interactions(
    gene_symbol: str,
    max_results: int = 50,
    approved_only: bool = False,
) -> InteractionBundle:
    """Fetch curated drug-gene interaction claims for a gene from DGIdb.

    Returns drugs known to interact with the gene's product, ranked by DGIdb's
    normalized interaction score, with mechanism (interaction type/directionality,
    e.g. inhibitor/INHIBITORY), supporting PMIDs, and contributing source databases.
    Set approved_only=True to restrict to drugs with regulatory approval.
    """
    data = await _graphql(_INTERACTIONS_QUERY, {"names": [gene_symbol]})
    nodes = (data.get("genes") or {}).get("nodes") or []
    if not nodes:
        return InteractionBundle(
            gene_symbol=gene_symbol,
            text=f"No DGIdb gene record found for {gene_symbol}.",
        )

    node = nodes[0]
    concept_id = node.get("conceptId") or ""
    raw = node.get("interactions") or []

    interactions: list[DrugInteraction] = []
    for item in raw:
        drug = item.get("drug") or {}
        if approved_only and not drug.get("approved"):
            continue
        types = item.get("interactionTypes") or []
        directionality = types[0].get("directionality", "") if types else ""
        interactions.append(
            DrugInteraction(
                drug_name=drug.get("name", ""),
                drug_concept_id=drug.get("conceptId", "") or "",
                approved=bool(drug.get("approved")),
                interaction_score=float(item.get("interactionScore") or 0.0),
                interaction_types=[t.get("type", "") for t in types if t.get("type")],
                directionality=directionality or "",
                pmids=[p["pmid"] for p in (item.get("publications") or []) if p.get("pmid")],
                sources=[
                    s.get("sourceDbName", "")
                    for s in (item.get("sources") or [])
                    if s.get("sourceDbName")
                ],
            )
        )

    interactions.sort(key=lambda d: d.interaction_score, reverse=True)
    total = len(interactions)
    interactions = interactions[:max_results]

    if not interactions:
        text = f"No DGIdb drug interactions found for {gene_symbol}" + (
            " (approved only)." if approved_only else "."
        )
    else:
        approved_count = sum(1 for d in interactions if d.approved)
        top_names = ", ".join(d.drug_name for d in interactions[:5])
        text = (
            f"DGIdb gene-drug interactions for {gene_symbol}: {total} drug(s). "
            f"Approved: {approved_count}. Top by interaction score: {top_names or 'N/A'}."
        )

    return InteractionBundle(
        gene_symbol=gene_symbol,
        gene_concept_id=concept_id,
        total_count=total,
        interactions=interactions,
        text=text,
    )


# ---------------------------------------------------------------------------
# Druggable genome / gene categories
# ---------------------------------------------------------------------------


class GeneCategory(BaseModel):
    name: str
    source_names: list[str] = []


class CategoryBundle(BaseModel):
    gene_symbol: str
    categories: list[GeneCategory] = []
    is_druggable_genome: bool = False
    text: str = ""


_CATEGORIES_QUERY = """
query GeneCategories($names: [String!]) {
  genes(names: $names) {
    nodes {
      name
      geneCategoriesWithSources {
        name
        sourceNames
      }
    }
  }
}
"""


async def get_gene_categories(gene_symbol: str) -> CategoryBundle:
    """Fetch DGIdb gene-category annotations for a gene.

    Returns category labels (e.g. DRUGGABLE GENOME, KINASE, CLINICALLY ACTIONABLE,
    DRUG RESISTANCE) each with the source databases asserting it, plus a convenience
    `is_druggable_genome` flag.
    """
    data = await _graphql(_CATEGORIES_QUERY, {"names": [gene_symbol]})
    nodes = (data.get("genes") or {}).get("nodes") or []
    if not nodes:
        return CategoryBundle(
            gene_symbol=gene_symbol,
            text=f"No DGIdb gene record found for {gene_symbol}.",
        )

    raw = nodes[0].get("geneCategoriesWithSources") or []
    categories = [
        GeneCategory(name=c.get("name", ""), source_names=c.get("sourceNames") or [])
        for c in raw
        if c.get("name")
    ]
    is_druggable = any(c.name == "DRUGGABLE GENOME" for c in categories)

    if not categories:
        text = f"No DGIdb gene-category annotations found for {gene_symbol}."
    else:
        names = ", ".join(c.name for c in categories)
        text = (
            f"DGIdb gene categories for {gene_symbol}: {names}. "
            f"Druggable genome: {'yes' if is_druggable else 'no'}."
        )

    return CategoryBundle(
        gene_symbol=gene_symbol,
        categories=categories,
        is_druggable_genome=is_druggable,
        text=text,
    )
