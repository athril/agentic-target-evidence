# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""UniProt FastMCP server — reviewed human protein profile."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ProteinProfile
from .tools import get_protein_profile as _get_protein_profile

mcp = FastMCP("uniprot")


@mcp.tool()
async def get_protein_profile(gene_symbol: str) -> ProteinProfile:
    """Fetch the reviewed human UniProt protein profile (class, location, ChEMBL xref)."""
    return await _get_protein_profile(gene_symbol)


if __name__ == "__main__":
    mcp.run()
