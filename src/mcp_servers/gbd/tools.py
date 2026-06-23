# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GBD (Global Burden of Disease, IHME) disease-keyed prevalence/incidence.

Orphanet's prevalence data (mcp_servers/orphanet) is rare-disease-only and
reached gene-first (gene → orphacode → prevalence), so a common indication
(T2D, NASH, MDD, …) that isn't a Mendelian disorder gets no addressable-
population signal at all. GBD fills that hole with a whole-population,
disease-keyed epidemiology source — the commercial lens's market-size axis.

There is no clean public REST API for GBD results (the GBD Results Tool's
query endpoint requires registration/permalinks and isn't ToS-friendly to
scrape), so this mirrors the SCImago/SJR model instead of the
Orphanet/OMIM/TTD bulk-download model: the operator downloads a GBD Results
CSV extract from GHDx (https://ghdx.healthdata.org) themselves and points
``GBD_DATA_PATH`` at it. We ship no GBD data.

GBD is distributed under the IHME Free-of-Charge Non-commercial User
Agreement (see NOTICE.md), so it is gated behind ``GBD_ENABLED`` (off by
default) like OMIM/SCImago — commercial deployments stay clean unless they
explicitly opt in.

Expected CSV columns: ``cause_id, cause_name, measure_name, metric_name,
location_name, year, val, upper, lower`` (a GBD Results Tool export filtered
to ``measure ∈ {Prevalence, Incidence}``, ``metric ∈ {Number, Rate}``,
``age = All ages``, ``sex = Both``).

The crux of this source is disease → GBD cause mapping: GBD's own cause
hierarchy (``cause_id``/``cause_name``) doesn't line up with MONDO/EFO/
OrphaCode. Resolution is precision-first and graceful on a miss:

  1. Normalized exact name match against ``cause_name``.
  2. A curated override crosswalk (config/gbd_cause_crosswalk.yaml) mapping a
     MONDO id or disease string to a ``cause_id``, for the cases where naming
     diverges.
  3. No confident match → an empty bundle (``mapping="none"``). We never
     fabricate a "prevalence unknown" — see commercial_interpret.py guard C.
"""

from __future__ import annotations

import csv
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

_CROSSWALK_PATH = Path("config/gbd_cause_crosswalk.yaml")

_PREVALENCE_MEASURE = "Prevalence"
_INCIDENCE_MEASURE = "Incidence"
_PREFERRED_LOCATION = "Global"


def _enabled() -> bool:
    """GBD is licensed for non-commercial use only (IHME Free-of-Charge
    Non-commercial User Agreement; see NOTICE.md).

    Off by default so commercial deployments stay clean; set GBD_ENABLED=true
    (and GBD_DATA_PATH to a local extract) to opt in for non-commercial/
    academic use.
    """
    return os.getenv("GBD_ENABLED", "false").strip().lower() == "true"


def _data_path() -> Path | None:
    raw = os.environ.get("GBD_DATA_PATH", "").strip()
    return Path(raw) if raw else None


class GBDPrevalenceRecord(BaseModel):
    cause_id: str
    cause_name: str
    measure: str
    metric: str
    location: str
    year: int
    value: float
    lower: float | None = None
    upper: float | None = None


class GBDBundle(BaseModel):
    disease: str
    cause_name: str = ""
    records: list[GBDPrevalenceRecord] = []
    total: int = 0
    text: str = ""
    mapping: str = "none"  # exact | crosswalk | none


_index: dict[str, list[dict[str, str]]] | None = None
_index_by_id: dict[str, list[dict[str, str]]] | None = None
_index_mtime: float | None = None
_index_path: Path | None = None


def _normalize_name(raw: str) -> str:
    name = raw.strip().lower()
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _parse_float(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _build_indexes(
    csv_path: Path,
) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
    by_name: dict[str, list[dict[str, str]]] = {}
    by_id: dict[str, list[dict[str, str]]] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cause_id = (row.get("cause_id") or "").strip()
            cause_name = (row.get("cause_name") or "").strip()
            if not cause_id or not cause_name:
                continue
            by_name.setdefault(_normalize_name(cause_name), []).append(row)
            by_id.setdefault(cause_id, []).append(row)
    return by_name, by_id


def _get_indexes() -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
    global _index, _index_by_id, _index_mtime, _index_path
    path = _data_path()
    if path is None or not path.exists():
        return {}, {}
    mtime = path.stat().st_mtime
    if _index is None or _index_mtime != mtime or _index_path != path:
        _index, _index_by_id = _build_indexes(path)
        _index_mtime = mtime
        _index_path = path
    return _index or {}, _index_by_id or {}


@lru_cache(maxsize=1)
def _load_crosswalk(path_str: str) -> dict[str, str]:
    """disease-string / MONDO-id (normalized) -> GBD cause_id."""
    path = Path(path_str)
    if not path.exists():
        return {}
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    crosswalk: dict[str, str] = {}
    for key, cause_id in (data.get("crosswalk") or {}).items():
        crosswalk[_normalize_name(str(key))] = str(cause_id)
    return crosswalk


def reload_gbd_crosswalk() -> None:
    """Drop the cached crosswalk so the next get_disease_burden() re-reads disk."""
    _load_crosswalk.cache_clear()


def _resolve_cause(
    disease: str,
    disease_id: str,
    by_name: dict[str, list[dict[str, str]]],
    by_id: dict[str, list[dict[str, str]]],
) -> tuple[list[dict[str, str]], str]:
    """Return (matched rows, mapping confidence): 'exact' | 'crosswalk' | 'none'."""
    rows = by_name.get(_normalize_name(disease), [])
    if rows:
        return rows, "exact"

    crosswalk = _load_crosswalk(str(_CROSSWALK_PATH))
    cause_id = crosswalk.get(_normalize_name(disease_id)) or crosswalk.get(_normalize_name(disease))
    if cause_id:
        rows = by_id.get(cause_id, [])
        if rows:
            return rows, "crosswalk"

    return [], "none"


def _select_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep Prevalence/Incidence rows, preferring the Global location and latest year."""
    filtered = [r for r in rows if (r.get("measure_name") or "") in (_PREVALENCE_MEASURE, _INCIDENCE_MEASURE)]
    if not filtered:
        return []
    global_rows = [r for r in filtered if (r.get("location_name") or "") == _PREFERRED_LOCATION]
    candidates = global_rows or filtered
    latest_year = max((int(r["year"]) for r in candidates if (r.get("year") or "").isdigit()), default=None)
    if latest_year is not None:
        candidates = [r for r in candidates if (r.get("year") or "") == str(latest_year)]
    return candidates


def _format_text(disease: str, records: list[GBDPrevalenceRecord]) -> str:
    if not records:
        return ""
    cause_name = records[0].cause_name
    lines = []
    for r in records:
        if r.metric == "Rate":
            lines.append(f"{r.measure.lower()} rate {r.value:,.0f} per 100k ({r.location}, {r.year})")
        else:
            lines.append(f"{r.measure.lower()} {r.value:,.0f} cases ({r.location}, {r.year})")
    return f"{cause_name} (GBD): " + "; ".join(lines) + "."


async def get_disease_burden(disease: str, *, disease_id: str = "") -> GBDBundle:
    """Fetch GBD prevalence/incidence burden for a disease (whole-population, disease-keyed).

    Returns an empty bundle (``mapping="none"``) when GBD is disabled
    (``GBD_ENABLED`` off), no extract is configured (``GBD_DATA_PATH``), or no
    confident cause mapping is found — never a fabricated "unknown" prevalence.
    """
    if not _enabled():
        return GBDBundle(disease=disease, mapping="none")

    by_name, by_id = _get_indexes()
    if not by_name:
        return GBDBundle(disease=disease, mapping="none")

    rows, mapping = _resolve_cause(disease, disease_id, by_name, by_id)
    if not rows:
        return GBDBundle(disease=disease, mapping="none")

    selected = _select_rows(rows)
    records = [
        GBDPrevalenceRecord(
            cause_id=(r.get("cause_id") or ""),
            cause_name=(r.get("cause_name") or ""),
            measure=(r.get("measure_name") or ""),
            metric=(r.get("metric_name") or ""),
            location=(r.get("location_name") or ""),
            year=int(r["year"]) if (r.get("year") or "").isdigit() else 0,
            value=_parse_float(r.get("val") or "") or 0.0,
            lower=_parse_float(r.get("lower") or ""),
            upper=_parse_float(r.get("upper") or ""),
        )
        for r in selected
    ]

    if not records:
        return GBDBundle(disease=disease, mapping="none")

    return GBDBundle(
        disease=disease,
        cause_name=records[0].cause_name,
        records=records,
        total=len(records),
        text=_format_text(disease, records),
        mapping=mapping,
    )
