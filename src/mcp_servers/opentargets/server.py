# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Open Targets Platform FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .tools import (
    AssociationBundle,
    ColocBundle,
    KnownDrugsBundle,
    L2GBundle,
    MousePhenotypeBundle,
    SafetyBundle,
    TractabilityBundle,
)
from .tools import get_associations as _get_associations
from .tools import get_colocalizations as _get_colocalizations
from .tools import get_known_drugs as _get_known_drugs
from .tools import get_l2g_scores as _get_l2g_scores
from .tools import get_mouse_phenotypes as _get_mouse_phenotypes
from .tools import get_safety as _get_safety
from .tools import get_tractability as _get_tractability
from .tools import resolve_disease as _resolve_disease
from .tools import resolve_gene as _resolve_gene

mcp = FastMCP("opentargets")


@mcp.tool()
async def get_associations(gene_id: str, disease_id: str) -> AssociationBundle:
    """Fetch gene-disease association scores from the Open Targets Platform.

    gene_id must be an Ensembl ID (e.g. ENSG00000012048 for BRCA1).
    disease_id must be an EFO ID (e.g. EFO_0000305 for breast carcinoma).
    """
    return await _get_associations(gene_id, disease_id)


@mcp.tool()
async def get_tractability(gene_id: str) -> TractabilityBundle:
    """Fetch tractability evidence for a gene from the Open Targets Platform.

    Returns small-molecule and antibody tractability flags plus any other
    modalities (PROTAC, oligonucleotide, etc.).
    """
    return await _get_tractability(gene_id)


@mcp.tool()
async def resolve_gene(symbol: str) -> str:
    """Resolve a gene symbol (e.g. BRCA1) to its Ensembl ID via Open Targets search."""
    return await _resolve_gene(symbol)


@mcp.tool()
async def resolve_disease(name: str) -> str:
    """Resolve a disease name (e.g. 'breast cancer') to its EFO/MONDO ID via Open Targets search."""
    return await _resolve_disease(name)


@mcp.tool()
async def get_l2g_scores(gene_id: str, disease_id: str, max_results: int = 25) -> L2GBundle:
    """Fetch GWAS Locus-to-Gene (L2G) evidence for a gene-disease pair (OT Genetics).

    Returns GWAS credible sets where this gene is prioritized as the likely causal gene
    for the given disease. gene_id must be an Ensembl ID; disease_id must be an
    EFO/MONDO ontology ID.
    """
    return await _get_l2g_scores(gene_id, disease_id=disease_id, max_results=max_results)


@mcp.tool()
async def get_colocalizations(
    gene_id: str,
    h4_threshold: float = 0.5,
    max_results: int = 25,
) -> ColocBundle:
    """Fetch eQTL/pQTL ↔ GWAS colocalisations for a gene (OT Genetics).

    Returns molecular QTL signals for this gene that colocalize with GWAS loci
    (posterior H4 >= h4_threshold). gene_id must be an Ensembl ID.
    """
    return await _get_colocalizations(gene_id, h4_threshold=h4_threshold, max_results=max_results)


@mcp.tool()
async def get_known_drugs(gene_id: str, max_results: int = 50) -> KnownDrugsBundle:
    """Fetch known drugs targeting a gene from the Open Targets Platform.

    Returns drug name, type, clinical phase, approval status, mechanism of action,
    and the indication each drug is being developed for.
    gene_id must be an Ensembl ID.
    """
    return await _get_known_drugs(gene_id, max_results=max_results)


@mcp.tool()
async def get_safety(gene_id: str) -> SafetyBundle:
    """Fetch safety liabilities for a gene from the Open Targets Platform.

    Returns curated adverse event and toxicity signals (hepatotoxicity, cardiotoxicity,
    nephrotoxicity, etc.) from FDA FAERS and toxicology databases.
    gene_id must be an Ensembl ID.
    """
    return await _get_safety(gene_id)


@mcp.tool()
async def get_mouse_phenotypes(gene_id: str) -> MousePhenotypeBundle:
    """Fetch mouse knock-out phenotypes for a gene from Open Targets (MGI/IMPC).

    Returns phenotypic consequences of disrupting the mouse orthologue, establishing
    biological plausibility and early safety signals.
    gene_id must be an Ensembl ID.
    """
    return await _get_mouse_phenotypes(gene_id)


if __name__ == "__main__":
    mcp.run()
