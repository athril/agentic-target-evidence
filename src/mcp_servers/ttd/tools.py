# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""TTD (Therapeutic Target Database) target development-status tools.

TTD has no public REST/JSON API; it publishes per-target records as a bulk
``KEY\\tVALUE`` text file (blocks separated by blank lines, one block per
target) on its "Data Download" page, refreshed periodically. We download it
once, cache it locally, and index it by gene symbol — mirroring the bulk-file
pattern used for OMIM (see mcp_servers/omim/tools.py) and ClinGen.

This is additive over DGIdb (mcp_servers/dgidb), which already aggregates
generic drug-gene interaction claims sourced in part from TTD: TTD's own
target development-stage classification ("Successful Target" / "Clinical
Trial Target" / "Research Target" / ...) and the drugs TTD itself maps to
that target are not exposed by DGIdb.

TTD's site (ttd.idrblab.cn) is a client-rendered SPA that could not be read by
automated fetchers when this integration was written, so neither the exact
current download URL nor TTD's current commercial-use terms could be
independently verified here. Treated conservatively as non-commercial pending
confirmation (see NOTICE.md) and gated behind ``TTD_ENABLED`` (off by
default), mirroring OMIM/SCImago. ``_DOWNLOAD_URL`` below is a placeholder —
confirm the current bulk-download link and field layout from TTD's "Data
Download" page before enabling this source.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

# TODO: unverified placeholder — confirm against TTD's current "Data Download" page
# (target info file, e.g. "P1-01-TTD_target_download.txt") before enabling.
_DOWNLOAD_URL = "https://ttd.idrblab.cn/sites/default/files/ttd_database/P1-01-TTD_target_download.txt"

_CACHE_DIR = Path(tempfile.gettempdir()) / "ttd_cache"
_CACHE_PATH = _CACHE_DIR / "ttd_target_download.txt"
_CACHE_TTL = 24 * 3600

# TTD's own target development-stage classification, most- to least-advanced.
_STATUS_RANK = {
    "successful target": 4,
    "clinical trial target": 3,
    "preclinical trial target": 2,
    "patented agent target": 1,
    "research target": 0,
}


class TtdDrug(BaseModel):
    drug_id: str
    drug_name: str = ""


class TtdTargetRecord(BaseModel):
    ttd_target_id: str
    gene_symbol: str
    target_name: str = ""
    uniprot_id: str = ""
    development_status: str = ""
    drugs: list[TtdDrug] = []


class TtdBundle(BaseModel):
    gene_symbol: str
    record: TtdTargetRecord | None = None
    text: str = ""


_index: dict[str, TtdTargetRecord] | None = None
_index_mtime: float | None = None


def _enabled() -> bool:
    """TTD's commercial-use terms are unconfirmed for this integration (see
    module docstring), so it defaults off like OMIM/SCImago; set
    TTD_ENABLED=true once you've confirmed current terms permit your use case.
    """
    return os.getenv("TTD_ENABLED", "false").strip().lower() == "true"


def ttd_configured() -> bool:
    """True when TTD has been explicitly opted into via ``TTD_ENABLED``."""
    return _enabled()


async def _download_bulk_file() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = _CACHE_PATH.with_name(_CACHE_PATH.name + ".tmp")
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(_DOWNLOAD_URL)
        if resp.status_code != 200:
            raise MCPToolError(f"TTD bulk dataset download failed: HTTP {resp.status_code}")
        tmp_path.write_bytes(resp.content)
        tmp_path.replace(_CACHE_PATH)
    except httpx.HTTPError as exc:
        raise MCPToolError(f"TTD bulk dataset download failed: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def _cache_valid() -> bool:
    return _CACHE_PATH.exists() and (time.time() - _CACHE_PATH.stat().st_mtime) < _CACHE_TTL


async def _ensure_cached() -> Path:
    if not _cache_valid():
        await _download_bulk_file()
    return _CACHE_PATH


def _build_index(path: Path) -> dict[str, TtdTargetRecord]:
    """Parse TTD's per-target ``KEY\\tVALUE`` block format.

    Each target is a run of ``KEY\\tVALUE...`` lines, blocks separated by
    blank lines. ``DRUGINFO`` repeats per drug mapped to the target
    (``DRUGINFO\\t<id>\\t<name>``); every other key is taken once per block.
    """
    index: dict[str, TtdTargetRecord] = {}
    fields: dict[str, str] = {}
    drugs: list[TtdDrug] = []

    def _flush() -> None:
        gene = fields.get("GENENAME", "").strip()
        if not gene:
            return
        index[gene.upper()] = TtdTargetRecord(
            ttd_target_id=fields.get("TARGETID", "").strip(),
            gene_symbol=gene,
            target_name=fields.get("TARGNAME", "").strip(),
            uniprot_id=fields.get("UNIPROID", "").strip(),
            development_status=fields.get("TARGTYPE", "").strip(),
            drugs=list(drugs),
        )

    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if not line.strip():
                if fields:
                    _flush()
                fields = {}
                drugs = []
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            key = parts[0].strip()
            if key == "DRUGINFO":
                drug_id = parts[1].strip() if len(parts) > 1 else ""
                drug_name = parts[2].strip() if len(parts) > 2 else ""
                if drug_id or drug_name:
                    drugs.append(TtdDrug(drug_id=drug_id, drug_name=drug_name))
                continue
            fields[key] = parts[1].strip()
    if fields:
        _flush()
    return index


async def _get_index() -> dict[str, TtdTargetRecord]:
    global _index, _index_mtime
    path = await _ensure_cached()
    mtime = path.stat().st_mtime
    if _index is None or _index_mtime != mtime:
        _index = _build_index(path)
        _index_mtime = mtime
    return _index


async def get_ttd_target_status(gene_symbol: str) -> TtdBundle:
    """Fetch TTD target development-status and mapped drugs for a gene.

    Returns an empty bundle (no error) if TTD is disabled (``TTD_ENABLED``
    off — see module docstring) rather than failing the run. TTD is an
    optional source; callers that want to skip the call (and its trace span)
    entirely should use ``ttd_configured()`` rather than re-checking the env
    themselves.
    """
    if not _enabled():
        return TtdBundle(
            gene_symbol=gene_symbol,
            text="TTD disabled (commercial-use terms unconfirmed) — set TTD_ENABLED=true to opt in.",
        )

    index = await _get_index()
    record = index.get(gene_symbol.upper())

    if record is None:
        return TtdBundle(
            gene_symbol=gene_symbol,
            text=f"No TTD target record found for {gene_symbol}.",
        )

    drug_names = ", ".join(d.drug_name for d in record.drugs[:5] if d.drug_name)
    text = (
        f"TTD target development status for {gene_symbol}: "
        f"{record.development_status or 'unknown'} ({record.ttd_target_id}). "
        f"Mapped drugs: {len(record.drugs)}" + (f" (e.g. {drug_names})" if drug_names else "") + "."
    )
    return TtdBundle(gene_symbol=gene_symbol, record=record, text=text)
