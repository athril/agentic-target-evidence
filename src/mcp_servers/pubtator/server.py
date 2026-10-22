# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""PubTator3 FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import PubTatorDocument
from .tools import get_annotations as _get_annotations

mcp = FastMCP("pubtator")


@mcp.tool(name="pubtator_get_annotations")
async def get_annotations(pmids: list[str]) -> list[PubTatorDocument]:
    """Fetch PubTator3 entity annotations (genes, variants, chemicals, diseases, species,
    cell lines, with identifiers and offsets) for the titles and abstracts of PubMed
    articles. PMIDs PubTator has not annotated are left out."""
    return await _get_annotations(pmids)


if __name__ == "__main__":
    mcp.run()
