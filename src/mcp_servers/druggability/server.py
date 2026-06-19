# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Druggability FastMCP server — UniProt protein profile + ChEMBL chemistry."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ChemistryBundle, ProteinProfile
from .tools import get_chemistry as _get_chemistry
from .tools import get_protein_profile as _get_protein_profile

mcp = FastMCP("druggability")


@mcp.tool()
async def get_protein_profile(gene_symbol: str) -> ProteinProfile:
    """Fetch the reviewed human UniProt protein profile (class, location, ChEMBL xref)."""
    return await _get_protein_profile(gene_symbol)


@mcp.tool()
async def get_chemistry(chembl_target_id: str, gene_symbol: str = "") -> ChemistryBundle:
    """Fetch ChEMBL drug-mechanism and bioactivity signals for a ChEMBL target id."""
    return await _get_chemistry(chembl_target_id, gene_symbol)


if __name__ == "__main__":
    mcp.run()
