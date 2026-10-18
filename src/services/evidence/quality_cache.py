# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cache the source-quality map by source identity, not by per-run evidence UUIDs.

The quality map lenses read is keyed by ``Evidence.evidence_id``, a UUID minted per run.
A cached map keyed that way matched no document of a later run: every repeat run of a
gene–disease pair showed its lenses literature without any quality score, and the lens
confidence scorer weighed all of it at the unscored floor. The cache therefore stores
entries under ``Evidence.source`` (``PMID:…``, ``NCT…``, ``fda:label:…``) and re-keys them
onto the current run's UUIDs when read.

A cached entry also records which quality sources produced it, so scores computed with a
non-commercial source switched on (SCImago SJR, for local testing) are never reused by a
run that has it switched off.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

from schemas.evidence import Evidence

# 3: entries carry the paper's iCite ``article_metrics``.
QUALITY_CACHE_VERSION = 3


def quality_sources() -> str:
    """Which quality sources are switched on, as part of the cache identity."""

    def flag(name: str, default: str) -> str:
        return os.getenv(name, default).strip().lower()

    return (
        f"sjr={flag('SCIMAGO_SJR_ENABLED', 'false')}"
        f"|openalex={flag('OPENALEX_ENABLED', 'true')}"
        f"|icite={flag('ICITE_ENABLED', 'true')}"
    )


def to_cache(
    quality_map: Mapping[str, Mapping[str, Any]],
    evidences: Iterable[Evidence],
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The cache payload for ``quality_map`` (keyed by evidence UUID), stored by source.

    Entries of ``previous`` (an earlier payload written under the same quality sources)
    are kept, so scoring only a run's new documents never drops the older ones.
    """
    by_source: dict[str, dict[str, Any]] = {}
    if previous and _usable(previous):
        by_source.update({k: dict(v) for k, v in (previous.get("by_source") or {}).items()})
    for ev in evidences:
        entry = quality_map.get(str(ev.evidence_id))
        if entry is None or not ev.source:
            continue
        by_source[ev.source] = {k: v for k, v in entry.items() if k != "evidence_id"}
    return {
        "version": QUALITY_CACHE_VERSION,
        "quality_sources": quality_sources(),
        "by_source": by_source,
    }


def from_cache(
    cached: Mapping[str, Any] | None, evidences: Iterable[Evidence]
) -> tuple[dict[str, dict[str, Any]], list[Evidence]]:
    """The current run's quality map from a cache payload, and the documents it misses.

    A payload from an older format (keyed by another run's UUIDs) or written under other
    quality sources covers nothing.
    """
    docs = list(evidences)
    if not cached or not _usable(cached):
        return {}, docs
    by_source: Mapping[str, Mapping[str, Any]] = cached.get("by_source") or {}
    quality_map: dict[str, dict[str, Any]] = {}
    missing: list[Evidence] = []
    for ev in docs:
        entry = by_source.get(ev.source) if ev.source else None
        if entry is None:
            missing.append(ev)
        else:
            quality_map[str(ev.evidence_id)] = dict(entry)
    return quality_map, missing


def _usable(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("version") == QUALITY_CACHE_VERSION
        and payload.get("quality_sources") == quality_sources()
    )
