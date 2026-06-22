# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ENCODE FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import RegulatoryCoverageBundle
from .tools import get_regulatory_coverage as _get_regulatory_coverage

mcp = FastMCP("encode")


@mcp.tool(name="encode_get_regulatory_coverage")
async def get_regulatory_coverage(
    gene_symbol: str, genome: str = "GRCh38"
) -> RegulatoryCoverageBundle:
    """Fetch regulatory-assay (ChIP-seq/DNase-seq/ATAC-seq) coverage at a gene locus."""
    return await _get_regulatory_coverage(gene_symbol, genome)


if __name__ == "__main__":
    mcp.run()
