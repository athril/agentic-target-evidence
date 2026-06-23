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
"""

from __future__ import annotations

import logging
import os
import re
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


def _params() -> dict[str, str]:
    mailto = _mailto()
    return {"mailto": mailto} if mailto else {}


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
    resp = await get_with_retry(
        client, f"{_BASE}/sources/issn:{_format_issn(issn)}", params=_params()
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return dict(resp.json())


async def _fetch_by_title(client: httpx.AsyncClient, title: str) -> dict[str, Any] | None:
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

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    try:
        for code in (issn, essn):
            normalized = _normalize_issn(code) if code else None
            if normalized is None:
                continue
            source = await _fetch_by_issn(client, normalized)
            if source is not None:
                return _to_record(source, "issn")

        if journal_title:
            source = await _fetch_by_title(client, journal_title)
            if source is not None:
                return _to_record(source, "title")
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OpenAlex lookup failed (%s); treating as unmatched", type(exc).__name__)
        return OpenAlexJournal(matched=False)
    finally:
        if owns_client:
            await client.aclose()

    return OpenAlexJournal(matched=False)
