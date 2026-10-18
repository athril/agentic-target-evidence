# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Article-level citation metrics from NIH iCite (https://icite.od.nih.gov).

iCite publishes, per PMID, the Relative Citation Ratio (citations normalised for the
article's field and age; 1.0 is the median NIH-funded paper), the citation count, the
article's share of human, animal and molecular/cellular research (from its MeSH terms),
and whether it is a primary research article or a clinical one. Its citations come from
the NIH Open Citation Collection, which is in the public domain, so the data are safe for
any licence posture; journal-level SJR is not.

The ratio is null for papers too recent to have one (about two years). Up to 1,000 PMIDs
go in one request, with no key. Lookups are cached per process. Any transport error
returns what was fetched so far, so callers degrade to "no metric" rather than fail.
Disable with ``ICITE_ENABLED=false`` for offline runs.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from pydantic import BaseModel

from core.http import get_with_retry

logger = logging.getLogger(__name__)

_BASE = "https://icite.od.nih.gov/api/pubs"
_BATCH = 1000  # iCite's per-request limit
_TIMEOUT = 30.0
_FIELDS = (
    "pmid,year,relative_citation_ratio,citation_count,is_research_article,is_clinical,"
    "human,animal,molecular_cellular,provisional"
)

_cache: dict[str, ICiteRecord] = {}


class ICiteRecord(BaseModel):
    """One article's iCite metrics; ``matched`` is False when iCite has no record."""

    pmid: str
    matched: bool = False
    year: int | None = None
    relative_citation_ratio: float | None = None
    citation_count: int | None = None
    is_research_article: bool | None = None
    is_clinical: bool | None = None
    human: float | None = None
    animal: float | None = None
    molecular_cellular: float | None = None
    provisional: bool | None = None


def _enabled() -> bool:
    return os.getenv("ICITE_ENABLED", "true").strip().lower() == "true"


def _record(raw: dict[str, Any]) -> ICiteRecord | None:
    pmid = str(raw.get("pmid") or "").strip()
    if not pmid:
        return None

    def num(key: str) -> float | None:
        value = raw.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    def flag(key: str) -> bool | None:
        value = raw.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("yes", "no", "true", "false"):
            return value.lower() in ("yes", "true")
        return None

    year = raw.get("year")
    count = raw.get("citation_count")
    return ICiteRecord(
        pmid=pmid,
        matched=True,
        year=int(year) if isinstance(year, int) else None,
        relative_citation_ratio=num("relative_citation_ratio"),
        citation_count=int(count) if isinstance(count, int) else None,
        is_research_article=flag("is_research_article"),
        is_clinical=flag("is_clinical"),
        human=num("human"),
        animal=num("animal"),
        molecular_cellular=num("molecular_cellular"),
        provisional=flag("provisional"),
    )


async def article_metrics(
    pmids: list[str], *, client: httpx.AsyncClient | None = None
) -> dict[str, ICiteRecord]:
    """iCite metrics for ``pmids``: {pmid: record}; a PMID iCite lacks gets ``matched=False``.

    Returns {} when disabled. A failed request leaves its PMIDs out of the result (not
    cached), so a later call can retry them.
    """
    if not _enabled():
        return {}
    wanted = [p for p in dict.fromkeys(str(p).strip() for p in pmids) if p.isdigit()]
    todo = [p for p in wanted if p not in _cache]
    if todo:
        own_client = client is None
        http = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
        try:
            for i in range(0, len(todo), _BATCH):
                batch = todo[i : i + _BATCH]
                try:
                    resp = await get_with_retry(
                        http, _BASE, params={"pmids": ",".join(batch), "fl": _FIELDS}
                    )
                except httpx.HTTPError as exc:
                    logger.warning("iCite request failed (%d PMIDs): %s", len(batch), exc)
                    continue
                if resp.status_code != 200:
                    logger.warning(
                        "iCite returned HTTP %d for %d PMIDs", resp.status_code, len(batch)
                    )
                    continue
                try:
                    rows = resp.json().get("data") or []
                except ValueError:
                    logger.warning("iCite returned a non-JSON body for %d PMIDs", len(batch))
                    continue
                found = {r.pmid: r for r in (_record(x) for x in rows if isinstance(x, dict)) if r}
                for pmid in batch:
                    _cache[pmid] = found.get(pmid) or ICiteRecord(pmid=pmid)
        finally:
            if own_client:
                await http.aclose()
    return {p: _cache[p] for p in wanted if p in _cache}


def reset_cache() -> None:
    """Forget cached lookups (tests)."""
    _cache.clear()
