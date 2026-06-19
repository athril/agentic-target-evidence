# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ENCODE cis-regulatory assay coverage tools.

The originally-targeted signal — cCRE classification (PLS/ELS/CTCF-bound) at the
gene locus via SCREEN — is not reachable through any currently-accessible public
API: SCREEN's GraphQL endpoint returns `{"error":"request not allowed"}` (403,
deliberately gated) and its legacy REST `cre_table` endpoint returns HTTP 500 with
an undocumented payload schema. Building true cCRE classification would require
bulk per-biosample BED-file downloads plus genomic-interval intersection — out of
scope here.

This module instead uses the one ENCODE-hosted endpoint confirmed to work,
`region-search`, which answers a coarser but still useful question: how many
regulatory-relevant assays (ChIP-seq, DNase-seq, ATAC-seq) have been run over the
gene's locus, broken down by assay type, target (e.g. CTCF, POLR2A), and
tissue/organ. This is real signal about how well-characterized a locus's
regulatory landscape is, just not a PLS/ELS/CTCF classification per se.

`region-search` only returns JSON with an explicit `Accept: application/json`
header (otherwise it serves the React-rendered HTML page), and ignores the
`output=json` query parameter entirely — both reverse-engineered by inspecting
the live response with and without the header.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_REGION_SEARCH_URL = "https://www.encodeproject.org/region-search/"

_TOP_N = 8


class AssayCoverage(BaseModel):
    key: str
    experiment_count: int


class RegulatoryCoverageBundle(BaseModel):
    gene_symbol: str
    coordinates: str = ""
    total_experiments: int = 0
    top_assays: list[AssayCoverage] = []
    top_targets: list[AssayCoverage] = []  # ChIP-seq targets, e.g. CTCF, POLR2A
    top_organs: list[AssayCoverage] = []
    source_link: str = ""
    text: str = ""


def _facet_terms(data: dict, field: str) -> list[AssayCoverage]:
    for f in data.get("facets") or []:
        if f.get("field") == field:
            terms = sorted(
                (t for t in (f.get("terms") or []) if t.get("doc_count", 0) > 0),
                key=lambda t: -t["doc_count"],
            )
            return [
                AssayCoverage(key=t["key"], experiment_count=t["doc_count"]) for t in terms[:_TOP_N]
            ]
    return []


async def get_regulatory_coverage(
    gene_symbol: str, genome: str = "GRCh38"
) -> RegulatoryCoverageBundle:
    """Fetch regulatory-assay (ChIP-seq/DNase-seq/ATAC-seq) coverage at a gene locus."""
    params = {"region": gene_symbol, "genome": genome}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            _REGION_SEARCH_URL, params=params, headers={"Accept": "application/json"}
        )
    if resp.status_code != 200:
        raise MCPToolError(
            f"ENCODE region-search API returned HTTP {resp.status_code} for {gene_symbol}"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise MCPToolError(f"ENCODE region-search returned non-JSON for {gene_symbol}") from exc

    total = data.get("total", 0)
    if not total:
        notification = data.get("notification", "")
        clause = f" ({notification})" if notification else ""
        return RegulatoryCoverageBundle(
            gene_symbol=gene_symbol,
            text=f"ENCODE: no regulatory-assay coverage found for {gene_symbol}{clause}.",
        )

    top_assays = _facet_terms(data, "assay_term_name")
    top_targets = _facet_terms(data, "target.label")
    top_organs = _facet_terms(data, "biosample_ontology.organ_slims")
    coordinates = data.get("coordinates_msg") or data.get("coordinates", "")

    assay_summary = ", ".join(f"{a.key} ({a.experiment_count})" for a in top_assays)
    target_clause = ""
    if top_targets:
        target_summary = ", ".join(f"{t.key} ({t.experiment_count})" for t in top_targets[:5])
        target_clause = f"; top ChIP-seq targets: {target_summary}"

    return RegulatoryCoverageBundle(
        gene_symbol=gene_symbol,
        coordinates=coordinates,
        total_experiments=total,
        top_assays=top_assays,
        top_targets=top_targets,
        top_organs=top_organs,
        source_link=f"https://www.encodeproject.org/region-search/?region={gene_symbol}&genome={genome}",
        text=(
            f"ENCODE region-search: {total} experiment(s) overlap the {gene_symbol} locus"
            f"{f' ({coordinates})' if coordinates else ''}. Assays: {assay_summary}{target_clause}."
        ),
    )
