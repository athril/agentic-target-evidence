# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinGen Gene Validity tools.

ClinGen retired its public GraphQL API (search.clinicalgenome.org/kb/graphql
and genegraph.clinicalgenome.org/api are both gone). search.clinicalgenome.org's
robots.txt now disallows all crawlers except a hand-picked list of search-engine
bots, so per-gene scraping of its HTML/`/api/genes` endpoints would run against
the site's stated crawling policy.

The sanctioned machine-readable path is the bulk Gene-Disease Validity dataset
ClinGen publishes for exactly this purpose from the genegraph.clinicalgenome.org
"Downloads" page: JSON-LD records for every curated assertion, refreshed daily,
served from a Google Cloud Storage bucket with no crawl restrictions. We
download it once, cache it locally, and look genes up in the cached index —
mirroring the bulk-file pattern already used for DepMap (see
mcp_servers/depmap/tools.py).
"""

from __future__ import annotations

import json
import re
import tarfile
import tempfile
import time
from pathlib import Path

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from mcp_servers.ontology.tools import (
    HPO_INHERITANCE_LABELS,
    resolve_hgnc_symbol,
    resolve_mondo_term,
)

_BULK_URL = (
    "https://storage.googleapis.com/genegraph-stage-public/clingen-gene-validity-json-latest.tar.gz"
)

# Dataset is regenerated ~daily; cache it locally instead of re-downloading per call.
_CACHE_DIR = Path(tempfile.gettempdir()) / "clingen_cache"
_CACHE_PATH = _CACHE_DIR / "clingen-gene-validity-json-latest.tar.gz"
_CACHE_TTL = 24 * 3600

_CLASSIFICATION_RANK = {
    "Definitive": 5,
    "Strong": 4,
    "Moderate": 3,
    "Limited": 2,
    "No Known Disease Relationship": 1,
    "Disputed": 0,
    "Refuted": -1,
}

# Dataset spells the "no relationship" classification as one PascalCase token
# (e.g. "NoKnownDiseaseRelationship"); split it into the old GraphQL API's
# spaced form so _CLASSIFICATION_RANK and downstream consumers don't need
# to special-case the new spelling.
_PASCAL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")


class ClinGenAssociation(BaseModel):
    gene_symbol: str
    hgnc_id: str | None = None
    disease_label: str
    disease_curie: str | None = None
    classification: str
    report_date: str | None = None
    report_url: str | None = None
    mode_of_inheritance: str | None = None
    mode_of_inheritance_curie: str | None = None


class ClinGenBundle(BaseModel):
    gene_symbol: str
    associations: list[ClinGenAssociation] = []
    total: int = 0
    text: str = ""


# In-process caches: the parsed gene-validity index (keyed by mtime so a
# fresh download invalidates it) and a small MONDO id -> label cache to
# avoid repeat OLS lookups for common diseases within one process lifetime.
_index: dict[str, list[dict]] | None = None
_index_mtime: float | None = None
_mondo_label_cache: dict[str, str] = {}


def _normalize_classification(raw: str) -> str:
    return _PASCAL_CASE_RE.sub(" ", raw) if raw else "Unknown"


def _curie_from_obo(value: object) -> str | None:
    """'obo:MONDO_0015452' -> 'MONDO:0015452'; None for anything else."""
    if not isinstance(value, str) or not value.startswith("obo:"):
        return None
    prefix, _, local_id = value[len("obo:") :].partition("_")
    return f"{prefix}:{local_id}" if prefix and local_id else None


def _mode_of_inheritance(proposition: dict) -> tuple[str | None, str | None]:
    """(label, curie) for proposition.qualifierModeOfInheritance, e.g. 'Autosomal dominant', 'HP:0000006'."""
    curie = _curie_from_obo(proposition.get("qualifierModeOfInheritance"))
    if curie is None:
        return None, None
    return HPO_INHERITANCE_LABELS.get(curie), curie


def _evaluated_date(contributions: list[dict]) -> str | None:
    """Date the classification was approved ('Evaluated'), falling back to 'Submitted'."""
    by_type = {c.get("activityType"): c.get("date") for c in contributions}
    date = by_type.get("Evaluated") or by_type.get("Submitted")
    return date.split("T")[0] if date else None


async def _download_bulk_file() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = _CACHE_PATH.with_name(_CACHE_PATH.name + ".tmp")
    try:
        async with (
            httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client,
            client.stream("GET", _BULK_URL) as resp,
        ):
            if resp.status_code != 200:
                raise MCPToolError(f"ClinGen bulk dataset download failed: HTTP {resp.status_code}")
            with open(tmp_path, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
        tmp_path.replace(_CACHE_PATH)
    except httpx.HTTPError as exc:
        raise MCPToolError(f"ClinGen bulk dataset download failed: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def _cache_valid() -> bool:
    return _CACHE_PATH.exists() and (time.time() - _CACHE_PATH.stat().st_mtime) < _CACHE_TTL


async def _ensure_cached() -> Path:
    if not _cache_valid():
        await _download_bulk_file()
    return _CACHE_PATH


def _build_index(tar_path: Path) -> dict[str, list[dict]]:
    """Group gene-validity JSON-LD records by lowercased 'hgnc:<id>' subject."""
    index: dict[str, list[dict]] = {}
    with tarfile.open(tar_path, "r:gz") as tf:
        for member in tf:
            f = tf.extractfile(member)
            if f is None:
                continue
            try:
                record = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            subject = record.get("subject")
            subjects = subject if isinstance(subject, list) else [subject]
            for s in subjects:
                if isinstance(s, str) and s.lower().startswith("hgnc:"):
                    index.setdefault(s.lower(), []).append(record)
    return index


async def _get_index() -> dict[str, list[dict]]:
    global _index, _index_mtime
    tar_path = await _ensure_cached()
    mtime = tar_path.stat().st_mtime
    if _index is None or _index_mtime != mtime:
        _index = _build_index(tar_path)
        _index_mtime = mtime
    return _index


async def _disease_label(disease_curie: str | None) -> str:
    if not disease_curie:
        return "Unknown disease"
    if disease_curie in _mondo_label_cache:
        return _mondo_label_cache[disease_curie]
    try:
        mondo = await resolve_mondo_term(disease_curie)
        _mondo_label_cache[disease_curie] = mondo.label
        return mondo.label
    except MCPToolError:
        return disease_curie


async def get_clingen_validity(gene_symbol: str) -> ClinGenBundle:
    """Fetch ClinGen gene-disease validity classifications for a gene.

    Resolves the symbol to an HGNC id via the HGNC REST API, then looks it up
    in ClinGen's bulk Gene-Disease Validity dataset (downloaded once, cached
    locally — see module docstring for why this replaced the old GraphQL call).
    """
    try:
        hgnc = await resolve_hgnc_symbol(gene_symbol)
        hgnc_id = hgnc.hgnc_id or None
    except MCPToolError:
        hgnc_id = None

    if not hgnc_id:
        return ClinGenBundle(
            gene_symbol=gene_symbol,
            text=f"No ClinGen gene-disease validity assertions found for {gene_symbol}.",
        )

    index = await _get_index()
    records = index.get(hgnc_id.lower(), [])

    associations: list[ClinGenAssociation] = []
    for record in records:
        proposition = record.get("proposition") or {}
        disease_curie = _curie_from_obo(proposition.get("objectCondition"))
        moi_label, moi_curie = _mode_of_inheritance(proposition)
        associations.append(
            ClinGenAssociation(
                gene_symbol=gene_symbol,
                hgnc_id=hgnc_id,
                disease_label=await _disease_label(disease_curie),
                disease_curie=disease_curie,
                classification=_normalize_classification(record.get("classification")),
                report_date=_evaluated_date(record.get("contributions") or []),
                report_url=None,
                mode_of_inheritance=moi_label,
                mode_of_inheritance_curie=moi_curie,
            )
        )

    # Sort strongest classification first
    associations.sort(
        key=lambda a: _CLASSIFICATION_RANK.get(a.classification, 0),
        reverse=True,
    )

    if not associations:
        text = f"No ClinGen gene-disease validity assertions found for {gene_symbol}."
    else:
        lines = [
            f"{a.disease_label} ({a.classification}"
            + (f", {a.mode_of_inheritance}" if a.mode_of_inheritance else "")
            + ")"
            for a in associations[:5]
        ]
        text = (
            f"ClinGen gene validity for {gene_symbol}: "
            + "; ".join(lines)
            + (f" [+{len(associations) - 5} more]" if len(associations) > 5 else "")
            + "."
        )

    return ClinGenBundle(
        gene_symbol=gene_symbol,
        associations=associations,
        total=len(associations),
        text=text,
    )
