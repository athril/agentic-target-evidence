# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinGen FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ClinGenBundle
from .tools import get_clingen_validity as _get_clingen_validity

mcp = FastMCP("clingen")


@mcp.tool(name="clingen_get_validity")
async def get_clingen_validity(gene_symbol: str) -> ClinGenBundle:
    """Fetch ClinGen gene-disease validity classifications for a gene."""
    return await _get_clingen_validity(gene_symbol)


if __name__ == "__main__":
    mcp.run()
