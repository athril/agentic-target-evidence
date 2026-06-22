# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Project Score (Sanger) FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ProjectScoreBundle
from .tools import get_project_score as _get_project_score

mcp = FastMCP("project_score")


@mcp.tool(name="project_score_get")
async def get_project_score(gene_symbol: str) -> ProjectScoreBundle:
    """Fetch Project Score (Sanger) CRISPR fitness/dependency data for a gene."""
    return await _get_project_score(gene_symbol)


if __name__ == "__main__":
    mcp.run()
