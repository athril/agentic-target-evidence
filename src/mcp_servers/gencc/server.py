# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GenCC FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import GenCCBundle
from .tools import get_gencc_validity as _get_gencc_validity

mcp = FastMCP("gencc")


@mcp.tool(name="gencc_get_validity")
async def get_gencc_validity(gene_symbol: str) -> GenCCBundle:
    """Fetch GenCC's per-submitter gene-disease validity classifications for a gene."""
    return await _get_gencc_validity(gene_symbol)


if __name__ == "__main__":
    mcp.run()
