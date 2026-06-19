# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""SCImago Journal Rank (SJR) lookup — deterministic, bundled, offline.

scimagojr.com's own export endpoint sits behind a Cloudflare JS challenge
that blocks any non-browser client, so there is no live API to call here.
Instead we ship a static index built once by scripts/build_scimago_index.py
from the `sjrdata` mirror of SCImago's own "freely available" data (see that
script's docstring for provenance/licensing). No network access at runtime —
safe under the `all_local` routing policy.

Resolution is ISSN-first (print or electronic, exact match against SCImago's
records) and falls back to a normalized journal-title match. A miss returns
`matched=False` rather than guessing — callers should treat that as "no SJR
data available" (which keeps the report's quality column showing "no data"
rather than a fabricated score).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_INDEX_PATH = Path(__file__).resolve().parent / "data" / "scimago_2025.json.gz"
_ISSN_RE = re.compile(r"^\d{7}[\dX]$")


def _sjr_enabled() -> bool:
    """SJR data is licensed for non-commercial use only (see NOTICE.md).

    Disabled by default so commercial deployments stay clean; set
    SCIMAGO_SJR_ENABLED=true to opt in for non-commercial/academic use.
    """
    return os.getenv("SCIMAGO_SJR_ENABLED", "false").strip().lower() == "true"


# Quartile -> normalized 0-1 score, one tier per star bucket in
# agents/synthesis/report/citations.py:quality_rank (>=0.75 / >=0.5 / >=0.25 / else).
_QUARTILE_SCORE = {"Q1": 0.85, "Q2": 0.6, "Q3": 0.4, "Q4": 0.2}


class SjrRecord(BaseModel):
    matched: bool = False
    match_type: str | None = None  # "issn" | "title"
    matched_title: str | None = None
    sjr: float | None = None
    sjr_quartile: str | None = None
    sjr_score: float | None = None


_index: dict | None = None


def _load_index() -> dict:
    global _index
    if _index is None:
        with gzip.open(_INDEX_PATH, "rt", encoding="utf-8") as f:
            _index = json.load(f)
    return _index


def _normalize_issn(raw: str) -> str | None:
    code = raw.strip().replace("-", "").upper()
    return code if _ISSN_RE.match(code) else None


def _normalize_title(raw: str) -> str:
    title = raw.strip().lower()
    title = re.sub(r"^the\s+", "", title)
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def _to_record(row: dict, match_type: str) -> SjrRecord:
    quartile = row.get("sjr_best_quartile")
    return SjrRecord(
        matched=True,
        match_type=match_type,
        matched_title=row.get("title"),
        sjr=row.get("sjr"),
        sjr_quartile=quartile,
        sjr_score=_QUARTILE_SCORE.get(quartile),
    )


def resolve_sjr(issn: str = "", essn: str = "", journal_title: str = "") -> SjrRecord:
    """Resolve a journal's SJR score/quartile from ISSN, falling back to title.

    `issn`/`essn` are tried first (either may be print or electronic — both
    are indexed identically). Falls back to a normalized title match. Returns
    `matched=False` if neither resolves, or if the bundled SJR data is disabled
    (`SCIMAGO_SJR_ENABLED` unset/false) — its license is non-commercial only.
    """
    if not _sjr_enabled():
        return SjrRecord(matched=False)

    if not _INDEX_PATH.exists():
        # Enabled but the index was never built. The bundled data is gitignored
        # (non-commercial license); regenerate it with build_scimago_index.py.
        logger.warning(
            "SCIMAGO_SJR_ENABLED is true but %s is missing; SJR lookups will not "
            "resolve. Build it with `python scripts/build_scimago_index.py --year 2025`.",
            _INDEX_PATH,
        )
        return SjrRecord(matched=False)

    index = _load_index()

    for code in (issn, essn):
        if not code:
            continue
        normalized = _normalize_issn(code)
        if normalized is None:
            continue
        row = index["by_issn"].get(normalized)
        if row is not None:
            return _to_record(row, "issn")

    if journal_title:
        normalized_title = _normalize_title(journal_title)
        row = index["by_title"].get(normalized_title)
        if row is not None:
            return _to_record(row, "title")

    return SjrRecord(matched=False)
