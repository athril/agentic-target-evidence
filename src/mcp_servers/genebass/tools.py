# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Rare-variant gene burden results of the UK Biobank exomes, from Genebass.

Genebass (Karczewski et al., Cell Genomics 2022;2:100168, PMID 36778668) holds SAIGE-GENE
results for 4,529 phenotypes in 394,841 exomes. Its association results are CC BY 4.0, with
attribution requested to the manuscript and the UK Biobank; its terms also bind users to UK
Biobank's terms of use and forbid re-identifying participants (summary statistics only).

The API is the browser's own backend (``main.genebass.org/api``): public, but undocumented,
so a change there surfaces as a failed source, never as "no association". It answers 500 for
a gene outside the exome analysis (non-coding genes), which cannot be told from a server
fault, so the caller decides when to ask.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import get_with_retry

_BASE = "https://main.genebass.org/api"
PAPER_PMID = "36778668"
MASKS = ("pLoF", "missense|LC")
# Rows kept per mask: anything near significance (Genebass's own gene-level cache bound).
_KEEP_P = 1e-4


class BurdenRow(BaseModel):
    """One gene × phenotype result, with the phenotype's name and case counts."""

    phenotype: str = ""
    detail: str = ""  # the coding's meaning, for a categorical field
    trait_type: str = ""
    category: str = ""
    n_cases: int | None = None
    n_controls: int | None = None
    p_skato: float | None = None
    p_burden: float | None = None
    beta_burden: float | None = None
    total_variants: int | None = None


class MaskResult(BaseModel):
    mask: str
    phenotypes_tested: int = 0
    caf: float | None = None  # cumulative allele frequency of the mask
    mean_coverage: float | None = None
    rows: list[BurdenRow] = []


class GenebassBundle(BaseModel):
    ensembl_id: str
    masks: list[MaskResult] = []
    source_link: str = ""


async def _json(client: httpx.AsyncClient, path: str, **params: Any) -> Any:
    resp = await get_with_retry(client, f"{_BASE}{path}", params=params or None)
    if resp.status_code != 200:
        raise MCPToolError(f"Genebass {path} returned HTTP {resp.status_code}")
    return resp.json()


def _key(r: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(r.get(k) for k in ("trait_type", "phenocode", "pheno_sex", "coding", "modifier"))


def _row(r: dict[str, Any], phenotype: dict[str, Any]) -> BurdenRow:
    return BurdenRow(
        phenotype=phenotype.get("description") or str(r.get("phenocode") or ""),
        detail=phenotype.get("coding_description") or "",
        trait_type=r.get("trait_type") or "",
        category=phenotype.get("category") or "",
        n_cases=phenotype.get("n_cases"),
        n_controls=phenotype.get("n_controls"),
        p_skato=r.get("Pvalue"),
        p_burden=r.get("Pvalue_Burden"),
        beta_burden=r.get("BETA_Burden"),
        total_variants=r.get("total_variants"),
    )


async def get_gene_burden(ensembl_id: str) -> GenebassBundle:
    """The gene's burden results per mask (pLoF; missense and low-confidence pLoF), rows with
    SKAT-O or burden p < 1e-4, with how many phenotypes were tested and the mask's cumulative
    allele frequency and coverage (the paper's QC inputs). Raises on any HTTP failure."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        phenotypes = {_key(p): p for p in await _json(client, "/phenotypes")}
        qc = {
            q.get("annotation"): q
            for q in await _json(client, f"/gene/{ensembl_id}/gene-analysis-qc")
        }
        masks = []
        for mask in MASKS:
            d = await _json(client, f"/phewas/{ensembl_id}", burdenSet=mask)
            rows = d.get("phewas") or []
            kept = [
                r
                for r in rows
                if min(r.get("Pvalue") or 1.0, r.get("Pvalue_Burden") or 1.0) < _KEEP_P
            ]
            q = qc.get(mask) or {}
            masks.append(
                MaskResult(
                    mask=mask,
                    phenotypes_tested=len(rows),
                    caf=q.get("CAF"),
                    mean_coverage=q.get("mean_coverage"),
                    rows=[_row(r, phenotypes.get(_key(r)) or {}) for r in kept],
                )
            )
    return GenebassBundle(
        ensembl_id=ensembl_id,
        masks=masks,
        source_link=f"https://app.genebass.org/gene/{ensembl_id}?burdenSet=pLoF",
    )
