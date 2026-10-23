# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""PubTator3 entity annotations of PubMed abstracts (NCBI, public domain).

PubTator3 marks every gene (NCBI Gene id), variant (tmVar: rs id, HGVS and the gene it
belongs to), chemical and disease (MeSH), species (NCBI taxon) and cell line of a title
and abstract, with character offsets into the BioC passages. Downstream code reads the
mentions as identifiers instead of words (services/evidence/grounding.py).

Requests go out in batches of at most 100 PMIDs, through the same throttle and retry as
the E-utilities calls (PubTator is served from the same NCBI infrastructure and rate
limit). A PMID PubTator has not annotated is absent from the reply. Each document is
cached on disk per PMID with the date PubTator gives it. A batch PubTator rejects as a bad
request is split in halves down to single PMIDs, so one rejected PMID costs only itself;
the PMIDs that still fail, and any batch failing otherwise, are recorded as a degradation
and left out.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from core.degradations import record_degradation
from mcp_servers.pubmed.tools import _get_with_retry

logger = logging.getLogger(__name__)

_EXPORT_URL = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson"
BATCH = 100
# v2: documents keep their relations; those cached before have none.
_CACHE_DIR = Path(tempfile.gettempdir()) / "pubtator_cache" / "v2"
# Annotations are refreshed when PubTator re-runs its taggers; a month keeps runs of one
# week consistent without serving years-old annotations.
_CACHE_TTL = 30 * 24 * 3600


class PubTatorAnnotation(BaseModel):
    """One entity mention. ``offset`` is into the whole document, as in BioC."""

    type: str  # Gene | Variant | Chemical | Disease | Species | CellLine | …
    text: str
    offset: int
    length: int
    identifier: str = ""  # NCBI Gene id(s) ";"-joined, "MESH:D…", taxon id, tmVar string
    name: str = ""  # the normalised name PubTator gives
    gene_id: str = ""  # a variant's CorrespondingGene (NCBI Gene id)
    rsid: str = ""  # a variant's rs id ("rs1800775")
    hgvs: str = ""  # a variant's HGVS ("c.-629C>A", "p.R46L")
    subtype: str = ""  # a variant's tmVar kind (DNAMutation, ProteinMutation, SNP, …)
    homologene: str = ""  # a gene's HomoloGene group


class PubTatorPassage(BaseModel):
    kind: str  # title | abstract | …
    offset: int
    text: str
    annotations: list[PubTatorAnnotation] = []


class PubTatorRelation(BaseModel):
    """A relation PubTator3 extracted between two entities of the document: their
    identifiers (as in the annotations), entity types and normalised names. The type
    (Association, Positive_Correlation, Negative_Correlation, Bind, Cotreatment, Comparison,
    Conversion, Drug_Interaction) is a text-mining tag of the co-mention, not a finding."""

    type: str
    role1: str
    role1_type: str = ""
    role1_name: str = ""
    role2: str
    role2_type: str = ""
    role2_name: str = ""


class PubTatorDocument(BaseModel):
    pmid: str
    date: str = ""  # the date PubTator gives the document
    passages: list[PubTatorPassage] = []
    relations: list[PubTatorRelation] = []


_TMVAR_FIELD = re.compile(r"(?P<key>CorrespondingGene|RS#|HGVS):(?P<value>[^;]+)")


def _str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ";".join(str(v) for v in value if v is not None)
    return str(value)


def _annotation(raw: Mapping[str, Any]) -> PubTatorAnnotation | None:
    infons = raw.get("infons") or {}
    locations = raw.get("locations") or []
    if not locations:
        return None
    identifier = _str(infons.get("identifier"))
    tmvar = {m.group("key"): m.group("value") for m in _TMVAR_FIELD.finditer(identifier)}
    rsids = infons.get("rsids") or []
    rsid = str(rsids[0]) if rsids else (f"rs{tmvar['RS#']}" if tmvar.get("RS#") else "")
    return PubTatorAnnotation(
        type=str(infons.get("type") or ""),
        text=str(raw.get("text") or ""),
        offset=int(locations[0].get("offset") or 0),
        length=int(locations[0].get("length") or 0),
        identifier=identifier,
        name=_str(infons.get("name")),
        gene_id=_str(infons.get("gene_id")) or tmvar.get("CorrespondingGene", ""),
        rsid=rsid.lower(),
        hgvs=_str(infons.get("hgvs")) or tmvar.get("HGVS", ""),
        subtype=_str(infons.get("subtype")),
        homologene=_str(infons.get("ncbi_homologene")),
    )


def _relation(raw: Mapping[str, Any]) -> PubTatorRelation | None:
    infons = raw.get("infons") or {}
    one, two = infons.get("role1") or {}, infons.get("role2") or {}
    if not isinstance(one, Mapping) or not isinstance(two, Mapping):
        return None
    if not one.get("identifier") or not two.get("identifier"):
        return None
    return PubTatorRelation(
        type=str(infons.get("type") or ""),
        role1=_str(one.get("identifier")),
        role1_type=_str(one.get("type")),
        role1_name=_str(one.get("name")),
        role2=_str(two.get("identifier")),
        role2_type=_str(two.get("type")),
        role2_name=_str(two.get("name")),
    )


def parse_biocjson(data: Mapping[str, Any] | list[Any]) -> list[PubTatorDocument]:
    """The documents of a PubTator3 BioC JSON export (``{"PubTator3": [...]}``)."""
    docs = data.get("PubTator3") if isinstance(data, Mapping) else data
    out: list[PubTatorDocument] = []
    for doc in docs or []:
        pmid = str(doc.get("pmid") or doc.get("id") or "").strip()
        if not pmid:
            continue
        passages = [
            PubTatorPassage(
                kind=str((p.get("infons") or {}).get("type") or ""),
                offset=int(p.get("offset") or 0),
                text=str(p.get("text") or ""),
                annotations=[
                    a for raw in p.get("annotations") or [] if (a := _annotation(raw)) is not None
                ],
            )
            for p in doc.get("passages") or []
        ]
        relations = [r for raw in doc.get("relations") or [] if (r := _relation(raw)) is not None]
        out.append(
            PubTatorDocument(
                pmid=pmid,
                date=str(doc.get("date") or ""),
                passages=passages,
                relations=relations,
            )
        )
    return out


def _cache_path(pmid: str) -> Path:
    return _CACHE_DIR / f"{pmid}.json"


def _cached(pmid: str) -> PubTatorDocument | None:
    path = _cache_path(pmid)
    try:
        if time.time() - path.stat().st_mtime > _CACHE_TTL:
            return None
        return PubTatorDocument.model_validate_json(path.read_text())
    except (OSError, ValueError):
        return None


def _store(doc: PubTatorDocument) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(doc.pmid).write_text(doc.model_dump_json())
    except OSError as exc:
        logger.info("PubTator cache write failed for %s: %s", doc.pmid, exc)


class _Rejected(RuntimeError):
    """PubTator answered a batch with HTTP 400: something in the request, not the service."""


async def _fetch_batch(pmids: list[str]) -> list[PubTatorDocument]:
    response = await _get_with_retry(_EXPORT_URL, {"pmids": ",".join(pmids)}, json=True)
    if response.status_code == 400:
        raise _Rejected(f"PubTator3 returned HTTP {response.status_code}")
    if response.status_code != 200:
        raise RuntimeError(f"PubTator3 returned HTTP {response.status_code}")
    return parse_biocjson(json.loads(response.text, strict=False))


async def _fetch_splitting(pmids: list[str]) -> tuple[list[PubTatorDocument], list[str]]:
    """The documents of ``pmids`` and the PMIDs PubTator rejects: a rejected batch is
    retried as two halves, down to single PMIDs. Any other failure propagates."""
    try:
        return await _fetch_batch(pmids), []
    except _Rejected:
        if len(pmids) == 1:
            return [], pmids
    half = len(pmids) // 2
    left, left_failed = await _fetch_splitting(pmids[:half])
    right, right_failed = await _fetch_splitting(pmids[half:])
    return left + right, left_failed + right_failed


def _pmid(value: str) -> str:
    return str(value).strip().removeprefix("PMID:")


async def get_annotations(pmids: Iterable[str]) -> list[PubTatorDocument]:
    """PubTator3 annotations of the titles and abstracts of ``pmids`` ("123" or
    "PMID:123"), in request order; a PMID not annotated, rejected, or in a failed batch
    is left out."""
    wanted = [p for p in dict.fromkeys(_pmid(p) for p in pmids) if p.isdigit()]
    found: dict[str, PubTatorDocument] = {}
    todo: list[str] = []
    for pmid in wanted:
        doc = _cached(pmid)
        if doc is None:
            todo.append(pmid)
        else:
            found[pmid] = doc
    failed = 0
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        try:
            docs, rejected = await _fetch_splitting(batch)
        except Exception as exc:
            failed += len(batch)
            record_degradation(
                "literature: PubTator3 annotations",
                f"{type(exc).__name__}: {exc}",
                f"{len(batch)} paper(s) have no grounded entities, so no statements",
            )
            continue
        if rejected:
            failed += len(rejected)
            record_degradation(
                "literature: PubTator3 annotations",
                f"HTTP 400 for PMID(s) {', '.join(rejected)}",
                f"{len(rejected)} paper(s) have no grounded entities, so no statements",
            )
        for doc in docs:
            found[doc.pmid] = doc
            _store(doc)
    logger.info(
        "PubTator3: %d of %d PMID(s) annotated (%d from the cache, %d failed)",
        len(found),
        len(wanted),
        len(wanted) - len(todo),
        failed,
    )
    return [found[p] for p in wanted if p in found]
