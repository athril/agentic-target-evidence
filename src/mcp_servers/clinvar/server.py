# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinVar FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ClinVarCallBundle
from .tools import get_pathogenic_variants as _get_pathogenic_variants

mcp = FastMCP("clinvar")


@mcp.tool(name="clinvar_get_pathogenic_variants")
async def get_pathogenic_variants(gene_symbol: str) -> ClinVarCallBundle:
    """Fetch ClinVar's pathogenic / likely pathogenic calls for a gene's own alleles, each
    with the condition it is for, its review status and its number of submissions."""
    return await _get_pathogenic_variants(gene_symbol)


if __name__ == "__main__":
    mcp.run()
