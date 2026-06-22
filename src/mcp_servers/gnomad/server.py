# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""gnomAD FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import ClinVarBundle, ConstraintBundle, LofVariantBundle
from .tools import get_clinvar_variants as _get_clinvar_variants
from .tools import get_constraint as _get_constraint
from .tools import get_lof_variants as _get_lof_variants

mcp = FastMCP("gnomad")


@mcp.tool(name="gnomad_get_constraint")
async def get_constraint(gene_symbol: str) -> ConstraintBundle:
    """Fetch gnomAD gene-level LoF/missense/synonymous constraint (LOEUF, pLI, pRec, MOEUF, syn_z)."""
    return await _get_constraint(gene_symbol)


@mcp.tool(name="gnomad_get_clinvar_variants")
async def get_clinvar_variants(ensembl_id: str, gene_symbol: str = "") -> ClinVarBundle:
    """Fetch ClinVar variants overlapping this gene from gnomAD's integrated dataset."""
    return await _get_clinvar_variants(ensembl_id, gene_symbol)


@mcp.tool(name="gnomad_get_lof_variants")
async def get_lof_variants(ensembl_id: str, gene_symbol: str = "") -> LofVariantBundle:
    """Fetch observed high-confidence pLoF variants (natural knockouts) from gnomAD v4."""
    return await _get_lof_variants(ensembl_id, gene_symbol)


if __name__ == "__main__":
    mcp.run()
