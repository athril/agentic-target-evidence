# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""NIH iCite article citation metrics (public domain) FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ICiteRecord
from .tools import article_metrics as _article_metrics

mcp = FastMCP("icite")


@mcp.tool(name="icite_article_metrics")
async def article_metrics(pmids: list[str]) -> list[ICiteRecord]:
    """Relative Citation Ratio, citation count and human/animal research share per PMID."""
    found = await _article_metrics(pmids)
    return list(found.values())


if __name__ == "__main__":
    mcp.run()
