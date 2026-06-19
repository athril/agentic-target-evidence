# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""USPTO / PatentsView FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import PatentRecord
from .tools import search_patents as _search_patents

mcp = FastMCP("uspto")


@mcp.tool()
async def search_patents(gene: str, disease: str) -> list[PatentRecord]:
    """Search PatentsView for patents referencing the gene and disease.

    All results are classified NON_SENSITIVE (patent data is public).
    """
    return await _search_patents(gene, disease)


if __name__ == "__main__":
    mcp.run()
