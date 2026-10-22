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

The phenotypes of a disorder (product 4: HPO terms with Orphanet's frequency classes)
and its natural history (product 9 ages: average age of onset, type of inheritance)
come from the same bulk downloads, indexed by OrphaCode. All products are CC BY 4.0.
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
_PREVALENCE_BULK_URL = "https://www.orphadata.com/data/xml/en_product9_prev.xml"
_PHENOTYPE_BULK_URL = "https://www.orphadata.com/data/xml/en_product4.xml"
_AGES_BULK_URL = "https://www.orphadata.com/data/xml/en_product9_ages.xml"

_CACHE_DIR = Path(tempfile.gettempdir()) / "orphanet_cache"
_CACHE_PATH = _CACHE_DIR / "en_product6.xml"
_PREVALENCE_CACHE_PATH = _CACHE_DIR / "en_product9_prev.xml"
_PHENOTYPE_CACHE_PATH = _CACHE_DIR / "en_product4.xml"
_AGES_CACHE_PATH = _CACHE_DIR / "en_product9_ages.xml"
_CACHE_TTL = 24 * 3600

# Curation status is the strongest available ordering signal in this dataset
# (there is no single validity-strength scale like ClinGen's).
_STATUS_RANK = {"Assessed": 1, "Not yet assessed": 0}

# Prefer validated, worldwide, point-prevalence records when a disorder has
# several prevalence rows from different studies/geographies.
_VALIDATION_RANK = {"Validated": 1, "Not yet validated": 0}


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


class OrphanetPrevalence(BaseModel):
    orphacode: str
    disorder_name: str
    prevalence_type: str
    prevalence_class: str
    geographic_area: str
    validation_status: str


class OrphanetPrevalenceBundle(BaseModel):
    orphacodes: list[str] = []
    records: list[OrphanetPrevalence] = []
    total: int = 0
    text: str = ""


class OrphanetPhenotype(BaseModel):
    hpo_id: str
    term: str
    frequency: str  # Orphanet's frequency class, e.g. "Very frequent (99-80%)"


class OrphanetDisorderProfile(BaseModel):
    orphacode: str
    disorder_name: str
    phenotypes: list[OrphanetPhenotype] = []
    onset: list[str] = []  # average age of onset classes, e.g. "Neonatal"
    inheritance: list[str] = []  # types of inheritance, e.g. "Autosomal dominant"


class OrphanetPhenotypeBundle(BaseModel):
    orphacodes: list[str] = []
    disorders: list[OrphanetDisorderProfile] = []
    total: int = 0
    text: str = ""


_index: dict[str, list[dict[str, str]]] | None = None
_index_mtime: float | None = None

_prevalence_index: dict[str, list[dict[str, str]]] | None = None
_prevalence_index_mtime: float | None = None

_phenotype_index: dict[str, OrphanetDisorderProfile] | None = None
_phenotype_index_mtimes: tuple[float, float] | None = None


async def _download_bulk_file(url: str = _BULK_URL, cache_path: Path = _CACHE_PATH) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_name(cache_path.name + ".tmp")
    try:
        async with (
            httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client,
            client.stream("GET", url) as resp,
        ):
            if resp.status_code != 200:
                raise MCPToolError(
                    f"Orphanet bulk dataset download failed: HTTP {resp.status_code}"
                )
            with open(tmp_path, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
        tmp_path.replace(cache_path)
    except httpx.HTTPError as exc:
        raise MCPToolError(f"Orphanet bulk dataset download failed: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def _cache_valid(cache_path: Path = _CACHE_PATH) -> bool:
    return cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < _CACHE_TTL


async def _ensure_cached(url: str = _BULK_URL, cache_path: Path = _CACHE_PATH) -> Path:
    if not _cache_valid(cache_path):
        await _download_bulk_file(url, cache_path)
    return cache_path


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


def _build_prevalence_index(xml_path: Path) -> dict[str, list[dict[str, str]]]:
    """Group prevalence records (product 9) by OrphaCode."""
    index: dict[str, list[dict[str, str]]] = {}
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        raise MCPToolError(f"Orphanet prevalence dataset is not valid XML: {exc}") from exc

    for disorder in tree.getroot().iter("Disorder"):
        orphacode = _text(disorder, "OrphaCode")
        if not orphacode:
            continue
        disorder_name = ""
        name_elem = disorder.find("Name")
        if name_elem is not None and name_elem.text:
            disorder_name = name_elem.text.strip()

        prevalence_list = disorder.find("PrevalenceList")
        if prevalence_list is None:
            continue
        for prevalence in prevalence_list.findall("Prevalence"):
            index.setdefault(orphacode, []).append(
                {
                    "disorder_name": disorder_name,
                    "prevalence_type": _text(prevalence.find("PrevalenceType"), "Name"),
                    "prevalence_class": _text(prevalence.find("PrevalenceClass"), "Name"),
                    "geographic_area": _text(prevalence.find("PrevalenceGeographic"), "Name"),
                    "validation_status": _text(
                        prevalence.find("PrevalenceValidationStatus"), "Name"
                    ),
                }
            )
    return index


async def _get_prevalence_index() -> dict[str, list[dict[str, str]]]:
    global _prevalence_index, _prevalence_index_mtime
    xml_path = await _ensure_cached(_PREVALENCE_BULK_URL, _PREVALENCE_CACHE_PATH)
    mtime = xml_path.stat().st_mtime
    if _prevalence_index is None or _prevalence_index_mtime != mtime:
        _prevalence_index = _build_prevalence_index(xml_path)
        _prevalence_index_mtime = mtime
    return _prevalence_index


def _disorder_name(disorder: ET.Element) -> str:
    name_elem = disorder.find("Name")
    return (name_elem.text or "").strip() if name_elem is not None else ""


def _build_phenotype_index(
    phenotype_path: Path, ages_path: Path
) -> dict[str, OrphanetDisorderProfile]:
    """Disorder profiles by OrphaCode: HPO phenotypes (product 4) joined with onset and
    inheritance (product 9 ages)."""
    try:
        phenotypes = ET.parse(phenotype_path)
        ages = ET.parse(ages_path)
    except ET.ParseError as exc:
        raise MCPToolError(f"Orphanet phenotype dataset is not valid XML: {exc}") from exc

    index: dict[str, OrphanetDisorderProfile] = {}
    for disorder in phenotypes.getroot().iter("Disorder"):
        orphacode = _text(disorder, "OrphaCode")
        assoc_list = disorder.find("HPODisorderAssociationList")
        if not orphacode or assoc_list is None:
            continue
        index[orphacode] = OrphanetDisorderProfile(
            orphacode=orphacode,
            disorder_name=_disorder_name(disorder),
            phenotypes=[
                OrphanetPhenotype(
                    hpo_id=_text(assoc.find("HPO"), "HPOId"),
                    term=_text(assoc.find("HPO"), "HPOTerm"),
                    frequency=_text(assoc.find("HPOFrequency"), "Name"),
                )
                for assoc in assoc_list.findall("HPODisorderAssociation")
                if _text(assoc.find("HPO"), "HPOTerm")
            ],
        )
    for disorder in ages.getroot().iter("Disorder"):
        orphacode = _text(disorder, "OrphaCode")
        if not orphacode:
            continue
        profile = index.setdefault(
            orphacode,
            OrphanetDisorderProfile(orphacode=orphacode, disorder_name=_disorder_name(disorder)),
        )
        profile.onset = [
            _text(o, "Name") for o in disorder.iter("AverageAgeOfOnset") if _text(o, "Name")
        ]
        profile.inheritance = [
            _text(t, "Name") for t in disorder.iter("TypeOfInheritance") if _text(t, "Name")
        ]
    return index


async def _get_phenotype_index() -> dict[str, OrphanetDisorderProfile]:
    global _phenotype_index, _phenotype_index_mtimes
    phenotype_path = await _ensure_cached(_PHENOTYPE_BULK_URL, _PHENOTYPE_CACHE_PATH)
    ages_path = await _ensure_cached(_AGES_BULK_URL, _AGES_CACHE_PATH)
    mtimes = (phenotype_path.stat().st_mtime, ages_path.stat().st_mtime)
    if _phenotype_index is None or _phenotype_index_mtimes != mtimes:
        _phenotype_index = _build_phenotype_index(phenotype_path, ages_path)
        _phenotype_index_mtimes = mtimes
    return _phenotype_index


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


async def get_orphanet_prevalence(orphacodes: list[str]) -> OrphanetPrevalenceBundle:
    """Fetch Orphanet disease-prevalence records (product 9) for a set of OrphaCodes.

    Disease prevalence is the addressable-population signal for a target's
    indication — it has no causal bearing on the gene-disease relationship,
    which is why it is sourced and reported separately from the product-6
    gene-disease associations above.
    """
    index = await _get_prevalence_index()

    records: list[OrphanetPrevalence] = []
    for orphacode in orphacodes:
        rows = index.get(orphacode, [])
        rows = sorted(
            rows,
            key=lambda r: _VALIDATION_RANK.get(r["validation_status"], 0),
            reverse=True,
        )
        for row in rows:
            records.append(
                OrphanetPrevalence(
                    orphacode=orphacode,
                    disorder_name=row["disorder_name"] or "Unknown disorder",
                    prevalence_type=row["prevalence_type"] or "Unknown",
                    prevalence_class=row["prevalence_class"] or "Unknown",
                    geographic_area=row["geographic_area"] or "Unknown",
                    validation_status=row["validation_status"] or "Unknown",
                )
            )

    if not records:
        text = f"No Orphanet prevalence records found for OrphaCode(s) {', '.join(orphacodes) or 'none provided'}."
    else:
        lines = [
            f"{r.disorder_name} (ORPHA:{r.orphacode}): {r.prevalence_class} "
            f"[{r.prevalence_type}, {r.geographic_area}, {r.validation_status}]"
            for r in records[:5]
        ]
        text = (
            "Orphanet prevalence: "
            + "; ".join(lines)
            + (f" [+{len(records) - 5} more]" if len(records) > 5 else "")
            + "."
        )

    return OrphanetPrevalenceBundle(
        orphacodes=orphacodes,
        records=records,
        total=len(records),
        text=text,
    )


async def get_orphanet_phenotypes(orphacodes: list[str]) -> OrphanetPhenotypeBundle:
    """Fetch the HPO phenotypes (with Orphanet frequency classes), average age of onset
    and type of inheritance of a set of Orphanet disorders (products 4 and 9)."""
    index = await _get_phenotype_index()
    disorders = [index[c] for c in dict.fromkeys(orphacodes) if c in index]
    if not disorders:
        text = (
            "No Orphanet phenotype records found for OrphaCode(s) "
            f"{', '.join(orphacodes) or 'none provided'}."
        )
    else:
        text = "Orphanet phenotypes: " + "; ".join(
            f"{d.disorder_name} (ORPHA:{d.orphacode}): {len(d.phenotypes)} HPO term(s)"
            for d in disorders[:5]
        )
    return OrphanetPhenotypeBundle(
        orphacodes=list(orphacodes), disorders=disorders, total=len(disorders), text=text
    )
