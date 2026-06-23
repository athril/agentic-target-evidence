# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""EBI Expression Atlas disease-vs-control differential expression tools.

Fills a gap GTEx cannot answer: GTEx (`mcp_servers/gtex`) is normal-tissue-only,
so it tells us "is this gene expressed in kidney" but not "is this gene
dysregulated in FSGS kidney vs. healthy kidney." Expression Atlas aggregates
disease-vs-control differential-expression contrasts across public RNA-seq/
microarray experiments and answers exactly that question.

There is no documented, gene-indexed REST endpoint for this data (the
`/gxa/json/experiments` listing endpoint ignores query params entirely). The
real data path — reverse-engineered from the live site, since EBI does not
publish it — is: resolve the gene symbol to an Ensembl ID via the HTML search
redirect, then call the same JSON endpoint the differential-expression results
widget itself calls (`/gxa/json/search/differential_results`).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_SEARCH_URL = "https://www.ebi.ac.uk/gxa/search"
_DIFFERENTIAL_URL = "https://www.ebi.ac.uk/gxa/json/search/differential_results"

_TOP_RESULT_COUNT = 10
_SUMMARY_COUNT = 5


class DifferentialResult(BaseModel):
    experiment_accession: str
    experiment_name: str
    comparison: str
    regulation: str  # "UP" or "DOWN"
    fold_change: float
    p_value: float
    factors: list[str] = []


class DifferentialExpressionBundle(BaseModel):
    gene_symbol: str
    ensembl_id: str = ""
    disease: str = ""
    disease_specific: bool = False
    results: list[DifferentialResult] = []
    source_link: str = ""
    text: str = ""


async def _resolve_ensembl_id(gene_symbol: str, species: str) -> str:
    """Resolve a gene symbol to an Ensembl ID via the gene-search redirect.

    `/gxa/search?geneQuery=<symbol>` 302s to `/gxa/genes/<ensembl_id>` when the
    gene resolves to exactly one Atlas bioentity; returns "" otherwise.
    """
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        resp = await client.get(_SEARCH_URL, params={"geneQuery": gene_symbol, "species": species})
    if resp.status_code not in (302, 303):
        return ""
    location: str = resp.headers.get("location", "")
    marker = "/genes/"
    if marker not in location:
        return ""
    return location.split(marker, 1)[1].split("?")[0]


async def _fetch_differential(
    ensembl_id: str, species: str, condition: str
) -> list[dict[str, Any]]:
    params = {
        "species": species,
        "geneQuery": json.dumps([{"value": ensembl_id}]),
        "conditionQuery": json.dumps([{"value": condition}]) if condition else "",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(_DIFFERENTIAL_URL, params=params)

    if resp.status_code != 200:
        raise MCPToolError(
            f"Expression Atlas API returned HTTP {resp.status_code} for {ensembl_id}"
        )
    try:
        data = resp.json()
    except ValueError:
        return []
    return list(data.get("results", []))


def _to_result(raw: dict[str, Any]) -> DifferentialResult:
    return DifferentialResult(
        experiment_accession=raw.get("experimentAccession", ""),
        experiment_name=raw.get("experimentName", ""),
        comparison=raw.get("comparison", ""),
        regulation=raw.get("regulation", ""),
        fold_change=float(raw.get("foldChange", 0.0)),
        p_value=float(raw.get("pValue", 1.0)),
        factors=raw.get("factors", []),
    )


def _summary_line(r: DifferentialResult) -> str:
    return (
        f"{r.regulation} {r.fold_change:+.1f}-fold (p={r.p_value:.2g}) in "
        f"{r.comparison} [{r.experiment_accession}]"
    )


async def get_differential_expression(
    gene_symbol: str, disease: str = "", species: str = "homo sapiens"
) -> DifferentialExpressionBundle:
    """Fetch disease-vs-control differential expression for a gene from Expression Atlas."""
    ensembl_id = await _resolve_ensembl_id(gene_symbol, species)
    if not ensembl_id:
        return DifferentialExpressionBundle(
            gene_symbol=gene_symbol,
            disease=disease,
            text=f"Expression Atlas: no gene record found for {gene_symbol}.",
        )

    disease_specific = False
    raw_results: list[dict[str, Any]] = []
    if disease:
        raw_results = await _fetch_differential(ensembl_id, species, disease)
        disease_specific = bool(raw_results)

    if not raw_results:
        raw_results = await _fetch_differential(ensembl_id, species, "")

    raw_results.sort(key=lambda r: (r.get("pValue", 1.0), -abs(r.get("foldChange", 0.0))))
    results = [_to_result(r) for r in raw_results[:_TOP_RESULT_COUNT]]
    summary = "; ".join(_summary_line(r) for r in results[:_SUMMARY_COUNT])

    if disease_specific:
        text = (
            f"Expression Atlas: {gene_symbol} differential expression matching "
            f"'{disease}': {summary}."
        )
    elif results:
        disease_clause = f" specific to '{disease}'" if disease else ""
        text = (
            f"Expression Atlas: no differential expression data{disease_clause} found for "
            f"{gene_symbol}; top significant differential results in other contexts: {summary}."
        )
    else:
        text = f"Expression Atlas: no differential expression data found for {gene_symbol}."

    return DifferentialExpressionBundle(
        gene_symbol=gene_symbol,
        ensembl_id=ensembl_id,
        disease=disease,
        disease_specific=disease_specific,
        results=results,
        source_link=f"https://www.ebi.ac.uk/gxa/genes/{ensembl_id}",
        text=text,
    )
