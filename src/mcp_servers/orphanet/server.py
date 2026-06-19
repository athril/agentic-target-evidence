# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Orphanet FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import OrphanetBundle
from .tools import get_orphanet_associations as _get_orphanet_associations

mcp = FastMCP("orphanet")


@mcp.tool()
async def get_orphanet_associations(gene_symbol: str) -> OrphanetBundle:
    """Fetch Orphanet rare-disease gene-disease associations for a gene."""
    return await _get_orphanet_associations(gene_symbol)


if __name__ == "__main__":
    mcp.run()
