# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OMIM FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import OmimBundle
from .tools import get_omim_validity as _get_omim_validity

mcp = FastMCP("omim")


@mcp.tool(name="omim_get_validity")
async def get_omim_validity(gene_symbol: str) -> OmimBundle:
    """Fetch OMIM Mendelian phenotype-gene associations for a gene (no-op if OMIM_API_KEY unset)."""
    return await _get_omim_validity(gene_symbol)


if __name__ == "__main__":
    mcp.run()
