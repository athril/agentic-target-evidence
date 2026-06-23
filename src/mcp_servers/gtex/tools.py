# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GTEx tissue expression + HPA protein localization tools."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_GTEX_GENE_API = "https://gtexportal.org/api/v2/reference/gene"
_GTEX_EXPR_API = "https://gtexportal.org/api/v2/expression/medianGeneExpression"
_HPA_SEARCH_API = "https://www.proteinatlas.org/api/search_download.php"
_UNIPROT_API = "https://rest.uniprot.org/uniprotkb/{accession}.json"


class TissueExpression(BaseModel):
    tissue: str
    median_tpm: float


class ExpressionBundle(BaseModel):
    gene_symbol: str
    ensembl_id: str = ""
    uniprot_accession: str = ""
    gtex_expressions: list[TissueExpression] = []  # all tissues sorted by median TPM
    hpa_tissue_specificity: str = ""  # e.g. "Low tissue specificity"
    hpa_subcellular_location: list[str] = []  # e.g. ["Nucleus", "Cytoplasm"]
    hpa_rna_tissue_category: str = ""
    source_link: str = ""
    text: str = ""


async def get_expression(gene_symbol: str, ensembl_id: str = "") -> ExpressionBundle:
    """Fetch GTEx median TPM per tissue and HPA/UniProt protein localization for a gene."""
    gtex_data, hpa_data = await asyncio.gather(
        _fetch_gtex(gene_symbol),
        _fetch_hpa(gene_symbol),
    )

    all_tissues = sorted(gtex_data, key=lambda x: x.median_tpm, reverse=True)
    top5_text = ", ".join(f"{t.tissue}={t.median_tpm:.1f}" for t in all_tissues[:5])

    # Resolve UniProt accession from HPA data then fetch subcellular location
    uniprot_acc = hpa_data.get("uniprot", "")
    subcellular = hpa_data.get("subcellular_location", [])
    if not subcellular and uniprot_acc:
        subcellular = await _fetch_uniprot_subcellular(uniprot_acc)

    hpa_text = ""
    if hpa_data.get("tissue_specificity"):
        hpa_text = f" HPA specificity: {hpa_data['tissue_specificity']}."
    if subcellular:
        hpa_text += f" Subcellular: {', '.join(subcellular[:4])}."

    ensg = ensembl_id or hpa_data.get("ensembl_id", "")
    return ExpressionBundle(
        gene_symbol=gene_symbol,
        ensembl_id=ensg,
        uniprot_accession=uniprot_acc,
        gtex_expressions=all_tissues,
        hpa_tissue_specificity=hpa_data.get("tissue_specificity", ""),
        hpa_subcellular_location=subcellular,
        hpa_rna_tissue_category=hpa_data.get("rna_tissue_category", ""),
        source_link=f"https://gtexportal.org/home/gene/{gene_symbol}",
        text=f"GTEx top tissues (median TPM): {top5_text}.{hpa_text}",
    )


async def _resolve_gencode_id(gene_symbol: str) -> str:
    """Resolve a gene symbol to a versioned GTEx gencodeId (e.g. ENSG00000169174.10)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            _GTEX_GENE_API, params={"geneId": gene_symbol, "datasetId": "gtex_v8"}
        )
    if resp.status_code != 200:
        return gene_symbol
    items = resp.json().get("data") or []
    return items[0].get("gencodeId", gene_symbol) if items else gene_symbol


async def _fetch_gtex(gene_symbol: str) -> list[TissueExpression]:
    gencode_id = await _resolve_gencode_id(gene_symbol)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            _GTEX_EXPR_API, params={"gencodeId": gencode_id, "datasetId": "gtex_v8"}
        )

    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise MCPToolError(f"GTEx API returned HTTP {resp.status_code} for {gene_symbol}")

    data = resp.json()
    entries = data.get("data") or []
    result = []
    for entry in entries:
        tissue = entry.get("tissueSiteDetailId", "")
        tpm = entry.get("median", 0.0)
        if tissue:
            result.append(TissueExpression(tissue=tissue, median_tpm=float(tpm)))
    return result


async def _fetch_hpa(gene_symbol: str) -> dict[str, Any]:
    """Query HPA search API for RNA tissue specificity and UniProt accession."""
    params = {
        "search": gene_symbol,
        "format": "json",
        "columns": "g,eg,up,rnats",
        "compress": "no",
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(_HPA_SEARCH_API, params=params)

    if resp.status_code != 200:
        return {}

    try:
        entries = resp.json()
    except Exception:
        return {}

    # Find the exact gene match (search may return partial matches)
    match = next((e for e in entries if e.get("Gene", "").upper() == gene_symbol.upper()), None)
    if not match:
        return {}

    uniprot_list = match.get("Uniprot") or []
    uniprot_acc = (
        uniprot_list[0]
        if isinstance(uniprot_list, list) and uniprot_list
        else str(uniprot_list or "")
    )

    specificity = match.get("RNA tissue specificity", "")
    return {
        "ensembl_id": match.get("Ensembl", ""),
        "uniprot": uniprot_acc,
        "tissue_specificity": specificity,
        "subcellular_location": [],  # populated separately from UniProt
        "rna_tissue_category": specificity,
    }


async def _fetch_uniprot_subcellular(accession: str) -> list[str]:
    """Return subcellular location strings from UniProt for a given accession."""
    url = _UNIPROT_API.format(accession=accession)
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url)

    if resp.status_code != 200:
        return []

    try:
        data = resp.json()
    except Exception:
        return []

    locations: list[str] = []
    for comment in data.get("comments", []):
        if comment.get("commentType") == "SUBCELLULAR LOCATION":
            for loc_entry in comment.get("subcellularLocations", []):
                val = loc_entry.get("location", {}).get("value", "")
                if val:
                    locations.append(val)
    return locations
