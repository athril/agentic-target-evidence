# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GTEx + HPA FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ExpressionBundle
from .tools import get_expression as _get_expression

mcp = FastMCP("gtex")


@mcp.tool()
async def get_expression(gene_symbol: str, ensembl_id: str = "") -> ExpressionBundle:
    """Fetch GTEx median tissue TPM and HPA protein localization for a gene."""
    return await _get_expression(gene_symbol, ensembl_id)


if __name__ == "__main__":
    mcp.run()
