# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""PubMed tools using NCBI E-utilities API.

Rate limit: 3 requests/second (NCBI free-tier without API key).
With NCBI_API_KEY set the limit rises to 10 req/s — the semaphore is
adjusted accordingly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, cast

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

logger = logging.getLogger(__name__)

_EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Retry backoff (tests replace it with a no-op).
_backoff_sleep = asyncio.sleep

_rate_lock = asyncio.Semaphore(1)
_request_times: list[float] = []


def _rate() -> int:
    """NCBI allows 3 requests/s per IP without an API key and 10 with one."""
    return 10 if os.environ.get("NCBI_API_KEY") else 3


async def _throttle() -> None:
    """Enforce NCBI sliding-window rate limit."""
    async with _rate_lock:
        now = time.monotonic()
        _request_times[:] = [t for t in _request_times if now - t < 1.0]
        if len(_request_times) >= _rate():
            wait = 1.0 - (now - _request_times[0])
            if wait > 0:
                await asyncio.sleep(wait)
        _request_times.append(time.monotonic())


# Another process on the same IP (a second run, a profile gather) shares NCBI's
# per-IP budget, so 429s can persist for a minute or more: retry long, with jitter.
_MAX_RETRIES = 8
_RETRY_BASE_DELAY = 2.0  # seconds; doubles on each 429
_RETRY_MAX_DELAY = 60.0


async def _get_with_retry(url: str, params: dict[str, Any], *, json: bool) -> httpx.Response:
    """Throttle, GET, and retry on 429 with exponential backoff."""
    delay = _RETRY_BASE_DELAY
    for _attempt in range(_MAX_RETRIES):
        await _throttle()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
        if response.status_code in (429, 500, 502, 503, 504):
            header = response.headers.get("Retry-After", "")
            wait = float(header) if header.replace(".", "", 1).isdigit() else delay
            await _backoff_sleep(min(wait, _RETRY_MAX_DELAY) * random.uniform(0.8, 1.2))
            delay = min(delay * 2, _RETRY_MAX_DELAY)
            continue
        return response
    raise MCPToolError(f"NCBI E-utilities rate-limited (HTTP 429/5xx) after {_MAX_RETRIES} retries")


async def _rate_limited_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET with NCBI rate limiting; returns parsed JSON."""
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key
    params["retmode"] = "json"

    response = await _get_with_retry(url, params, json=True)
    if response.status_code != 200:
        raise MCPToolError(f"NCBI E-utilities returned HTTP {response.status_code}")
    return cast("dict[str, Any]", response.json())


async def _rate_limited_get_text(url: str, params: dict[str, Any]) -> str:
    """GET with NCBI rate limiting; returns raw text (for XML responses)."""
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key

    response = await _get_with_retry(url, params, json=False)
    if response.status_code != 200:
        raise MCPToolError(f"NCBI E-utilities returned HTTP {response.status_code}")
    return response.text


class MeshHeading(BaseModel):
    """One MeSH heading of an article: the descriptor, its qualifiers, and whether either
    is a major topic of the article."""

    descriptor: str
    ui: str = ""
    major: bool = False
    qualifiers: list[str] = []


class ArticleMetadata(BaseModel):
    """Indexing metadata of an article, from the efetch record the abstract comes from.

    ``indexed`` is False when the record has no MeSH headings yet (recent papers are
    indexed weeks after they appear), so a caller can tell "not indexed" from "indexed
    without this heading".
    """

    publication_types: list[str] = []
    mesh_headings: list[MeshHeading] = []
    retracted: bool = False
    indexed: bool = False


_RETRACTION_REF_TYPES = frozenset({"RetractionIn"})


def _parse_metadata(article: ET.Element) -> ArticleMetadata:
    types = [
        " ".join("".join(el.itertext()).split())
        for el in article.findall(".//PublicationTypeList/PublicationType")
    ]
    headings: list[MeshHeading] = []
    for heading in article.findall(".//MeshHeadingList/MeshHeading"):
        descriptor = heading.find("DescriptorName")
        if descriptor is None:
            continue
        name = " ".join("".join(descriptor.itertext()).split())
        if not name:
            continue
        qualifiers = heading.findall("QualifierName")
        headings.append(
            MeshHeading(
                descriptor=name,
                ui=descriptor.get("UI", ""),
                major=descriptor.get("MajorTopicYN") == "Y"
                or any(q.get("MajorTopicYN") == "Y" for q in qualifiers),
                qualifiers=[" ".join("".join(q.itertext()).split()) for q in qualifiers],
            )
        )
    retracted = "Retracted Publication" in types or any(
        c.get("RefType") in _RETRACTION_REF_TYPES
        for c in article.findall(".//CommentsCorrectionsList/CommentsCorrections")
    )
    return ArticleMetadata(
        publication_types=[t for t in types if t],
        mesh_headings=headings,
        retracted=retracted,
        indexed=bool(headings),
    )


def _parse_metadata_from_xml(xml_text: str) -> dict[str, ArticleMetadata]:
    """Parse a PubMed efetch XML response into {pmid: ArticleMetadata}."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    out: dict[str, ArticleMetadata] = {}
    for article in root.iter("PubmedArticle"):
        pmid_el = article.find(".//PMID")
        if pmid_el is None or not pmid_el.text:
            continue
        out[pmid_el.text.strip()] = _parse_metadata(article)
    return out


def _parse_abstracts_from_xml(xml_text: str) -> dict[str, str]:
    """Parse a PubMed efetch XML response and return {pmid: abstract_text}."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}

    abstracts: dict[str, str] = {}
    for article in root.iter("PubmedArticle"):
        pmid_el = article.find(".//PMID")
        if pmid_el is None or not pmid_el.text:
            continue
        pmid = pmid_el.text.strip()

        parts: list[str] = []
        for el in article.findall(".//AbstractText"):
            # itertext(), not .text: .text stops at the first inline child (<i>, <sup>, <sub>),
            # silently truncating abstracts that italicise a gene name or use a superscript.
            text = " ".join("".join(el.itertext()).split())
            if not text:
                continue
            label = el.get("Label")
            parts.append(f"{label}: {text}" if label else text)

        abstracts[pmid] = " ".join(parts)
    return abstracts


async def _fetch_efetch_batch(
    pmids: list[str],
) -> tuple[dict[str, str], dict[str, ArticleMetadata]]:
    """Fetch one efetch XML batch; returns ({pmid: abstract}, {pmid: metadata})."""
    xml_text = await _rate_limited_get_text(
        f"{_EUTILS_BASE}/efetch.fcgi",
        {"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"},
    )
    return _parse_abstracts_from_xml(xml_text), _parse_metadata_from_xml(xml_text)


async def _fetch_abstracts_batch(pmids: list[str]) -> dict[str, str]:
    """Fetch abstracts for a batch of PMIDs via efetch XML; returns {pmid: abstract}."""
    abstracts, _ = await _fetch_efetch_batch(pmids)
    return abstracts


_METADATA_BATCH = 200


async def fetch_article_metadata(pmids: list[str]) -> dict[str, ArticleMetadata]:
    """Publication types, MeSH headings and retraction status for PMIDs, batched.

    For literature rows retrieved before the metadata was kept. A batch that fails after
    retries is logged and skipped; its PMIDs are absent from the result.
    """
    out: dict[str, ArticleMetadata] = {}
    unique = list(dict.fromkeys(p for p in pmids if p))
    for i in range(0, len(unique), _METADATA_BATCH):
        batch = unique[i : i + _METADATA_BATCH]
        try:
            _, metadata = await _fetch_efetch_batch(batch)
        except (MCPToolError, httpx.HTTPError) as exc:
            logger.warning("pubmed metadata batch %d failed: %s", i // _METADATA_BATCH, exc)
            continue
        out.update(metadata)
    return out


class PubMedRecord(BaseModel):
    pmid: str
    title: str
    authors: list[str] = []
    journal: str = ""
    full_journal: str = ""
    issn: str = ""
    essn: str = ""
    pub_year: int | None = None
    abstract: str = ""
    mesh_terms: list[str] = []
    publication_types: list[str] = []
    mesh_headings: list[MeshHeading] = []
    retracted: bool = False
    indexed: bool = False


class PubMedAbstract(BaseModel):
    pmid: str
    title: str
    abstract: str
    authors: list[str] = []
    pub_year: int | None = None


class PubMedFullText(BaseModel):
    pmid: str
    pmc_id: str | None = None
    full_text_url: str | None = None
    available: bool = False
    full_text: str = ""  # PMC Open Access body text; "" if not in the OA subset


_MESH_TERMS_RE = re.compile(r'"([^"]+)"\[MeSH Terms\]')


async def resolve_mesh_term(label: str) -> str | None:
    """Resolve a free-text label to its canonical MeSH descriptor heading.

    Uses PubMed's own Automatic Term Mapping: an esearch with the bare label
    returns a ``querytranslation`` that shows the MeSH descriptor the term maps
    to (e.g. ``"breast cancer"`` -> ``"breast neoplasms"``, ``"pancreatic
    neoplasm"`` -> ``"pancreatic neoplasms"``). Returns that heading, or None
    when the term maps to no MeSH descriptor.
    """
    data = await _rate_limited_get(
        f"{_EUTILS_BASE}/esearch.fcgi",
        {"db": "pubmed", "term": label, "retmax": 0},
    )
    translation = (data.get("esearchresult") or {}).get("querytranslation", "")
    match = _MESH_TERMS_RE.search(translation)
    return match.group(1) if match else None


async def search_pmids(query: str, max_results: int = 200, *, sort: str | None = None) -> list[str]:
    """PMIDs matching ``query`` (one esearch call, no summaries)."""
    params: dict[str, Any] = {"db": "pubmed", "term": query, "retmax": max_results}
    if sort:
        params["sort"] = sort
    data = await _rate_limited_get(f"{_EUTILS_BASE}/esearch.fcgi", params)
    return [str(i) for i in (data.get("esearchresult") or {}).get("idlist") or []]


_MESH_UID_PREFIX = "68"  # the MeSH database's numeric uid of descriptor Dnnnnnn is 68nnnnnn


async def mesh_tree_numbers(descriptor_uis: list[str]) -> dict[str, list[str]]:
    """MeSH tree numbers of descriptors by their UI ("D003424" → ["C06.405.205.731.500",
    ...]), batched. A batch that fails is logged and skipped."""
    uids = {
        f"{_MESH_UID_PREFIX}{ui[1:]}": ui
        for ui in dict.fromkeys(descriptor_uis)
        if ui.startswith("D") and ui[1:].isdigit()
    }
    out: dict[str, list[str]] = {}
    keys = list(uids)
    for i in range(0, len(keys), _METADATA_BATCH):
        batch = keys[i : i + _METADATA_BATCH]
        try:
            data = await _rate_limited_get(
                f"{_EUTILS_BASE}/esummary.fcgi", {"db": "mesh", "id": ",".join(batch)}
            )
        except (MCPToolError, httpx.HTTPError) as exc:
            logger.warning("mesh tree-number batch %d failed: %s", i // _METADATA_BATCH, exc)
            continue
        result = data.get("result") or {}
        for uid in batch:
            record = result.get(uid) or {}
            trees = [
                str(link.get("treenum"))
                for link in record.get("ds_idxlinks") or []
                if link.get("treenum")
            ]
            out[uids[uid]] = trees
    return out


async def search_pubmed(
    query: str, max_results: int = 500, *, sort: str | None = None
) -> list[PubMedRecord]:
    """Search PubMed and return structured records including abstracts.

    ``sort`` is an E-utilities sort order ("relevance" = PubMed Best Match,
    "pub_date"); the default is PubMed's own default order.
    """
    params: dict[str, Any] = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "usehistory": "y",
    }
    if sort:
        params["sort"] = sort
    search_data = await _rate_limited_get(f"{_EUTILS_BASE}/esearch.fcgi", params)
    esearch = search_data.get("esearchresult", {})
    ids: list[str] = esearch.get("idlist", [])
    if not ids:
        return []

    # Fetch summaries in batches of 200 (URL length limit)
    _BATCH = 200
    result_map: dict[str, Any] = {}
    # A batch that still fails after retries loses only its own records (summaries) or
    # abstracts — never the whole search.
    failed_batches = 0
    for i in range(0, len(ids), _BATCH):
        batch = ids[i : i + _BATCH]
        try:
            summary_data = await _rate_limited_get(
                f"{_EUTILS_BASE}/esummary.fcgi",
                {"db": "pubmed", "id": ",".join(batch)},
            )
        except MCPToolError as exc:
            failed_batches += 1
            logger.warning("pubmed esummary batch %d failed: %s", i // _BATCH, exc)
            continue
        result_map.update(summary_data.get("result", {}))

    # Batch-fetch abstracts and indexing metadata via efetch XML (same batch size)
    abstract_map: dict[str, str] = {}
    metadata_map: dict[str, ArticleMetadata] = {}
    for i in range(0, len(ids), _BATCH):
        batch = ids[i : i + _BATCH]
        try:
            abstracts, metadata = await _fetch_efetch_batch(batch)
            abstract_map.update(abstracts)
            metadata_map.update(metadata)
        except MCPToolError as exc:
            failed_batches += 1
            logger.warning("pubmed efetch batch %d failed: %s", i // _BATCH, exc)
    if failed_batches and not result_map:
        raise MCPToolError(f"PubMed: every summary batch failed for {len(ids)} hits")

    records: list[PubMedRecord] = []
    for pmid in ids:
        item = result_map.get(pmid)
        if not item or not isinstance(item, dict):
            continue
        pub_date = item.get("pubdate", "")
        try:
            pub_year: int | None = int(pub_date[:4])
        except (ValueError, IndexError):
            pub_year = None

        meta = metadata_map.get(pmid) or ArticleMetadata()
        # esummary lists publication types too; it covers a record whose efetch batch failed.
        pub_types = meta.publication_types or [
            str(t) for t in item.get("pubtype") or [] if isinstance(t, str)
        ]
        records.append(
            PubMedRecord(
                pmid=pmid,
                title=item.get("title", ""),
                authors=[a.get("name", "") for a in item.get("authors", [])],
                journal=item.get("source", ""),
                full_journal=item.get("fulljournalname", ""),
                issn=item.get("issn", ""),
                essn=item.get("essn", ""),
                pub_year=pub_year,
                abstract=abstract_map.get(pmid, ""),
                mesh_terms=[h.descriptor for h in meta.mesh_headings],
                publication_types=pub_types,
                mesh_headings=meta.mesh_headings,
                retracted=meta.retracted or "Retracted Publication" in pub_types,
                indexed=meta.indexed,
            )
        )
    return records


async def fetch_abstract(pmid: str) -> PubMedAbstract:
    """Fetch the abstract for a single PMID."""
    # Fetch summary for metadata
    summary_data = await _rate_limited_get(
        f"{_EUTILS_BASE}/esummary.fcgi",
        {"db": "pubmed", "id": pmid},
    )
    result = summary_data.get("result", {}).get(pmid, {})
    pub_date = result.get("pubdate", "")
    try:
        pub_year: int | None = int(pub_date[:4])
    except (ValueError, IndexError):
        pub_year = None

    # Fetch abstract text via efetch XML
    abstract_map = await _fetch_abstracts_batch([pmid])

    return PubMedAbstract(
        pmid=pmid,
        title=result.get("title", ""),
        abstract=abstract_map.get(pmid, ""),
        authors=[a.get("name", "") for a in result.get("authors", [])],
        pub_year=pub_year,
    )


_PMC_ARTICLE_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC{}/"


def _extract_pmc_id(data: dict[str, Any]) -> str | None:
    """Pull the PMC id from a ``linkname=pubmed_pmc`` elink response, or None.

    The PMC id lives in ``linksets[].linksetdbs[].links`` of the ``pubmed_pmc``
    link; an article not in PMC has no ``linksetdbs`` at all. ``linksets[].ids``
    only echoes the queried PMID — a different identifier that must never be
    substituted, since that would be fabricated attribution.
    """
    for linkset in data.get("linksets", []):
        for linksetdb in linkset.get("linksetdbs", []):
            if linksetdb.get("linkname") != "pubmed_pmc":
                continue
            links = linksetdb.get("links") or []
            if links and isinstance(links[0], (str, int)):
                return str(links[0])
    return None


# JATS subtrees that carry no useful screening signal (citations, floats, math).
_JATS_SKIP_TAGS = {"ref-list", "table-wrap", "table", "fig", "disp-formula", "inline-formula"}


def _collect_jats_text(el: ET.Element, out: list[str]) -> None:
    """Recursively gather section titles and paragraph text from a JATS subtree.

    Skips reference lists, figures, tables, and formulae so the result reads as
    the article's prose (intro/methods/results/discussion), not its apparatus.
    """
    for child in el:
        tag = child.tag.rsplit("}", 1)[-1]  # strip any namespace
        if tag in _JATS_SKIP_TAGS:
            continue
        if tag in ("title", "p"):
            text = " ".join(t.strip() for t in child.itertext() if t and t.strip())
            if text:
                out.append(text)
        else:
            _collect_jats_text(child, out)


def _parse_jats_body(xml_text: str) -> str:
    """Extract readable body text from a PMC efetch JATS document.

    Returns "" when there is no ``<body>`` — i.e. the article is not in the PMC
    Open Access subset, so efetch returns metadata only.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    body = None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "body":
            body = el
            break
    if body is None:
        return ""
    parts: list[str] = []
    _collect_jats_text(body, parts)
    return "\n\n".join(parts).strip()


async def fetch_full_text(pmc_id: str) -> str:
    """Fetch the PMC Open Access full-text body for a numeric PMC id.

    Returns cleaned body prose, or "" if the article is not in the OA subset or
    has no retrievable body. Network/parse failures degrade to "" rather than
    raising — the caller falls back to the abstract.
    """
    if not pmc_id:
        return ""
    try:
        xml_text = await _rate_limited_get_text(
            f"{_EUTILS_BASE}/efetch.fcgi",
            {"db": "pmc", "id": pmc_id, "rettype": "xml", "retmode": "xml"},
        )
    except MCPToolError:
        return ""
    return _parse_jats_body(xml_text)


async def fetch_pmc_record(pmid: str, *, with_content: bool = False) -> PubMedFullText | None:
    """Return a PubMedFullText record if the article is in PubMed Central.

    With ``with_content=True``, also downloads the PMC Open Access body text into
    ``full_text`` when a PMC id resolves (empty if the article isn't OA).
    """
    data = await _rate_limited_get(
        f"{_EUTILS_BASE}/elink.fcgi",
        {"dbfrom": "pubmed", "db": "pmc", "id": pmid, "linkname": "pubmed_pmc"},
    )
    pmc_id = _extract_pmc_id(data)
    if not pmc_id:
        return PubMedFullText(pmid=pmid, available=False)
    content = await fetch_full_text(pmc_id) if with_content else ""
    return PubMedFullText(
        pmid=pmid,
        pmc_id=pmc_id,
        full_text_url=_PMC_ARTICLE_URL.format(pmc_id),
        available=True,
        full_text=content,
    )
