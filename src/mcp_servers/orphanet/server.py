# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Orphanet FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import OrphanetBundle, OrphanetPhenotypeBundle, OrphanetPrevalenceBundle
from .tools import get_orphanet_associations as _get_orphanet_associations
from .tools import get_orphanet_phenotypes as _get_orphanet_phenotypes
from .tools import get_orphanet_prevalence as _get_orphanet_prevalence

mcp = FastMCP("orphanet")


@mcp.tool(name="orphanet_get_associations")
async def get_orphanet_associations(gene_symbol: str) -> OrphanetBundle:
    """Fetch Orphanet rare-disease gene-disease associations for a gene."""
    return await _get_orphanet_associations(gene_symbol)


@mcp.tool(name="orphanet_get_prevalence")
async def get_orphanet_prevalence(orphacodes: list[str]) -> OrphanetPrevalenceBundle:
    """Fetch Orphanet disease-prevalence records for a set of OrphaCodes."""
    return await _get_orphanet_prevalence(orphacodes)


@mcp.tool(name="orphanet_get_phenotypes")
async def get_orphanet_phenotypes(orphacodes: list[str]) -> OrphanetPhenotypeBundle:
    """Fetch the HPO phenotypes, age of onset and inheritance of Orphanet disorders."""
    return await _get_orphanet_phenotypes(orphacodes)


if __name__ == "__main__":
    mcp.run()
