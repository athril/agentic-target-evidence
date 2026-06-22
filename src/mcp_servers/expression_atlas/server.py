# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Expression Atlas FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import DifferentialExpressionBundle
from .tools import get_differential_expression as _get_differential_expression

mcp = FastMCP("expression_atlas")


@mcp.tool(name="expression_atlas_get_differential_expression")
async def get_differential_expression(
    gene_symbol: str, disease: str = "", species: str = "homo sapiens"
) -> DifferentialExpressionBundle:
    """Fetch disease-vs-control differential expression for a gene from Expression Atlas."""
    return await _get_differential_expression(gene_symbol, disease, species)


if __name__ == "__main__":
    mcp.run()
