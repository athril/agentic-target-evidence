# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""DGIdb (Drug Gene Interaction Database) FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import CategoryBundle, InteractionBundle
from .tools import get_gene_categories as _get_gene_categories
from .tools import get_gene_drug_interactions as _get_gene_drug_interactions

mcp = FastMCP("dgidb")


@mcp.tool(name="dgidb_get_gene_drug_interactions")
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
    return await _get_gene_drug_interactions(
        gene_symbol, max_results=max_results, approved_only=approved_only
    )


@mcp.tool(name="dgidb_get_gene_categories")
async def get_gene_categories(gene_symbol: str) -> CategoryBundle:
    """Fetch DGIdb gene-category annotations for a gene.

    Returns category labels (e.g. DRUGGABLE GENOME, KINASE, CLINICALLY ACTIONABLE,
    DRUG RESISTANCE) each with the source databases asserting it, plus a convenience
    `is_druggable_genome` flag.
    """
    return await _get_gene_categories(gene_symbol)


if __name__ == "__main__":
    mcp.run()
