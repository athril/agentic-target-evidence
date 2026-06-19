# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Ontology lookup tools — HGNC gene symbol canonicalization and MONDO disease
cross-referencing, both via public, keyless REST APIs.

Open Targets' own fuzzy search (mcp_servers.opentargets.tools.resolve_gene /
resolve_disease) already covers the common case of resolving a clean gene
symbol or disease name to an Ensembl/EFO id. These tools fill the gaps that
search can miss: HGNC catches previous/alias gene symbols (e.g. a withdrawn
or colloquial symbol), and MONDO provides cross-references (OMIM/DOID/MeSH/
ICD-10) so the same disease mentioned under different vocabularies can later
be recognised as one entity.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx
from pydantic import BaseModel

from core.exceptions import MCPToolError
from core.http import get_with_retry

_HGNC_BASE = "https://rest.genenames.org"
_HGNC_HEADERS = {"Accept": "application/json"}
_OLS_BASE = "https://www.ebi.ac.uk/ols4/api"
_MONARCH_BASE = "https://api.monarchinitiative.org/v3/api"

# Bounded HPO "Mode of inheritance" vocabulary (children of HP:0000005). ClinGen's
# bulk dataset and Monarch's phenotype annotations both use these same curies, so
# one lookup table serves as both the ClinGen MOI parser and the Monarch
# phenotype-vs-inheritance-term filter.
HPO_INHERITANCE_LABELS: dict[str, str] = {
    "HP:0000005": "Unspecified",
    "HP:0000006": "Autosomal dominant",
    "HP:0000007": "Autosomal recessive",
    "HP:0001417": "X-linked",
    "HP:0001419": "X-linked recessive",
    "HP:0001423": "X-linked dominant",
    "HP:0001427": "Mitochondrial",
    "HP:0001428": "Somatic mutation",
    "HP:0032113": "Semidominant",
    "HP:0010985": "Gonosomal",
    "HP:0001450": "Y-linked",
}


class HGNCResult(BaseModel):
    symbol: str
    hgnc_id: str = ""
    ensembl_gene_id: str = ""
    aliases: list[str] = []
    previous_symbols: list[str] = []


class MondoResult(BaseModel):
    mondo_id: str
    label: str
    xrefs: dict[str, str] = {}


class GenePhenotypeBundle(BaseModel):
    gene_symbol: str
    hgnc_id: str | None = None
    phenotype_count: int = 0
    top_phenotypes: list[str] = []
    inheritance_modes: list[str] = []
    specificity_band: str = "unknown"
    text: str = ""


async def _hgnc_fetch_by_symbol(client: httpx.AsyncClient, symbol: str) -> dict | None:
    resp = await get_with_retry(
        client, f"{_HGNC_BASE}/fetch/symbol/{symbol}", headers=_HGNC_HEADERS
    )
    if resp.status_code != 200:
        raise MCPToolError(f"HGNC API returned HTTP {resp.status_code}")
    docs = (resp.json().get("response") or {}).get("docs") or []
    return docs[0] if docs else None


async def resolve_hgnc_symbol(symbol: str) -> HGNCResult:
    """Resolve a gene symbol to its canonical HGNC record.

    Falls back to an alias/previous-symbol search (then re-fetches the
    canonical record) when ``symbol`` is not itself a current HGNC symbol.
    Raises MCPToolError if nothing is found.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            doc = await _hgnc_fetch_by_symbol(client, symbol)
            if doc is None:
                for field in ("alias_symbol", "prev_symbol"):
                    resp = await get_with_retry(
                        client, f"{_HGNC_BASE}/search/{field}/{symbol}", headers=_HGNC_HEADERS
                    )
                    if resp.status_code != 200:
                        continue
                    hits = (resp.json().get("response") or {}).get("docs") or []
                    if hits:
                        doc = await _hgnc_fetch_by_symbol(client, hits[0]["symbol"])
                        break
        except MCPToolError:
            raise
        except Exception as exc:
            raise MCPToolError(f"HGNC API request failed: {exc}") from exc

    if doc is None:
        raise MCPToolError(f"No HGNC record found for symbol '{symbol}'")

    return HGNCResult(
        symbol=doc.get("symbol", symbol),
        hgnc_id=doc.get("hgnc_id", ""),
        ensembl_gene_id=doc.get("ensembl_gene_id", ""),
        aliases=doc.get("alias_symbol") or [],
        previous_symbols=doc.get("prev_symbol") or [],
    )


async def resolve_mondo_term(name_or_id: str) -> MondoResult:
    """Resolve a free-text disease name (or an existing EFO/OMIM/DOID id) to its
    MONDO term, plus cross-references to other disease vocabularies.

    Raises MCPToolError if no MONDO term matches.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await get_with_retry(
                client,
                f"{_OLS_BASE}/search",
                params={"q": name_or_id, "ontology": "mondo", "rows": 1},
            )
        except Exception as exc:
            raise MCPToolError(f"OLS API request failed: {exc}") from exc

        if resp.status_code != 200:
            raise MCPToolError(f"OLS API returned HTTP {resp.status_code}")
        docs = (resp.json().get("response") or {}).get("docs") or []
        if not docs:
            raise MCPToolError(f"No MONDO term found for '{name_or_id}'")
        hit = docs[0]
        short_form = hit.get("short_form", "")
        label = hit.get("label", name_or_id)
        iri = hit.get("iri", "")

        xrefs: dict[str, str] = {}
        if iri:
            double_encoded = quote(quote(iri, safe=""), safe="")
            try:
                term_resp = await get_with_retry(
                    client, f"{_OLS_BASE}/ontologies/mondo/terms/{double_encoded}"
                )
                if term_resp.status_code == 200:
                    for x in term_resp.json().get("obo_xref") or []:
                        db = str(x.get("database", "")).lower()
                        if db and x.get("id"):
                            xrefs[db] = x["id"]
            except Exception:
                pass  # xrefs are best-effort; the MONDO id itself is the primary result

    return MondoResult(mondo_id=short_form, label=label, xrefs=xrefs)


def _specificity_band(phenotype_count: int) -> str:
    """Coarse focal-vs-pleiotropic band over the true (non-inheritance) phenotype count."""
    if phenotype_count <= 5:
        return "focal"
    if phenotype_count <= 15:
        return "moderate"
    return "pleiotropic"


async def get_gene_phenotypes(gene_symbol: str) -> GenePhenotypeBundle:
    """Fetch HPO phenotype breadth/specificity for a gene from the Monarch API.

    Resolves the symbol to an HGNC id, then queries Monarch's gene entity
    endpoint for its aggregated phenotype annotations. Monarch's
    ``has_phenotype``/``has_phenotype_label`` lists mix true phenotype terms
    with HPO "Mode of inheritance" terms (children of HP:0000005) — those are
    split out via HPO_INHERITANCE_LABELS rather than counted as phenotypes, and
    doubled as a fallback inheritance-mode source when ClinGen has no curation.

    Raises MCPToolError if the symbol can't be resolved; returns an empty
    bundle (not an error) if Monarch has no phenotype annotations for the gene.
    """
    try:
        hgnc = await resolve_hgnc_symbol(gene_symbol)
        hgnc_id = hgnc.hgnc_id or None
    except MCPToolError:
        hgnc_id = None

    if not hgnc_id:
        return GenePhenotypeBundle(
            gene_symbol=gene_symbol,
            text=f"No HPO phenotype data found for {gene_symbol} (gene symbol not resolved).",
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await get_with_retry(client, f"{_MONARCH_BASE}/entity/{hgnc_id}")
        except Exception as exc:
            raise MCPToolError(f"Monarch API request failed: {exc}") from exc

    if resp.status_code != 200:
        return GenePhenotypeBundle(
            gene_symbol=gene_symbol,
            hgnc_id=hgnc_id,
            text=f"No HPO phenotype data found for {gene_symbol} (Monarch returned HTTP {resp.status_code}).",
        )

    body = resp.json()
    curies = body.get("has_phenotype") or []
    labels = body.get("has_phenotype_label") or []

    phenotypes: list[str] = []
    inheritance_modes: list[str] = []
    for curie, label in zip(curies, labels, strict=False):
        if curie in HPO_INHERITANCE_LABELS:
            inheritance_modes.append(HPO_INHERITANCE_LABELS[curie])
        else:
            phenotypes.append(label)

    phenotype_count = len(phenotypes)
    top_phenotypes = phenotypes[:5]
    band = _specificity_band(phenotype_count)

    if phenotype_count == 0:
        text = f"No HPO phenotype data found for {gene_symbol}."
    else:
        text = (
            f"HPO phenotype profile for {gene_symbol}: {phenotype_count} annotated phenotype(s) "
            f"({band}); top terms: {', '.join(top_phenotypes)}"
            + (f" [+{phenotype_count - 5} more]" if phenotype_count > 5 else "")
            + "."
        )
        if inheritance_modes:
            text += (
                f" Reported inheritance (HPO/Monarch): {', '.join(sorted(set(inheritance_modes)))}."
            )

    return GenePhenotypeBundle(
        gene_symbol=gene_symbol,
        hgnc_id=hgnc_id,
        phenotype_count=phenotype_count,
        top_phenotypes=top_phenotypes,
        inheritance_modes=sorted(set(inheritance_modes)),
        specificity_band=band,
        text=text,
    )
