# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""EBI GWAS Catalog FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import GWASBundle
from .tools import get_gwas_associations as _get_gwas_associations

mcp = FastMCP("gwas_catalog")


@mcp.tool()
async def get_gwas_associations(
    gene_symbol: str,
    p_threshold: float = 5e-8,
    max_snps: int = 200,
) -> GWASBundle:
    """Fetch genome-wide significant GWAS associations for a gene from EBI GWAS Catalog."""
    return await _get_gwas_associations(gene_symbol, p_threshold=p_threshold, max_snps=max_snps)


if __name__ == "__main__":
    mcp.run()
