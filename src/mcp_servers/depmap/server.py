# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""DepMap FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import DependencyBundle
from .tools import get_dependency as _get_dependency

mcp = FastMCP("depmap")


@mcp.tool()
async def get_dependency(gene_symbol: str) -> DependencyBundle:
    """Fetch DepMap CRISPR gene effect and dependency scores for a gene."""
    return await _get_dependency(gene_symbol)


if __name__ == "__main__":
    mcp.run()
