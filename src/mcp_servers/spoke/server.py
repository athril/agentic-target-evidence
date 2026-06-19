# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""SPOKE knowledge graph FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import AnatomyExpressionBundle, GeneDiseaseBundle
from .tools import get_anatomy_expression as _get_anatomy_expression
from .tools import get_gene_disease_associations as _get_gene_disease_associations

mcp = FastMCP("spoke")


@mcp.tool()
async def get_gene_disease_associations(gene_symbol: str) -> GeneDiseaseBundle:
    """Fetch SPOKE Disease-ASSOCIATES-Gene edges for a gene symbol."""
    return await _get_gene_disease_associations(gene_symbol)


@mcp.tool()
async def get_anatomy_expression(gene_symbol: str) -> AnatomyExpressionBundle:
    """Fetch SPOKE Anatomy-Gene expression edges for a gene symbol."""
    return await _get_anatomy_expression(gene_symbol)


if __name__ == "__main__":
    mcp.run()
