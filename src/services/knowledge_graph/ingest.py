# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Knowledge graph ingestion stubs.

Ingests external biomedical edges into the knowledge graph.
Implemented in a follow-up milestone once the Postgres GraphNodeRow/GraphEdgeRow
models and Alembic migration are in place.
"""

from __future__ import annotations


async def ingest_opentargets_associations(gene_id: str, disease_id: str) -> None:
    """Ingest ASSOCIATED_WITH / CAUSES edges from OpenTargets for a gene-disease pair."""
    raise NotImplementedError("ingest_opentargets_associations not yet implemented")


async def ingest_gtex_expression(gene_id: str, tissue: str | None = None) -> None:
    """Ingest EXPRESSED_IN edges from GTEx for a gene, optionally filtered by tissue."""
    raise NotImplementedError("ingest_gtex_expression not yet implemented")


async def ingest_reactome_pathways(gene_id: str) -> None:
    """Ingest pathway membership edges (GENE → PATHWAY) from Reactome."""
    raise NotImplementedError("ingest_reactome_pathways not yet implemented")
