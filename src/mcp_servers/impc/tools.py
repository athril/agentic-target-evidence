# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""IMPC (International Mouse Phenotyping Consortium) knockout-mouse phenotype tools.

Live, keyless SOLR REST API — a per-gene query, no bulk download (mirrors the
gnomAD/GWAS Catalog live-API shape rather than ClinGen's bulk-file pattern).
This is a different evidentiary axis from the curated human gene-disease
validity sources (OMIM/GenCC/Orphanet/ClinGen): in-vivo knockout phenotype and
viability outcome, the whole-organism analogue of DepMap's cell-line
dependency signal. Feeds ``EvidenceType.FUNCTIONAL_GENOMICS``, not
``GENETICS``.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import get_with_retry

_SOLR_BASE = "https://www.ebi.ac.uk/mi/impc/solr/genotype-phenotype/select"
_GENE_PAGE = "https://www.mousephenotype.org/data/genes/{mgi}"
_MAX_ROWS = 200

# Heuristic keyword match against mp_term_name to derive an overall viability
# call — IMPC has no single "viability" field on this core, but lethality/
# subviability phenotypes are reported as MP terms like the ones below.
_LETHAL_KEYWORDS = ("lethality", "lethal")
_SUBVIABLE_KEYWORDS = ("subviable",)


class ImpcPhenotype(BaseModel):
    mp_term_name: str
    mp_term_id: str = ""
    p_value: float | None = None
    zygosity: str = ""
    life_stage_name: str = ""
    procedure_name: str = ""


class ImpcBundle(BaseModel):
    gene_symbol: str
    viability: str = "unknown"  # "lethal" | "subviable" | "viable" | "unknown"
    phenotypes: list[ImpcPhenotype] = []
    total: int = 0
    source_link: str = ""
    text: str = ""


def _derive_viability(phenotypes: list[ImpcPhenotype]) -> str:
    names = [p.mp_term_name.lower() for p in phenotypes]
    if any(kw in n for n in names for kw in _LETHAL_KEYWORDS):
        return "lethal"
    if any(kw in n for n in names for kw in _SUBVIABLE_KEYWORDS):
        return "subviable"
    if phenotypes:
        return "viable"
    return "unknown"


def _mouse_symbol(gene_symbol: str) -> str:
    """Mouse (MGI) capitalisation of a human symbol: PCSK9 -> Pcsk9, SEPTIN9 -> Septin9."""
    return gene_symbol[:1].upper() + gene_symbol[1:].lower()


def _query(gene_symbol: str, mgi_id: str) -> str:
    # marker_symbol is the mouse symbol and matches case-sensitively, so a human symbol
    # alone ("PCSK9") silently returns nothing; the MGI accession is exact when known.
    if mgi_id:
        return f'marker_accession_id:"{mgi_id}"'
    symbols = dict.fromkeys([gene_symbol, _mouse_symbol(gene_symbol)])
    return "marker_symbol:(" + " OR ".join(f'"{s}"' for s in symbols) + ")"


async def get_impc_phenotypes(gene_symbol: str, mgi_id: str = "") -> ImpcBundle:
    """Fetch IMPC statistically significant knockout-mouse phenotype calls for a gene.

    ``gene_symbol`` may be the human or mouse symbol; pass the mouse ortholog's MGI
    accession (e.g. HGNC ``mgd_id``) as ``mgi_id`` for an exact match.
    """
    params = {
        "q": _query(gene_symbol, mgi_id),
        "rows": _MAX_ROWS,
        "wt": "json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await get_with_retry(client, _SOLR_BASE, params=params)
        except httpx.HTTPError as exc:
            raise MCPToolError(f"IMPC API request failed: {exc}") from exc

    if resp.status_code != 200:
        raise MCPToolError(f"IMPC API returned HTTP {resp.status_code}")

    docs = ((resp.json().get("response") or {}).get("docs")) or []
    phenotypes = [
        ImpcPhenotype(
            mp_term_name=doc.get("mp_term_name", ""),
            mp_term_id=doc.get("mp_term_id", ""),
            p_value=doc.get("p_value"),
            zygosity=doc.get("zygosity", ""),
            life_stage_name=doc.get("life_stage_name", ""),
            procedure_name=doc.get("procedure_name", ""),
        )
        for doc in docs
        if doc.get("mp_term_name")
    ]

    viability = _derive_viability(phenotypes)
    mgi = next((d["marker_accession_id"] for d in docs if d.get("marker_accession_id")), mgi_id)
    source_link = _GENE_PAGE.format(mgi=mgi) if mgi else f"{_SOLR_BASE}?{urlencode(params)}"

    if not phenotypes:
        text = (
            f"No statistically significant IMPC knockout-mouse phenotypes found for {gene_symbol}."
        )
    else:
        top_terms = ", ".join(dict.fromkeys(p.mp_term_name for p in phenotypes[:5]))
        text = (
            f"IMPC knockout-mouse phenotype for {gene_symbol}: viability={viability}; "
            f"{len(phenotypes)} significant phenotype call(s); top terms: {top_terms}."
        )

    return ImpcBundle(
        gene_symbol=gene_symbol,
        viability=viability,
        phenotypes=phenotypes,
        total=len(phenotypes),
        source_link=source_link,
        text=text,
    )
