# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ChEMBL FastMCP server — drug mechanisms and bioactivity signals."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ChemistryBundle
from .tools import get_chemistry as _get_chemistry

mcp = FastMCP("chembl")


@mcp.tool(name="chembl_get_chemistry")
async def get_chemistry(chembl_target_id: str, gene_symbol: str = "") -> ChemistryBundle:
    """Fetch ChEMBL drug-mechanism and bioactivity signals for a ChEMBL target id."""
    return await _get_chemistry(chembl_target_id, gene_symbol)


if __name__ == "__main__":
    mcp.run()
