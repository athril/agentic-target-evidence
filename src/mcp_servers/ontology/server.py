# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""HGNC + MONDO ontology lookup FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import GenePhenotypeBundle, HGNCResult, MondoResult
from .tools import get_gene_phenotypes as _get_gene_phenotypes
from .tools import resolve_hgnc_symbol as _resolve_hgnc_symbol
from .tools import resolve_mondo_term as _resolve_mondo_term

mcp = FastMCP("ontology")


@mcp.tool(name="hgnc_resolve_symbol")
async def resolve_hgnc_symbol(symbol: str) -> HGNCResult:
    """Resolve a gene symbol (including aliases/previous symbols) to its canonical HGNC record."""
    return await _resolve_hgnc_symbol(symbol)


@mcp.tool(name="mondo_resolve_term")
async def resolve_mondo_term(name_or_id: str) -> MondoResult:
    """Resolve a disease name or existing ontology id to its MONDO term and cross-references."""
    return await _resolve_mondo_term(name_or_id)


@mcp.tool(name="hpo_get_phenotypes")
async def get_gene_phenotypes(gene_symbol: str) -> GenePhenotypeBundle:
    """Fetch HPO phenotype breadth/specificity and inheritance-mode hints for a gene."""
    return await _get_gene_phenotypes(gene_symbol)


if __name__ == "__main__":
    mcp.run()
