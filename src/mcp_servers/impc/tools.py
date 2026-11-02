# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""IMPC (International Mouse Phenotyping Consortium) knockout-mouse phenotype tools.

Live, keyless REST API — a per-gene query, no bulk download (mirrors the
gnomAD/GWAS Catalog live-API shape rather than ClinGen's bulk-file pattern).
This is a different evidentiary axis from the curated human gene-disease
validity sources (OMIM/GenCC/Orphanet/ClinGen): in-vivo knockout phenotype and
viability outcome, the whole-organism analogue of DepMap's cell-line
dependency signal. Feeds ``EvidenceType.FUNCTIONAL_GENOMICS``, not
``GENETICS``.

**Coverage, not silence.** IMPC has phenotyped part of the genome, so an empty
answer carries two very different meanings: the pipeline tested this knockout
and found nothing significant, or it never phenotyped the gene at all. The
former is evidence; the latter is not. ``phenotypingDataAvailable`` and the
production-status fields say which, and ``coverage`` on the bundle carries that
distinction to every caller, so no report can turn "never phenotyped" into "no
knockout phenotype". ``tested_not_significant_systems`` is what makes a real
negative usable: the organ systems IMPC screened without a significant call.

The former SOLR cores under ``/mi/impc/solr/`` were retired (the path 404s while
the rest of ebi.ac.uk answers); this uses the current bulkdata API, which is
keyed by MGI accession only — there is no symbol search — so a human symbol is
resolved through HGNC first.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import get_with_retry
from mcp_servers.ontology.tools import resolve_hgnc_symbol

_API_BASE = "https://www.ebi.ac.uk/mi/impc/bulkdata-api"
_GENE_ENDPOINT = _API_BASE + "/genes/{mgi}"
_GENE_PAGE = "https://www.mousephenotype.org/data/genes/{mgi}"

# Coverage of the source for this gene, not a finding about the gene.
COVERED, NOT_COVERED, COVERAGE_UNKNOWN = "covered", "not_covered", "unknown"

# Viability is read from the significant MP terms (IMPC reports lethality as a
# phenotype call) plus whether the mortality/ageing screen ran at all.
_LETHAL_KEYWORDS = ("lethality", "lethal")
_SUBVIABLE_KEYWORDS = ("subviable",)
_MORTALITY_SYSTEM = "mortality/aging"


class ImpcPhenotype(BaseModel):
    mp_term_name: str
    mp_term_id: str = ""
    # Top-level MP systems the term sits under, as the API gives them: grouping
    # without a separate ontology call.
    top_level_systems: list[str] = []
    # Per-call statistics live in the statisticalResults collection, which answers
    # too slowly to call per gene; these stay unset unless a caller fills them.
    p_value: float | None = None
    zygosity: str = ""
    life_stage_name: str = ""
    procedure_name: str = ""


class ImpcBundle(BaseModel):
    gene_symbol: str
    # "covered": IMPC holds phenotyping data, so an empty phenotype list is a
    # tested negative. "not_covered"/"unknown": it does not, or we could not
    # establish that it does — never an absence claim.
    coverage: str = COVERAGE_UNKNOWN
    coverage_note: str = ""
    mgi_id: str = ""
    viability: str = "unknown"  # "lethal" | "subviable" | "viable" | "unknown"
    phenotypes: list[ImpcPhenotype] = []
    total: int = 0
    significant_systems: list[str] = []
    # Organ systems screened with no significant call: the scope of a negative.
    tested_not_significant_systems: list[str] = []
    source_link: str = ""
    text: str = ""

    @property
    def tested(self) -> bool:
        """Whether an empty ``phenotypes`` list may be read as a negative at all."""
        return self.coverage == COVERED


def _mp_terms(raw: Any) -> list[ImpcPhenotype]:
    """Significant MP terms of a gene record, each with its top-level systems."""
    terms = []
    for term in raw or []:
        if not isinstance(term, dict) or not term.get("mpTermName"):
            continue
        systems = [
            a.get("mpTermName", "")
            for a in (term.get("topLevelAncestors") or [])
            if isinstance(a, dict) and a.get("mpTermName")
        ]
        terms.append(
            ImpcPhenotype(
                mp_term_name=term["mpTermName"],
                mp_term_id=term.get("mpTermId", ""),
                top_level_systems=systems,
            )
        )
    return terms


def _derive_viability(phenotypes: list[ImpcPhenotype], not_significant: list[str]) -> str:
    """Lethality is a phenotype call, so it comes from the significant terms; absence of a
    call counts as viable only when the mortality/ageing screen actually ran."""
    names = [p.mp_term_name.lower() for p in phenotypes]
    if any(kw in n for n in names for kw in _LETHAL_KEYWORDS):
        return "lethal"
    if any(kw in n for n in names for kw in _SUBVIABLE_KEYWORDS):
        return "subviable"
    if _MORTALITY_SYSTEM in {s.lower() for s in not_significant}:
        return "viable"
    return "unknown"


def _not_covered(gene_symbol: str, mgi_id: str, note: str) -> ImpcBundle:
    return ImpcBundle(
        gene_symbol=gene_symbol,
        coverage=NOT_COVERED,
        coverage_note=note,
        mgi_id=mgi_id,
        source_link=_GENE_PAGE.format(mgi=mgi_id) if mgi_id else f"{_API_BASE}/genes",
        text=(
            f"IMPC holds no knockout-mouse phenotyping data for {gene_symbol}: {note}. "
            "This is the source's coverage, not a finding: it does not mean the knockout "
            "has no phenotype, and published phenotypes may exist in MGI."
        ),
    )


async def _resolve_mgi_id(gene_symbol: str) -> str:
    """The mouse ortholog's MGI accession from HGNC; "" when HGNC records none.

    The bulkdata API is keyed by MGI accession and has no symbol search, so a caller that
    passes only a human symbol needs this hop.
    """
    try:
        hgnc = await resolve_hgnc_symbol(gene_symbol)
    except MCPToolError:
        return ""
    return next((m for m in hgnc.mgd_id if m), "")


async def get_impc_phenotypes(gene_symbol: str, mgi_id: str = "") -> ImpcBundle:
    """Fetch IMPC knockout-mouse phenotype calls and phenotyping coverage for a gene.

    ``mgi_id`` is the mouse ortholog's MGI accession (e.g. HGNC ``mgd_id``); when it is not
    given it is resolved through HGNC, since the API has no symbol search.

    An empty phenotype list means "tested, nothing significant" only when
    ``coverage == "covered"``; otherwise IMPC holds no data for the gene and the bundle says
    so. Callers must not read ``phenotypes == []`` as a negative without checking ``tested``.
    """
    mgi = mgi_id or await _resolve_mgi_id(gene_symbol)
    if not mgi:
        return ImpcBundle(
            gene_symbol=gene_symbol,
            coverage=COVERAGE_UNKNOWN,
            coverage_note="no MGI accession for the mouse ortholog",
            source_link=f"{_API_BASE}/genes",
            text=(
                f"IMPC was not queried for {gene_symbol}: no MGI accession for a mouse "
                "ortholog, and the API has no symbol search. Not checked, not a negative."
            ),
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await get_with_retry(client, _GENE_ENDPOINT.format(mgi=mgi))
        except httpx.HTTPError as exc:
            raise MCPToolError(f"IMPC API request failed: {exc}") from exc

    # The gene index covers what IMPC tracks, so an unknown accession is coverage, not failure.
    if resp.status_code == 404:
        return _not_covered(gene_symbol, mgi, f"{mgi} is not in the IMPC gene index")
    if resp.status_code != 200:
        raise MCPToolError(f"IMPC API returned HTTP {resp.status_code}")

    try:
        record = resp.json()
    except ValueError as exc:
        # A response that cannot be parsed as a record is never a negative.
        raise MCPToolError(f"IMPC API returned an unparsable response: {exc}") from exc
    if not isinstance(record, dict) or not record.get("mgiAccessionId"):
        raise MCPToolError("IMPC API returned no gene record")

    source_link = _GENE_PAGE.format(mgi=mgi)
    statuses = ", ".join(
        f"{label}: {record[field]}"
        for label, field in (
            ("assignment", "assignmentStatus"),
            ("phenotyping", "phenotypeStatus"),
            ("mouse production", "mouseProductionStatus"),
        )
        if record.get(field)
    )
    if not record.get("phenotypingDataAvailable"):
        bundle = _not_covered(gene_symbol, mgi, statuses or "no phenotyping data available")
        bundle.source_link = source_link
        return bundle

    phenotypes = _mp_terms(record.get("significantMpTerms"))
    significant = [s for s in (record.get("significantPhenotypeSystem") or []) if s]
    not_significant = [s for s in (record.get("nonSignificantPhenotypeSystem") or []) if s]
    viability = _derive_viability(phenotypes, not_significant)

    if phenotypes:
        top_terms = ", ".join(dict.fromkeys(p.mp_term_name for p in phenotypes[:5]))
        text = (
            f"IMPC knockout-mouse phenotype for {gene_symbol}: viability={viability}; "
            f"{len(phenotypes)} significant phenotype call(s) in "
            f"{len(significant)} organ system(s); top terms: {top_terms}."
        )
    else:
        scope = f" in {len(not_significant)} organ system(s) screened" if not_significant else ""
        text = (
            f"IMPC phenotyped {gene_symbol} and reports no significant knockout phenotype"
            f"{scope}. This is a tested negative, limited to the screens run."
        )

    return ImpcBundle(
        gene_symbol=gene_symbol,
        coverage=COVERED,
        coverage_note=statuses,
        mgi_id=mgi,
        viability=viability,
        phenotypes=phenotypes,
        total=len(phenotypes),
        significant_systems=significant,
        tested_not_significant_systems=not_significant,
        source_link=source_link,
        text=text,
    )
