# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GWAS Catalog tools via the EBI REST API v2.

The legacy v1 API (``/gwas/rest/api/singleNucleotidePolymorphisms/...``) is deprecated and is
intentionally rate-limited ahead of retirement: it answers HTTP 429 to almost every request,
which the old gene → SNP → per-SNP fan-out silently read as "no associations". v2 serves
gene-mapped associations directly, sortable by p-value, so one paged query replaces the fan-out.
See https://gwas-catalog.github.io/blog/rest-api-v2-migration-guide (v2 limit: 15 req/s).
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import get_with_retry

logger = logging.getLogger(__name__)

_BASE = "https://www.ebi.ac.uk/gwas/rest/api/v2"
_PAGE_SIZE = 100
_PAGE_BATCH = 4  # pages fetched concurrently when scanning for scoped (disease-filtered) hits
_MAX_ATTEMPTS = 4  # per request, on HTTP 429 / 5xx
_BACKOFF_S = 1.0  # doubles per attempt unless the server sends Retry-After
_MAX_CONCURRENT_ENRICH = 5  # study/publication lookups; stays well under 15 req/s


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


async def _get_json(
    client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """GET a v2 resource, retrying 429/5xx with backoff. None on 404; raises otherwise."""
    delay = _BACKOFF_S
    status = 0
    for attempt in range(_MAX_ATTEMPTS):
        resp = await get_with_retry(client, f"{_BASE}{path}", params=params)
        status = resp.status_code
        if status == 200:
            data: dict[str, Any] = resp.json()
            return data
        if status == 404:
            return None
        if status != 429 and status < 500:
            break
        if attempt < _MAX_ATTEMPTS - 1:
            retry_after = resp.headers.get("Retry-After", "")
            await asyncio.sleep(float(retry_after) if retry_after.isdigit() else delay)
            delay *= 2
    raise MCPToolError(f"GWAS Catalog v2 {path} returned HTTP {status}")


def _log10_p(a: dict[str, Any]) -> float:
    """log10(p) from mantissa/exponent — ``p_value`` underflows to 0.0 below ~1e-308."""
    mantissa = int(a.get("pvalue_mantissa") or 0)
    exponent = a.get("pvalue_exponent")
    if mantissa > 0 and exponent is not None:
        return math.log10(mantissa) + int(exponent)
    p = float(a.get("p_value") or 0.0)
    return math.log10(p) if p > 0 else -math.inf


def _parse_beta(beta: str | None) -> tuple[float | None, str | None, str | None]:
    """'0.7471 unit increase' → (0.7471, 'unit', 'increase'); '-' / '' → (None, None, None)."""
    parts = (beta or "").split()
    if not parts:
        return None, None, None
    try:
        num = float(parts[0])
    except ValueError:
        return None, None, None
    direction = parts[-1] if len(parts) > 1 and parts[-1] in ("increase", "decrease") else None
    unit_parts = parts[1:-1] if direction else parts[1:]
    return num, " ".join(unit_parts) or None, direction


def _parse_hit(a: dict[str, Any]) -> GWASHit:
    efo = (a.get("efo_traits") or [{}])[0]
    efo_id = efo.get("efo_id", "")
    snps = a.get("snp_allele") or []
    beta_num, beta_unit, beta_direction = _parse_beta(a.get("beta"))
    odds = a.get("or_per_copy_num") or a.get("or_value")
    reported = a.get("reported_trait") or []
    return GWASHit(
        association_id=str(a.get("association_id", "")),
        rs_id=snps[0].get("rs_id", "") if snps else "",
        pvalue=a.get("p_value") or 0.0,
        pvalue_mantissa=a.get("pvalue_mantissa") or 0,
        pvalue_exponent=a.get("pvalue_exponent") or 0,
        beta_num=beta_num,
        beta_unit=beta_unit,
        beta_direction=beta_direction,
        or_per_copy=float(odds) if odds is not None and odds not in ("", "-") else None,
        risk_frequency=a.get("risk_frequency"),
        trait=reported[0] if reported else efo.get("efo_trait", ""),
        efo_id=efo_id,
        efo_uri=f"http://www.ebi.ac.uk/efo/{efo_id}" if efo_id else "",
        study_accession=a.get("accession_id", ""),
        pmid=str(a.get("pubmed_id") or ""),
    )


async def _fetch_significant(
    client: httpx.AsyncClient,
    gene: str,
    p_threshold: float,
    max_associations: int,
    enough: int | None,
) -> list[dict[str, Any]]:
    """Gene-mapped associations with p ≤ threshold, most significant first.

    v2 is slow per row (~0.1 s server-side), so: stop as soon as ``enough`` significant rows
    are in hand (unscoped queries only need the top hits), and otherwise fetch the remaining
    pages in small concurrent batches until a page contains a non-significant row.
    """
    log_threshold = math.log10(p_threshold)

    async def page(n: int) -> tuple[list[dict[str, Any]], int]:
        params = {
            "mapped_gene": gene,
            "sort": "p_value",
            "direction": "asc",
            "size": _PAGE_SIZE,
            "page": n,
        }
        data = await _get_json(client, "/associations", params) or {}
        rows = (data.get("_embedded") or {}).get("associations") or []
        return rows, int((data.get("page") or {}).get("totalPages", 0))

    rows, total_pages = await page(0)
    out = [a for a in rows if _log10_p(a) <= log_threshold]
    done = len(out) < len(rows) or (enough is not None and len(out) >= enough)
    last_page = min(total_pages, -(-max_associations // _PAGE_SIZE)) - 1
    next_page = 1
    while not done and next_page <= last_page:
        batch = range(next_page, min(next_page + _PAGE_BATCH, last_page + 1))
        for rows, _ in await asyncio.gather(*(page(n) for n in batch)):
            significant = [a for a in rows if _log10_p(a) <= log_threshold]
            out.extend(significant)
            # Sorted ascending: a page with a non-significant row ends the significant set.
            done = done or len(significant) < len(rows)
        next_page = batch.stop
    return out[:max_associations]


async def _enrich(client: httpx.AsyncClient, hits: list[GWASHit]) -> None:
    """Fill sample size and publication details for the kept hits. Best effort, never fatal."""
    sem = asyncio.Semaphore(_MAX_CONCURRENT_ENRICH)

    async def fetch(path: str) -> dict[str, Any] | None:
        async with sem:
            try:
                return await _get_json(client, path)
            except (MCPToolError, httpx.HTTPError) as exc:
                logger.warning("GWAS Catalog enrichment %s failed: %s", path, exc)
                return None

    accessions = list(dict.fromkeys(h.study_accession for h in hits if h.study_accession))
    pmids = list(dict.fromkeys(h.pmid for h in hits if h.pmid))
    results = await asyncio.gather(
        *(fetch(f"/studies/{a}") for a in accessions), *(fetch(f"/publications/{p}") for p in pmids)
    )
    studies = dict(zip(accessions, results[: len(accessions)], strict=True))
    pubs = dict(zip(pmids, results[len(accessions) :], strict=True))
    for h in hits:
        study = studies.get(h.study_accession) or {}
        pub = pubs.get(h.pmid) or {}
        h.initial_sample_size = study.get("initial_sample_size") or ""
        h.pub_date = pub.get("publication_date") or ""
        h.journal = pub.get("journal") or ""
        h.title = pub.get("title") or ""


def _p_str(h: GWASHit) -> str:
    return f"{h.pvalue_mantissa}e{h.pvalue_exponent}" if h.pvalue_mantissa else f"{h.pvalue:.2e}"


async def get_gwas_associations(
    gene_symbol: str,
    p_threshold: float = 5e-8,
    max_associations: int = 1000,
    *,
    efo_ids: set[str] | None = None,
    trait_terms: list[str] | None = None,
    max_hits: int = 25,
) -> GWASBundle:
    """Fetch genome-wide significant GWAS associations for a gene from EBI GWAS Catalog (v2).

    One paged query for associations whose mapped gene is ``gene_symbol``, sorted by p-value;
    scanning stops at the first non-significant association or after ``max_associations``.
    p_threshold defaults to 5e-8 (genome-wide significance).

    When efo_ids or trait_terms are provided, only associations matching the target
    indication (by EFO ontology membership or trait substring) are kept. All others
    are counted in dropped_off_target. Backward compatible: pass neither to get all
    associations as before. Study sample size and publication details are fetched only
    for the hits that are returned.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        scoped = efo_ids is not None or bool(trait_terms)
        raw = await _fetch_significant(
            client, gene_symbol, p_threshold, max_associations, None if scoped else max_hits
        )

        seen: set[str] = set()
        hits: list[GWASHit] = []
        for a in raw:
            hit = _parse_hit(a)
            if hit.association_id in seen:
                continue
            seen.add(hit.association_id)
            hits.append(hit)

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

    hits.sort(key=lambda h: (h.pvalue_exponent, h.pvalue_mantissa))
    hits = hits[:max_hits]
    async with httpx.AsyncClient(timeout=60.0) as client:
        await _enrich(client, hits)

    kept_traits = list(dict.fromkeys(h.trait for h in hits if h.trait))

    if hits:
        top_traits = kept_traits[:5]
        text = (
            f"GWAS Catalog: {len(hits)} genome-wide significant association(s) "
            f"(p≤{p_threshold:.0e}) for {gene_symbol} matched the target indication. "
            f"Top traits: {', '.join(top_traits) or 'N/A'}. "
            f"Lead variant p-value: {_p_str(hits[0])} ({hits[0].trait})."
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
