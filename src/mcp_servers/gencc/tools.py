# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GenCC gene-disease validity tools.

GenCC (the Gene Curation Coalition) aggregates curated gene-disease validity
classifications submitted by independent curation bodies (ClinGen, Genomics
England PanelApp, Orphanet, Italian Telethon, ClinVar, etc.) and publishes a
single bulk CSV of every submission — no API key, refreshed periodically.
That per-submitter breakdown is what makes GenCC additive over having ClinGen
alone: the same gene-disease pair can carry several independent classifications,
and agreement/disagreement across curators is itself a signal.

We download the bulk export once, cache it locally, and index it by gene
symbol — mirroring the bulk-file pattern used for ClinGen
(see mcp_servers/clingen/tools.py module docstring).
"""

from __future__ import annotations

import csv
import io
import tempfile
import time
from pathlib import Path

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_BULK_URL = "https://search.thegencc.org/download/action/submissions-export-csv"

_CACHE_DIR = Path(tempfile.gettempdir()) / "gencc_cache"
_CACHE_PATH = _CACHE_DIR / "gencc-submissions-export.csv"
_CACHE_TTL = 24 * 3600

_CLASSIFICATION_RANK = {
    "Definitive": 6,
    "Strong": 5,
    "Moderate": 4,
    "Supportive": 3,
    "Limited": 2,
    "Disputed Evidence": 1,
    "Animal Model Only": 1,
    "No Known Disease Relationship": 0,
    "Refuted Evidence": -1,
}

# Several plausible header spellings exist across GenCC export revisions; resolve
# each logical field from the first header that's actually present rather than
# hard-coding one column layout.
_GENE_SYMBOL_FIELDS = ("gene_symbol", "submitted_as_hgnc_symbol")
_DISEASE_TITLE_FIELDS = ("disease_title", "submitted_as_disease_name")
_DISEASE_CURIE_FIELDS = ("disease_curie", "submitted_as_disease_id")
_CLASSIFICATION_FIELDS = ("classification_title", "submitted_as_classification_name")
_MOI_FIELDS = ("moi_title", "submitted_as_moi_name")
_SUBMITTER_FIELDS = ("submitter_title", "submitted_as_submitter")
_DATE_FIELDS = ("submitted_as_date", "date")


class GenCCAssociation(BaseModel):
    gene_symbol: str
    disease_title: str
    disease_curie: str | None = None
    classification: str
    mode_of_inheritance: str | None = None
    submitter: str | None = None
    submitted_date: str | None = None


class GenCCBundle(BaseModel):
    gene_symbol: str
    associations: list[GenCCAssociation] = []
    total: int = 0
    text: str = ""


_index: dict[str, list[dict[str, str]]] | None = None
_index_mtime: float | None = None


def _first_present(row: dict[str, str], candidates: tuple[str, ...]) -> str:
    for field in candidates:
        value = row.get(field)
        if value:
            return value
    return ""


async def _download_bulk_file() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = _CACHE_PATH.with_name(_CACHE_PATH.name + ".tmp")
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(_BULK_URL)
        if resp.status_code != 200:
            raise MCPToolError(f"GenCC bulk export download failed: HTTP {resp.status_code}")
        tmp_path.write_bytes(resp.content)
        tmp_path.replace(_CACHE_PATH)
    except httpx.HTTPError as exc:
        raise MCPToolError(f"GenCC bulk export download failed: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def _cache_valid() -> bool:
    return _CACHE_PATH.exists() and (time.time() - _CACHE_PATH.stat().st_mtime) < _CACHE_TTL


async def _ensure_cached() -> Path:
    if not _cache_valid():
        await _download_bulk_file()
    return _CACHE_PATH


def _build_index(csv_path: Path) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(io.StringIO(f.read()))
        for row in reader:
            symbol = _first_present(row, _GENE_SYMBOL_FIELDS)
            if not symbol:
                continue
            index.setdefault(symbol.upper(), []).append(row)
    return index


async def _get_index() -> dict[str, list[dict[str, str]]]:
    global _index, _index_mtime
    csv_path = await _ensure_cached()
    mtime = csv_path.stat().st_mtime
    if _index is None or _index_mtime != mtime:
        _index = _build_index(csv_path)
        _index_mtime = mtime
    return _index


async def get_gencc_validity(gene_symbol: str) -> GenCCBundle:
    """Fetch GenCC's per-submitter gene-disease validity classifications for a gene."""
    index = await _get_index()
    rows = index.get(gene_symbol.upper(), [])

    associations = [
        GenCCAssociation(
            gene_symbol=gene_symbol,
            disease_title=_first_present(row, _DISEASE_TITLE_FIELDS) or "Unknown disease",
            disease_curie=_first_present(row, _DISEASE_CURIE_FIELDS) or None,
            classification=_first_present(row, _CLASSIFICATION_FIELDS) or "Unknown",
            mode_of_inheritance=_first_present(row, _MOI_FIELDS) or None,
            submitter=_first_present(row, _SUBMITTER_FIELDS) or None,
            submitted_date=_first_present(row, _DATE_FIELDS) or None,
        )
        for row in rows
    ]
    associations.sort(
        key=lambda a: _CLASSIFICATION_RANK.get(a.classification, 0),
        reverse=True,
    )

    if not associations:
        text = f"No GenCC gene-disease validity submissions found for {gene_symbol}."
    else:
        lines = [
            f"{a.disease_title} ({a.classification}, {a.submitter or 'unknown submitter'})"
            for a in associations[:5]
        ]
        text = (
            f"GenCC gene-disease validity for {gene_symbol}: "
            + "; ".join(lines)
            + (f" [+{len(associations) - 5} more]" if len(associations) > 5 else "")
            + "."
        )

    return GenCCBundle(
        gene_symbol=gene_symbol,
        associations=associations,
        total=len(associations),
        text=text,
    )
