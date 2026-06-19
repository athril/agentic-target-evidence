# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""OMIM Mendelian disease association tools.

OMIM publishes a bulk gene-phenotype map (``genemap2.txt``) for exactly this
kind of lookup, refreshed regularly, behind a free academic/research API key
(register at https://www.omim.org/api). We download it once, cache it
locally, and index it by gene symbol — mirroring the bulk-file pattern used
for ClinGen (see mcp_servers/clingen/tools.py module docstring).

Unlike sources that require a key to function at all, OMIM is treated as
*optional*: a missing ``OMIM_API_KEY`` returns an empty bundle instead of
raising, so a run without a registered key simply proceeds without OMIM
evidence rather than failing.

OMIM also restricts use to educational/internal-research/non-commercial purposes
(see NOTICE.md), so it is additionally gated behind ``OMIM_ENABLED`` (off by
default) — commercial deployments stay clean unless they explicitly opt in.
Callers that want to skip the call (and its trace span) entirely should use
``omim_configured()`` rather than re-checking the env themselves.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_DOWNLOAD_URL = "https://data.omim.org/downloads/{api_key}/genemap2.txt"

_CACHE_DIR = Path(tempfile.gettempdir()) / "omim_cache"
_CACHE_PATH = _CACHE_DIR / "genemap2.txt"
_CACHE_TTL = 24 * 3600

# "<name>, <mim> (<mapping key>), <inheritance>" — mapping key 3 = molecularly
# confirmed, 4 = chromosome deletion/duplication syndrome, 2 = linkage only,
# 1 = disputed/disorder with unknown molecular basis. A leading '?' marks a
# provisional (unconfirmed) phenotype-gene relationship.
_PHENOTYPE_RE = re.compile(
    r"^(?P<provisional>\?)?(?P<name>.+?),\s*(?P<mim>\d{6})\s*\((?P<key>[1-4])\)"
    r"(?:,\s*(?P<inheritance>.+))?$"
)

_MAPPING_LABELS = {
    "1": "disputed mapping",
    "2": "mapped by linkage",
    "3": "molecularly confirmed",
    "4": "chromosomal deletion/duplication syndrome",
}


class OmimAssociation(BaseModel):
    gene_symbol: str
    phenotype_label: str
    mim_number: str | None = None
    mapping_key: str | None = None
    mapping_confidence: str | None = None
    inheritance: str | None = None
    provisional: bool = False


class OmimBundle(BaseModel):
    gene_symbol: str
    associations: list[OmimAssociation] = []
    total: int = 0
    text: str = ""


_index: dict[str, list[str]] | None = None
_index_mtime: float | None = None


def _api_key() -> str | None:
    return os.environ.get("OMIM_API_KEY") or None


def _enabled() -> bool:
    """OMIM restricts use to non-commercial purposes (see NOTICE.md).

    Off by default so commercial deployments stay clean; set OMIM_ENABLED=true
    (and configure OMIM_API_KEY) to opt in for non-commercial/academic use.
    """
    return os.getenv("OMIM_ENABLED", "false").strip().lower() == "true"


def omim_configured() -> bool:
    """True when OMIM is both opted in (``OMIM_ENABLED``) and has an API key."""
    return _enabled() and _api_key() is not None


async def _download_bulk_file(api_key: str) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = _CACHE_PATH.with_name(_CACHE_PATH.name + ".tmp")
    url = _DOWNLOAD_URL.format(api_key=api_key)
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            raise MCPToolError(f"OMIM bulk dataset download failed: HTTP {resp.status_code}")
        tmp_path.write_bytes(resp.content)
        tmp_path.replace(_CACHE_PATH)
    except httpx.HTTPError as exc:
        raise MCPToolError(f"OMIM bulk dataset download failed: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def _cache_valid() -> bool:
    return _CACHE_PATH.exists() and (time.time() - _CACHE_PATH.stat().st_mtime) < _CACHE_TTL


async def _ensure_cached(api_key: str) -> Path:
    if not _cache_valid():
        await _download_bulk_file(api_key)
    return _CACHE_PATH


def _build_index(path: Path) -> dict[str, list[str]]:
    """Group raw Phenotypes-column strings by uppercased approved gene symbol."""
    index: dict[str, list[str]] = {}
    header: list[str] | None = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#"):
                # The last comment line before the data rows is the header.
                candidate = line.lstrip("#").strip()
                if "\t" in candidate:
                    header = candidate.split("\t")
                continue
            if not line.strip() or header is None:
                continue
            cols = line.split("\t")
            row = dict(zip(header, cols, strict=False))
            symbol = row.get("Approved Gene Symbol") or row.get("Approved Symbol") or ""
            phenotypes = row.get("Phenotypes") or ""
            if not symbol or not phenotypes:
                continue
            index.setdefault(symbol.upper(), []).append(phenotypes)
    return index


async def _get_index(api_key: str) -> dict[str, list[str]]:
    global _index, _index_mtime
    path = await _ensure_cached(api_key)
    mtime = path.stat().st_mtime
    if _index is None or _index_mtime != mtime:
        _index = _build_index(path)
        _index_mtime = mtime
    return _index


def _parse_phenotypes(gene_symbol: str, raw: str) -> list[OmimAssociation]:
    associations: list[OmimAssociation] = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        m = _PHENOTYPE_RE.match(entry)
        if not m:
            # No MIM number / mapping key present (e.g. a bare gene-name placeholder) —
            # not a usable phenotype association.
            continue
        key = m.group("key")
        associations.append(
            OmimAssociation(
                gene_symbol=gene_symbol,
                phenotype_label=m.group("name").strip(),
                mim_number=m.group("mim"),
                mapping_key=key,
                mapping_confidence=_MAPPING_LABELS.get(key),
                inheritance=(m.group("inheritance") or "").strip() or None,
                provisional=bool(m.group("provisional")),
            )
        )
    return associations


async def get_omim_validity(gene_symbol: str) -> OmimBundle:
    """Fetch OMIM Mendelian phenotype-gene associations for a gene.

    Returns an empty bundle (no error) if OMIM is disabled (``OMIM_ENABLED``
    off — its license is non-commercial only) or ``OMIM_API_KEY`` is not
    configured. OMIM is an optional source; see module docstring.
    """
    if not _enabled():
        return OmimBundle(
            gene_symbol=gene_symbol,
            text="OMIM disabled (non-commercial license) — set OMIM_ENABLED=true to opt in.",
        )

    api_key = _api_key()
    if not api_key:
        return OmimBundle(
            gene_symbol=gene_symbol,
            text="OMIM_API_KEY not configured — OMIM source skipped.",
        )

    index = await _get_index(api_key)
    raw_entries = index.get(gene_symbol.upper(), [])

    associations: list[OmimAssociation] = []
    for raw in raw_entries:
        associations.extend(_parse_phenotypes(gene_symbol, raw))

    # Molecularly confirmed (3) first, then chromosomal (4), then linkage (2), then disputed (1).
    _sort_rank = {"3": 3, "4": 2, "2": 1, "1": 0}
    associations.sort(key=lambda a: _sort_rank.get(a.mapping_key or "", 0), reverse=True)

    if not associations:
        text = f"No OMIM Mendelian phenotype associations found for {gene_symbol}."
    else:
        lines = [
            f"{a.phenotype_label} (MIM:{a.mim_number}, {a.mapping_confidence}"
            + (f", {a.inheritance}" if a.inheritance else "")
            + ")"
            for a in associations[:5]
        ]
        text = (
            f"OMIM phenotypes for {gene_symbol}: "
            + "; ".join(lines)
            + (f" [+{len(associations) - 5} more]" if len(associations) > 5 else "")
            + "."
        )

    return OmimBundle(
        gene_symbol=gene_symbol,
        associations=associations,
        total=len(associations),
        text=text,
    )
