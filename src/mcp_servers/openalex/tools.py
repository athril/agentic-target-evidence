# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OpenAlex journal-quality lookup — a commercial-safe (CC0) SJR fallback.

OpenAlex (https://openalex.org) publishes its entire catalogue under CC0 (public
domain) — unlike SCImago's SJR data, which is non-commercial-only (see NOTICE.md
and `mcp_servers.scimago`). This resolver gives the source-quality agent a
journal-prestige signal that is safe to use under any license posture, including
commercial deployments where `SCIMAGO_SJR_ENABLED` is off.

OpenAlex does *not* publish SJR-style quartiles. It exposes, per journal source:
- `summary_stats.2yr_mean_citedness` — citations/paper over 2 years, the open
  analogue of an impact factor;
- `summary_stats.h_index`, `works_count` — size/establishment signals;
- `is_in_doaj` — listed in the Directory of Open Access Journals (a legitimacy,
  i.e. anti-predatory, signal).

We map `2yr_mean_citedness` onto the same 0-1 `quality_score` tiers the SJR path
uses (so `report/citations.py:quality_rank` renders comparable stars), but this
is an open *approximation*, not a quartile — callers should surface it as
"OpenAlex" provenance, never as an SJR figure.

Network access is required (CC0 data, but served live). Any transport error or a
miss returns `matched=False` so the agent degrades gracefully (it then falls to
its LLM predatory-journal judgment) rather than raising. Disable entirely with
`OPENALEX_ENABLED=false` for fully offline runs.

Requests without an API key draw on a small free daily budget shared by everyone on
the caller's IP (a title search costs ~1/100 of it). Set `OPENALEX_API_KEY` (free) for
a dedicated budget. Lookups are de-duplicated per process, and once the budget is
spent (HTTP 429 with a Retry-After of hours) further lookups are skipped until it
resets instead of each failing in turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import weakref
from typing import Any

import httpx
from pydantic import BaseModel

from core.http import get_with_retry

logger = logging.getLogger(__name__)

_BASE = "https://api.openalex.org"
_ISSN_RE = re.compile(r"^\d{7}[\dX]$")
_TIMEOUT = 15.0

# 2yr mean citedness (cites/paper) -> normalized 0-1 score, aligned with the SJR
# path's quartile tiers in mcp_servers/scimago/tools.py (_QUARTILE_SCORE:
# 0.85 / 0.6 / 0.4 / 0.2) so downstream star ranking stays consistent. These are
# an open approximation of journal prestige, NOT SCImago quartiles.
_CITEDNESS_TIERS = ((8.0, 0.85), (4.0, 0.6), (2.0, 0.4), (0.0, 0.2))

# Establishment signal: a journal with this h-index (or DOAJ listing) is treated
# as non-predatory without consulting the LLM. Below it, predatory judgment is
# left to the agent's LLM pass (predatory_flag stays None).
_ESTABLISHED_H_INDEX = 10

# OpenAlex answers ~10 concurrent requests and rate-limits the rest (HTTP 429): a
# source-quality pass resolving one journal per paper concurrently lost 91 lookups in
# one second. Requests are capped per event loop; 429s are retried by get_with_retry.
_MAX_CONCURRENCY = int(os.getenv("OPENALEX_MAX_CONCURRENCY", "8"))
_LIMITERS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _limiter() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _LIMITERS.get(loop)
    if sem is None:
        sem = _LIMITERS[loop] = asyncio.Semaphore(max(1, _MAX_CONCURRENCY))
    return sem


class OpenAlexJournal(BaseModel):
    matched: bool = False
    match_type: str | None = None  # "issn" | "title"
    source_id: str | None = None
    display_name: str | None = None
    issn_l: str | None = None
    works_count: int | None = None
    h_index: int | None = None
    two_yr_mean_citedness: float | None = None
    is_in_doaj: bool | None = None
    quality_score: float | None = None  # 0-1 open approximation of prestige
    established: bool | None = None  # legitimacy signal (DOAJ or h-index)


def _enabled() -> bool:
    return os.getenv("OPENALEX_ENABLED", "true").strip().lower() == "true"


def _mailto() -> str:
    """OpenAlex "polite pool" contact — recommended, not required."""
    return os.getenv("OPENALEX_MAILTO", "").strip()


def _normalize_issn(raw: str) -> str | None:
    code = raw.strip().replace("-", "").upper()
    return code if _ISSN_RE.match(code) else None


def _format_issn(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def _score_from_citedness(citedness: float | None) -> float | None:
    if citedness is None:
        return None
    for threshold, score in _CITEDNESS_TIERS:
        if citedness >= threshold:
            return score
    return _CITEDNESS_TIERS[-1][1]


def _api_key() -> str:
    """OpenAlex API key — a dedicated daily budget instead of the shared per-IP one."""
    return os.getenv("OPENALEX_API_KEY", "").strip()


def _params() -> dict[str, str]:
    params: dict[str, str] = {}
    if mailto := _mailto():
        params["mailto"] = mailto
    if key := _api_key():
        params["api_key"] = key
    return params


# Definitive answers (a match, or a clean miss) per journal, so a run resolving one
# journal per paper pays once per journal. Errors are never cached.
_LOOKUP_CACHE: dict[tuple[str, str, str], OpenAlexJournal] = {}
_LOOKUP_CACHE_MAX = 4096
# Retry-After beyond this means a spent budget, not a burst: stop calling until reset.
_BUDGET_BLOCK_MIN_S = 60.0
_blocked_until = 0.0


def _cache_key(issn: str, essn: str, title: str) -> tuple[str, str, str]:
    return (
        _normalize_issn(issn) or "",
        _normalize_issn(essn) or "",
        " ".join(title.lower().split()),
    )


def _remember(key: tuple[str, str, str], record: OpenAlexJournal) -> OpenAlexJournal:
    if len(_LOOKUP_CACHE) >= _LOOKUP_CACHE_MAX:
        _LOOKUP_CACHE.clear()
    _LOOKUP_CACHE[key] = record
    return record


def _note_budget_block(exc: httpx.HTTPStatusError) -> bool:
    """Open the circuit when a 429 says the budget is spent; True if it did."""
    global _blocked_until
    if exc.response.status_code != 429:
        return False
    try:
        wait = float(exc.response.headers.get("Retry-After") or 0)
    except ValueError:
        return False
    if wait < _BUDGET_BLOCK_MIN_S:
        return False
    _blocked_until = time.monotonic() + wait
    logger.warning(
        "OpenAlex budget exhausted (resets in %.1f h); skipping journal-quality lookups "
        "until then — set OPENALEX_API_KEY for a dedicated budget",
        wait / 3600,
    )
    return True


def _to_record(source: dict[str, Any], match_type: str) -> OpenAlexJournal:
    stats = source.get("summary_stats") or {}
    citedness = stats.get("2yr_mean_citedness")
    h_index = stats.get("h_index")
    is_in_doaj = source.get("is_in_doaj")
    established = bool(is_in_doaj) or (h_index is not None and h_index >= _ESTABLISHED_H_INDEX)
    return OpenAlexJournal(
        matched=True,
        match_type=match_type,
        source_id=source.get("id"),
        display_name=source.get("display_name"),
        issn_l=source.get("issn_l"),
        works_count=source.get("works_count"),
        h_index=h_index,
        two_yr_mean_citedness=citedness,
        is_in_doaj=is_in_doaj,
        quality_score=_score_from_citedness(citedness),
        established=established,
    )


async def _fetch_by_issn(client: httpx.AsyncClient, issn: str) -> dict[str, Any] | None:
    """OpenAlex resolves a namespaced ISSN to a single source object."""
    async with _limiter():
        resp = await get_with_retry(
            client, f"{_BASE}/sources/issn:{_format_issn(issn)}", params=_params()
        )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return dict(resp.json())


async def _fetch_by_title(client: httpx.AsyncClient, title: str) -> dict[str, Any] | None:
    async with _limiter():
        resp = await get_with_retry(
            client,
            f"{_BASE}/sources",
            params={**_params(), "search": title, "per_page": 1},
        )
    resp.raise_for_status()
    results = (resp.json() or {}).get("results") or []
    return results[0] if results else None


async def resolve_journal(
    issn: str = "",
    essn: str = "",
    journal_title: str = "",
    client: httpx.AsyncClient | None = None,
) -> OpenAlexJournal:
    """Resolve a journal's OpenAlex quality signal from ISSN, falling back to title.

    `issn`/`essn` are tried first (either may be print or electronic). Falls back
    to a title search. Returns `matched=False` on a miss, a transport error, or
    when `OPENALEX_ENABLED` is false (fully offline) — never raises for the
    caller, so the source-quality agent can degrade to its LLM judgment.
    """
    if not _enabled():
        return OpenAlexJournal(matched=False)
    key = _cache_key(issn, essn, journal_title)
    if key in _LOOKUP_CACHE:
        return _LOOKUP_CACHE[key]
    if time.monotonic() < _blocked_until:
        return OpenAlexJournal(matched=False)

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    try:
        for code in (issn, essn):
            normalized = _normalize_issn(code) if code else None
            if normalized is None:
                continue
            source = await _fetch_by_issn(client, normalized)
            if source is not None:
                return _remember(key, _to_record(source, "issn"))

        if journal_title:
            source = await _fetch_by_title(client, journal_title)
            if source is not None:
                return _remember(key, _to_record(source, "title"))
    except (httpx.HTTPError, ValueError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        if isinstance(exc, httpx.HTTPStatusError) and _note_budget_block(exc):
            return OpenAlexJournal(matched=False)
        logger.warning(
            "OpenAlex lookup failed (%s%s); treating as unmatched",
            type(exc).__name__,
            f" HTTP {status}" if status else "",
        )
        return OpenAlexJournal(matched=False)
    finally:
        if owns_client:
            await client.aclose()

    return _remember(key, OpenAlexJournal(matched=False))
