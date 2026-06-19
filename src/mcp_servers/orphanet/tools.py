# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Orphanet rare-disease gene-disease association tools.

Orphadata publishes the "Genes associated with rare disorders" cross-reference
(product 6) as a bulk XML file, refreshed periodically, no API key required.
Each association carries an explicit relationship type (e.g. "Disease-causing
germline mutation(s) in" vs. "Major susceptibility factor in" vs. "Modifying
germline mutation in") and a curation status (Assessed / Not yet assessed) —
finer-grained than a single validity classification, which is what makes
Orphanet additive alongside ClinGen/GenCC/OMIM for rare disease.

We download the bulk file once, cache it locally, and index it by gene
symbol — mirroring the bulk-file pattern used for ClinGen
(see mcp_servers/clingen/tools.py module docstring).
"""

from __future__ import annotations

import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError

_BULK_URL = "https://www.orphadata.com/data/xml/en_product6.xml"

_CACHE_DIR = Path(tempfile.gettempdir()) / "orphanet_cache"
_CACHE_PATH = _CACHE_DIR / "en_product6.xml"
_CACHE_TTL = 24 * 3600

# Curation status is the strongest available ordering signal in this dataset
# (there is no single validity-strength scale like ClinGen's).
_STATUS_RANK = {"Assessed": 1, "Not yet assessed": 0}


class OrphanetAssociation(BaseModel):
    gene_symbol: str
    orphacode: str
    disorder_name: str
    association_type: str
    association_status: str


class OrphanetBundle(BaseModel):
    gene_symbol: str
    associations: list[OrphanetAssociation] = []
    total: int = 0
    text: str = ""


_index: dict[str, list[dict[str, str]]] | None = None
_index_mtime: float | None = None


async def _download_bulk_file() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = _CACHE_PATH.with_name(_CACHE_PATH.name + ".tmp")
    try:
        async with (
            httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client,
            client.stream("GET", _BULK_URL) as resp,
        ):
            if resp.status_code != 200:
                raise MCPToolError(
                    f"Orphanet bulk dataset download failed: HTTP {resp.status_code}"
                )
            with open(tmp_path, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
        tmp_path.replace(_CACHE_PATH)
    except httpx.HTTPError as exc:
        raise MCPToolError(f"Orphanet bulk dataset download failed: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def _cache_valid() -> bool:
    return _CACHE_PATH.exists() and (time.time() - _CACHE_PATH.stat().st_mtime) < _CACHE_TTL


async def _ensure_cached() -> Path:
    if not _cache_valid():
        await _download_bulk_file()
    return _CACHE_PATH


def _text(elem: ET.Element | None, tag: str) -> str:
    if elem is None:
        return ""
    child = elem.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def _build_index(xml_path: Path) -> dict[str, list[dict[str, str]]]:
    """Group gene-disease association records by uppercased gene symbol."""
    index: dict[str, list[dict[str, str]]] = {}
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        raise MCPToolError(f"Orphanet bulk dataset is not valid XML: {exc}") from exc

    for disorder in tree.getroot().iter("Disorder"):
        orphacode = _text(disorder, "OrphaCode")
        disorder_name = ""
        name_elem = disorder.find("Name")
        if name_elem is not None and name_elem.text:
            disorder_name = name_elem.text.strip()

        assoc_list = disorder.find("DisorderGeneAssociationList")
        if assoc_list is None:
            continue
        for assoc in assoc_list.findall("DisorderGeneAssociation"):
            gene = assoc.find("Gene")
            symbol = _text(gene, "Symbol")
            if not symbol:
                continue
            assoc_type = _text(assoc.find("DisorderGeneAssociationType"), "Name")
            assoc_status = _text(assoc.find("DisorderGeneAssociationStatus"), "Name")
            index.setdefault(symbol.upper(), []).append(
                {
                    "orphacode": orphacode,
                    "disorder_name": disorder_name,
                    "association_type": assoc_type,
                    "association_status": assoc_status,
                }
            )
    return index


async def _get_index() -> dict[str, list[dict[str, str]]]:
    global _index, _index_mtime
    xml_path = await _ensure_cached()
    mtime = xml_path.stat().st_mtime
    if _index is None or _index_mtime != mtime:
        _index = _build_index(xml_path)
        _index_mtime = mtime
    return _index


async def get_orphanet_associations(gene_symbol: str) -> OrphanetBundle:
    """Fetch Orphanet rare-disease gene-disease associations for a gene."""
    index = await _get_index()
    rows = index.get(gene_symbol.upper(), [])

    associations = [
        OrphanetAssociation(
            gene_symbol=gene_symbol,
            orphacode=row["orphacode"],
            disorder_name=row["disorder_name"] or "Unknown disorder",
            association_type=row["association_type"] or "Unknown",
            association_status=row["association_status"] or "Unknown",
        )
        for row in rows
    ]
    associations.sort(
        key=lambda a: _STATUS_RANK.get(a.association_status, 0),
        reverse=True,
    )

    if not associations:
        text = f"No Orphanet rare-disease gene associations found for {gene_symbol}."
    else:
        lines = [
            f"{a.disorder_name} (ORPHA:{a.orphacode}; {a.association_type}; {a.association_status})"
            for a in associations[:5]
        ]
        text = (
            f"Orphanet associations for {gene_symbol}: "
            + "; ".join(lines)
            + (f" [+{len(associations) - 5} more]" if len(associations) > 5 else "")
            + "."
        )

    return OrphanetBundle(
        gene_symbol=gene_symbol,
        associations=associations,
        total=len(associations),
        text=text,
    )
