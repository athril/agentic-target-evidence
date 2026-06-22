# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""PubMed tools using NCBI E-utilities API.

Rate limit: 3 requests/second (NCBI free-tier without API key).
With NCBI_API_KEY set the limit rises to 10 req/s — the semaphore is
adjusted accordingly.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

_RATE = 3  # req/s without API key; 10 with
_rate_lock = asyncio.Semaphore(1)
_request_times: list[float] = []


async def _throttle() -> None:
    """Enforce NCBI sliding-window rate limit."""
    async with _rate_lock:
        now = time.monotonic()
        _request_times[:] = [t for t in _request_times if now - t < 1.0]
        if len(_request_times) >= _RATE:
            wait = 1.0 - (now - _request_times[0])
            if wait > 0:
                await asyncio.sleep(wait)
        _request_times.append(time.monotonic())


_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 2.0  # seconds; doubles on each 429


async def _get_with_retry(url: str, params: dict[str, Any], *, json: bool) -> httpx.Response:
    """Throttle, GET, and retry on 429 with exponential backoff."""
    delay = _RETRY_BASE_DELAY
    for _attempt in range(_MAX_RETRIES):
        await _throttle()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", delay))
            await asyncio.sleep(retry_after)
            delay *= 2
            continue
        return response
    raise MCPToolError(f"NCBI E-utilities returned HTTP 429 after {_MAX_RETRIES} retries")


async def _rate_limited_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET with NCBI rate limiting; returns parsed JSON."""
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key
    params["retmode"] = "json"

    response = await _get_with_retry(url, params, json=True)
    if response.status_code != 200:
        raise MCPToolError(f"NCBI E-utilities returned HTTP {response.status_code}")
    return response.json()


async def _rate_limited_get_text(url: str, params: dict[str, Any]) -> str:
    """GET with NCBI rate limiting; returns raw text (for XML responses)."""
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key

    response = await _get_with_retry(url, params, json=False)
    if response.status_code != 200:
        raise MCPToolError(f"NCBI E-utilities returned HTTP {response.status_code}")
    return response.text


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
            text = (el.text or "").strip()
            if not text:
                continue
            label = el.get("Label")
            parts.append(f"{label}: {text}" if label else text)

        abstracts[pmid] = " ".join(parts)
    return abstracts


async def _fetch_abstracts_batch(pmids: list[str]) -> dict[str, str]:
    """Fetch abstracts for a batch of PMIDs via efetch XML; returns {pmid: abstract}."""
    xml_text = await _rate_limited_get_text(
        f"{_EUTILS_BASE}/efetch.fcgi",
        {"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"},
    )
    return _parse_abstracts_from_xml(xml_text)


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


async def search_pubmed(query: str, max_results: int = 500) -> list[PubMedRecord]:
    """Search PubMed and return structured records including abstracts."""
    search_data = await _rate_limited_get(
        f"{_EUTILS_BASE}/esearch.fcgi",
        {"db": "pubmed", "term": query, "retmax": max_results, "usehistory": "y"},
    )
    esearch = search_data.get("esearchresult", {})
    ids: list[str] = esearch.get("idlist", [])
    if not ids:
        return []

    # Fetch summaries in batches of 200 (URL length limit)
    _BATCH = 200
    result_map: dict = {}
    for i in range(0, len(ids), _BATCH):
        batch = ids[i : i + _BATCH]
        summary_data = await _rate_limited_get(
            f"{_EUTILS_BASE}/esummary.fcgi",
            {"db": "pubmed", "id": ",".join(batch)},
        )
        result_map.update(summary_data.get("result", {}))

    # Batch-fetch abstracts via efetch XML (same batch size)
    abstract_map: dict[str, str] = {}
    for i in range(0, len(ids), _BATCH):
        batch = ids[i : i + _BATCH]
        abstract_map.update(await _fetch_abstracts_batch(batch))

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


def _extract_pmc_id(linkset: dict[str, Any]) -> str | None:
    """Pull the PMC id from an elink linkset, or None if it isn't present.

    NCBI returns ``ids`` in more than one shape across responses: a wrapped
    dict (``{"id": ["123"]}``), a flat list (``["123"]``), or sometimes an
    empty/absent value. Parse all of these; when the id list is present but
    empty, return None rather than indexing into it (the original cause of the
    IndexError) — and never substitute the PMID, which is a different
    identifier and would be fabricated attribution.
    """
    ids = linkset.get("ids")
    if isinstance(ids, dict):
        ids = ids.get("id")
    if isinstance(ids, list):
        return str(ids[0]) if ids else None
    if isinstance(ids, (str, int)):
        return str(ids)
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
        {"dbfrom": "pubmed", "db": "pmc", "id": pmid, "cmd": "prlinks"},
    )
    linksets = data.get("linksets", [])
    for ls in linksets:
        for link in ls.get("idurllist", []):
            url = (link.get("objurls") or [{}])[0].get("url", {}).get("$", "")
            if url:
                pmc_id = _extract_pmc_id(ls)
                content = ""
                if with_content and pmc_id:
                    content = await fetch_full_text(pmc_id)
                return PubMedFullText(
                    pmid=pmid,
                    pmc_id=pmc_id,
                    full_text_url=url,
                    available=True,
                    full_text=content,
                )
    return PubMedFullText(pmid=pmid, available=False)
