# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""SCImago Journal Rank (SJR) FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import SjrRecord
from .tools import resolve_sjr as _resolve_sjr

mcp = FastMCP("scimago")


@mcp.tool(name="scimago_resolve_sjr")
def resolve_sjr(issn: str = "", essn: str = "", journal_title: str = "") -> SjrRecord:
    """Resolve a journal's SJR score and quartile from ISSN (preferred) or title."""
    return _resolve_sjr(issn=issn, essn=essn, journal_title=journal_title)


if __name__ == "__main__":
    mcp.run()
