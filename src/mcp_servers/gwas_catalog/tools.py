# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GWAS Catalog tools via the EBI REST API (v1)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import get_with_retry

_BASE = "https://www.ebi.ac.uk/gwas/rest/api"
_SNP_PAGE_SIZE = 200
_ASSOC_PAGE_SIZE = 100
_MAX_CONCURRENT_SNP_REQUESTS = 10


class GWASHit(BaseModel):
    association_id: str
    rs_id: str
    pvalue: float
    pvalue_mantissa: int
    pvalue_exponent: int
    beta_num: float | None = None
    beta_unit: str | None = None
    beta_direction: str | None = None
    or_per_copy: float | None = None
    risk_frequency: str | None = None
    standard_error: float | None = None
    trait: str = ""
    efo_id: str = ""
    efo_uri: str = ""
    study_accession: str = ""
    pmid: str = ""
    pub_date: str = ""
    journal: str = ""
    title: str = ""
    initial_sample_size: str = ""


class GWASBundle(BaseModel):
    gene_symbol: str
    hits: list[GWASHit]
    source_link: str
    text: str
    dropped_off_target: int = 0
    all_traits: list[str] = []
    kept_traits: list[str] = []


async def _fetch_snp_page(client: httpx.AsyncClient, gene: str, page: int) -> tuple[list[str], int]:
    """Return (rsIds, total_pages) for one page of gene SNPs."""
    url = (
        f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene"
        f"?geneName={gene}&size={_SNP_PAGE_SIZE}&page={page}"
    )
    resp = await get_with_retry(client, url)
    if resp.status_code != 200:
        raise MCPToolError(f"GWAS Catalog SNP search returned HTTP {resp.status_code}")
    data = resp.json()
    snps = data.get("_embedded", {}).get("singleNucleotidePolymorphisms", [])
    rs_ids = [s["rsId"] for s in snps if "rsId" in s]
    page_info = data.get("page", {})
    total_pages = page_info.get("totalPages", 1)
    return rs_ids, total_pages


async def _fetch_associations_for_snp(
    client: httpx.AsyncClient, rs_id: str, p_threshold: float, semaphore: asyncio.Semaphore
) -> list[dict[str, Any]]:
    """Return filtered associations for one SNP (all pages, p ≤ threshold)."""
    results: list[dict[str, Any]] = []
    page = 0
    while True:
        url = (
            f"{_BASE}/singleNucleotidePolymorphisms/{rs_id}/associations"
            f"?projection=associationByStudy&size={_ASSOC_PAGE_SIZE}&page={page}"
        )
        async with semaphore:
            resp = await get_with_retry(client, url)
        if resp.status_code != 200:
            break
        data = resp.json()
        assocs = data.get("_embedded", {}).get("associations", [])
        for a in assocs:
            pval = a.get("pvalue")
            if pval is not None and pval <= p_threshold:
                results.append({"rs_id": rs_id, **a})
        page_info = data.get("page", {})
        total_pages = page_info.get("totalPages", 1)
        if page >= total_pages - 1:
            break
        page += 1
    return results


def _parse_hit(rs_id: str, a: dict[str, Any]) -> GWASHit:
    study = a.get("study") or {}
    efo_traits = a.get("efoTraits") or []
    efo = efo_traits[0] if efo_traits else {}
    pub = study.get("publicationInfo") or {}
    self_href = (a.get("_links") or {}).get("self", {}).get("href", "")
    assoc_id = self_href.rstrip("/").rsplit("/", 1)[-1]
    return GWASHit(
        association_id=assoc_id,
        rs_id=rs_id,
        pvalue=a.get("pvalue", 0.0),
        pvalue_mantissa=a.get("pvalueMantissa", 0),
        pvalue_exponent=a.get("pvalueExponent", 0),
        beta_num=a.get("betaNum"),
        beta_unit=a.get("betaUnit"),
        beta_direction=a.get("betaDirection"),
        or_per_copy=a.get("orPerCopyNum"),
        risk_frequency=a.get("riskFrequency"),
        standard_error=a.get("standardError"),
        trait=(study.get("diseaseTrait") or {}).get("trait", efo.get("trait", "")),
        efo_id=efo.get("shortForm", ""),
        efo_uri=efo.get("uri", ""),
        study_accession=study.get("accessionId", ""),
        pmid=pub.get("pubmedId", ""),
        pub_date=pub.get("publicationDate", ""),
        journal=pub.get("publication", ""),
        title=pub.get("title", ""),
        initial_sample_size=study.get("initialSampleSize", ""),
    )


async def get_gwas_associations(
    gene_symbol: str,
    p_threshold: float = 5e-8,
    max_snps: int = 200,
    *,
    efo_ids: set[str] | None = None,
    trait_terms: list[str] | None = None,
    max_hits: int = 25,
) -> GWASBundle:
    """Fetch genome-wide significant GWAS associations for a gene from EBI GWAS Catalog.

    Two-hop strategy: gene → SNPs (paginated) → associations with study details
    (projection=associationByStudy, concurrent per SNP).
    p_threshold defaults to 5e-8 (genome-wide significance).

    When efo_ids or trait_terms are provided, only associations matching the target
    indication (by EFO ontology membership or trait substring) are kept. All others
    are counted in dropped_off_target. Backward compatible: pass neither to get all
    associations as before.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Collect all rsIds (paginate until done or max_snps reached)
        rs_ids: list[str] = []
        page = 0
        total_pages = 1
        while page < total_pages and len(rs_ids) < max_snps:
            page_rs, total_pages = await _fetch_snp_page(client, gene_symbol, page)
            rs_ids.extend(page_rs)
            page += 1
        rs_ids = list(dict.fromkeys(rs_ids))[:max_snps]  # deduplicate, preserve order

        # Fetch associations concurrently for all SNPs, bounded so we don't overwhelm
        # the EBI API with hundreds of simultaneous connections (causes read timeouts).
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SNP_REQUESTS)
        tasks = [_fetch_associations_for_snp(client, rs, p_threshold, semaphore) for rs in rs_ids]
        results_per_snp: list[list[dict[str, Any]]] = await asyncio.gather(*tasks)

    # Flatten, deduplicate by association_id, parse
    seen: set[str] = set()
    hits: list[GWASHit] = []
    for rs_id, assoc_list in zip(rs_ids, results_per_snp, strict=True):
        for a in assoc_list:
            self_href = (a.get("_links") or {}).get("self", {}).get("href", "")
            if self_href and self_href in seen:
                continue
            seen.add(self_href)
            hits.append(_parse_hit(rs_id, a))

    # Collect all distinct traits before any disease-scope filtering.
    all_traits = list(dict.fromkeys(h.trait for h in hits if h.trait))

    # Apply disease-scope filter when requested.
    dropped_off_target = 0
    if efo_ids is not None or trait_terms:
        kept: list[GWASHit] = []
        lc_terms = [t.lower() for t in (trait_terms or [])]
        for h in hits:
            if (
                efo_ids
                and h.efo_id in efo_ids
                or lc_terms
                and any(t in h.trait.lower() for t in lc_terms)
            ):
                kept.append(h)
            else:
                dropped_off_target += 1
        hits = kept

    hits.sort(key=lambda h: h.pvalue)
    hits = hits[:max_hits]

    kept_traits = list(dict.fromkeys(h.trait for h in hits if h.trait))

    if hits:
        top_traits = kept_traits[:5]
        text = (
            f"GWAS Catalog: {len(hits)} genome-wide significant association(s) "
            f"(p≤{p_threshold:.0e}) for {gene_symbol} matched the target indication. "
            f"Top traits: {', '.join(top_traits) or 'N/A'}. "
            f"Lead variant p-value: {hits[0].pvalue:.2e} ({hits[0].trait})."
        )
        if dropped_off_target:
            off_sample = ", ".join(t for t in all_traits if t not in set(kept_traits))[:200]
            text += (
                f" {dropped_off_target} association(s) at the {gene_symbol} locus "
                f"matched other traits (e.g. {off_sample or 'various'}) and were excluded."
            )
    elif dropped_off_target:
        off_sample = ", ".join(all_traits[:5])
        text = (
            f"GWAS Catalog: {len(all_traits)} distinct trait(s) found at the {gene_symbol} "
            f"locus (e.g. {off_sample or 'various'}); 0 matched the target indication "
            f"(EFO descendants or trait terms). "
            f"All {dropped_off_target} association(s) excluded as off-indication."
        )
    else:
        text = (
            f"No genome-wide significant GWAS associations (p≤{p_threshold:.0e}) "
            f"found for {gene_symbol} in the EBI GWAS Catalog."
        )

    return GWASBundle(
        gene_symbol=gene_symbol,
        hits=hits,
        source_link=f"https://www.ebi.ac.uk/gwas/genes/{gene_symbol}",
        text=text,
        dropped_off_target=dropped_off_target,
        all_traits=all_traits,
        kept_traits=kept_traits,
    )
