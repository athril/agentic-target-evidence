# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""PubMed FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import PubMedAbstract, PubMedFullText, PubMedRecord
from .tools import fetch_abstract as _fetch_abstract
from .tools import fetch_pmc_record as _fetch_pmc_record
from .tools import search_pubmed as _search_pubmed

mcp = FastMCP("pubmed")


@mcp.tool()
async def search_pubmed(query: str, max_results: int = 500) -> list[PubMedRecord]:
    """Search PubMed using the NCBI E-utilities API.

    Respects the NCBI free-tier rate limit (3 req/s).  Pass an
    NCBI_API_KEY env var to raise the limit to 10 req/s.
    """
    return await _search_pubmed(query, max_results)


@mcp.tool()
async def fetch_abstract(pmid: str) -> PubMedAbstract:
    """Fetch the abstract and metadata for a single PubMed article."""
    return await _fetch_abstract(pmid)


@mcp.tool()
async def fetch_pmc_record(pmid: str) -> PubMedFullText | None:
    """Return full-text availability info via PubMed Central.

    Returns None if the article is not in PMC.
    """
    return await _fetch_pmc_record(pmid)


if __name__ == "__main__":
    mcp.run()
