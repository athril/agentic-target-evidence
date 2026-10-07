# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""IMPC FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ImpcBundle
from .tools import get_impc_phenotypes as _get_impc_phenotypes

mcp = FastMCP("impc")


@mcp.tool(name="impc_get_phenotypes")
async def get_impc_phenotypes(gene_symbol: str, mgi_id: str = "") -> ImpcBundle:
    """Fetch IMPC statistically significant knockout-mouse phenotype calls for a gene.

    ``gene_symbol`` may be human (PCSK9) or mouse (Pcsk9); ``mgi_id`` (e.g. "MGI:2140260")
    matches the mouse ortholog exactly.
    """
    return await _get_impc_phenotypes(gene_symbol, mgi_id)


if __name__ == "__main__":
    mcp.run()
