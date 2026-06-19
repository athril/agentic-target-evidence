# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenAlex journal-quality (CC0) FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import OpenAlexJournal
from .tools import resolve_journal as _resolve_journal

mcp = FastMCP("openalex")


@mcp.tool()
async def resolve_journal(
    issn: str = "", essn: str = "", journal_title: str = ""
) -> OpenAlexJournal:
    """Resolve a journal's OpenAlex quality signal from ISSN (preferred) or title."""
    return await _resolve_journal(issn=issn, essn=essn, journal_title=journal_title)


if __name__ == "__main__":
    mcp.run()
