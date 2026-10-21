# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for HGNC/MONDO ontology lookup MCP tools."""

from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.ontology.tools import (
    GenePhenotypeBundle,
    HGNCResult,
    MondoResult,
    get_gene_phenotypes,
    resolve_hgnc_symbol,
    resolve_mondo_term,
    xref_parents,
)

_HGNC_BASE = "https://rest.genenames.org"
_OLS_BASE = "https://www.ebi.ac.uk/ols4/api"
_MONARCH_BASE = "https://api.monarchinitiative.org/v3/api"


# ---------------------------------------------------------------------------
# resolve_hgnc_symbol
# ---------------------------------------------------------------------------


@respx.mock
async def test_resolve_hgnc_symbol_returns_canonical_record() -> None:
    respx.get(f"{_HGNC_BASE}/fetch/symbol/PRMT5").mock(
        return_value=httpx.Response(
            200,
            json={
                "response": {
                    "docs": [
                        {
                            "symbol": "PRMT5",
                            "hgnc_id": "HGNC:17353",
                            "ensembl_gene_id": "ENSG00000100462",
                            "alias_symbol": ["SKB1", "IBP72"],
                            "prev_symbol": [],
                        }
                    ]
                }
            },
        )
    )
    result = await resolve_hgnc_symbol("PRMT5")

    assert isinstance(result, HGNCResult)
    assert result.symbol == "PRMT5"
    assert result.ensembl_gene_id == "ENSG00000100462"
    assert result.aliases == ["SKB1", "IBP72"]


@respx.mock
async def test_resolve_hgnc_symbol_falls_back_to_alias_search() -> None:
    respx.get(f"{_HGNC_BASE}/fetch/symbol/SKB1").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    respx.get(f"{_HGNC_BASE}/search/alias_symbol/SKB1").mock(
        return_value=httpx.Response(200, json={"response": {"docs": [{"symbol": "PRMT5"}]}})
    )
    respx.get(f"{_HGNC_BASE}/fetch/symbol/PRMT5").mock(
        return_value=httpx.Response(
            200,
            json={
                "response": {
                    "docs": [
                        {
                            "symbol": "PRMT5",
                            "hgnc_id": "HGNC:17353",
                            "ensembl_gene_id": "ENSG00000100462",
                            "alias_symbol": ["SKB1"],
                        }
                    ]
                }
            },
        )
    )
    result = await resolve_hgnc_symbol("SKB1")

    assert result.symbol == "PRMT5"
    assert result.ensembl_gene_id == "ENSG00000100462"


@respx.mock
async def test_resolve_hgnc_symbol_raises_when_no_match() -> None:
    respx.get(f"{_HGNC_BASE}/fetch/symbol/NOTAGENE").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    respx.get(f"{_HGNC_BASE}/search/alias_symbol/NOTAGENE").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    respx.get(f"{_HGNC_BASE}/search/prev_symbol/NOTAGENE").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    with pytest.raises(MCPToolError, match="No HGNC record"):
        await resolve_hgnc_symbol("NOTAGENE")


@respx.mock
async def test_resolve_hgnc_symbol_raises_on_http_error() -> None:
    respx.get(f"{_HGNC_BASE}/fetch/symbol/PRMT5").mock(return_value=httpx.Response(503))
    with pytest.raises(MCPToolError, match="HTTP 503"):
        await resolve_hgnc_symbol("PRMT5")


# ---------------------------------------------------------------------------
# resolve_mondo_term
# ---------------------------------------------------------------------------


@respx.mock
async def test_resolve_mondo_term_returns_term_with_xrefs() -> None:
    iri = "http://purl.obolibrary.org/obo/MONDO_0008170"
    respx.get(f"{_OLS_BASE}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "response": {
                    "docs": [
                        {
                            "short_form": "MONDO:0008170",
                            "label": "pancreatic cancer",
                            "iri": iri,
                        }
                    ]
                }
            },
        )
    )
    respx.get(url__startswith=f"{_OLS_BASE}/ontologies/mondo/terms/").mock(
        return_value=httpx.Response(
            200,
            json={
                "obo_xref": [
                    {"database": "EFO", "id": "EFO_0002618"},
                    {"database": "OMIM", "id": "260350"},
                    {"database": "OMIM", "id": "614320"},
                ]
            },
        )
    )
    result = await resolve_mondo_term("pancreatic cancer")

    assert isinstance(result, MondoResult)
    assert result.mondo_id == "MONDO:0008170"
    assert result.label == "pancreatic cancer"
    # Every cross-reference is kept; a second OMIM id no longer overwrites the first.
    assert result.xrefs == {"efo": ["EFO_0002618"], "omim": ["260350", "614320"]}


@respx.mock
async def test_resolve_mondo_term_xrefs_best_effort_on_term_lookup_failure() -> None:
    """Search succeeds but the term-detail fetch fails — mondo_id/label still returned, xrefs empty."""
    iri = "http://purl.obolibrary.org/obo/MONDO_0008170"
    respx.get(f"{_OLS_BASE}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "response": {
                    "docs": [
                        {
                            "short_form": "MONDO:0008170",
                            "label": "pancreatic cancer",
                            "iri": iri,
                        }
                    ]
                }
            },
        )
    )
    respx.get(url__startswith=f"{_OLS_BASE}/ontologies/mondo/terms/").mock(
        return_value=httpx.Response(500)
    )
    result = await resolve_mondo_term("pancreatic cancer")

    assert result.mondo_id == "MONDO:0008170"
    assert result.xrefs == {}


@respx.mock
async def test_resolve_mondo_term_raises_when_no_match() -> None:
    respx.get(f"{_OLS_BASE}/search").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    with pytest.raises(MCPToolError, match="No MONDO term found"):
        await resolve_mondo_term("not a real disease")


@respx.mock
async def test_resolve_mondo_term_raises_on_http_error() -> None:
    respx.get(f"{_OLS_BASE}/search").mock(return_value=httpx.Response(503))
    with pytest.raises(MCPToolError, match="HTTP 503"):
        await resolve_mondo_term("pancreatic cancer")


# ---------------------------------------------------------------------------
# get_gene_phenotypes
# ---------------------------------------------------------------------------


def _mock_hgnc(symbol: str, hgnc_id: str) -> None:
    respx.get(f"{_HGNC_BASE}/fetch/symbol/{symbol}").mock(
        return_value=httpx.Response(
            200, json={"response": {"docs": [{"symbol": symbol, "hgnc_id": hgnc_id}]}}
        )
    )


@respx.mock
async def test_get_gene_phenotypes_splits_inheritance_terms_from_phenotypes() -> None:
    """Monarch mixes 'Autosomal dominant inheritance' into has_phenotype_label —
    it must be reported as inheritance_modes, not counted as a phenotype."""
    _mock_hgnc("TRPC6", "HGNC:12338")
    respx.get(f"{_MONARCH_BASE}/entity/HGNC:12338").mock(
        return_value=httpx.Response(
            200,
            json={
                "has_phenotype": [
                    "HP:0000822",
                    "HP:0000006",
                    "HP:0000100",
                    "HP:0000097",
                    "HP:0012622",
                ],
                "has_phenotype_label": [
                    "Hypertension",
                    "Autosomal dominant inheritance",
                    "Nephrotic syndrome",
                    "Proteinuria",
                    "Focal segmental glomerulosclerosis",
                ],
                "has_phenotype_count": 5,
            },
        )
    )
    bundle = await get_gene_phenotypes("TRPC6")

    assert isinstance(bundle, GenePhenotypeBundle)
    assert bundle.phenotype_count == 4
    assert "Autosomal dominant inheritance" not in bundle.top_phenotypes
    assert bundle.inheritance_modes == ["Autosomal dominant"]
    assert bundle.specificity_band == "focal"
    assert "TRPC6" in bundle.text
    assert "Autosomal dominant" in bundle.text


@respx.mock
async def test_get_gene_phenotypes_bands_pleiotropic_above_fifteen() -> None:
    _mock_hgnc("GENE1", "HGNC:1")
    labels = [f"Phenotype {i}" for i in range(20)]
    respx.get(f"{_MONARCH_BASE}/entity/HGNC:1").mock(
        return_value=httpx.Response(
            200,
            json={
                "has_phenotype": [f"HP:9{i:05d}" for i in range(20)],
                "has_phenotype_label": labels,
                "has_phenotype_count": 20,
            },
        )
    )
    bundle = await get_gene_phenotypes("GENE1")

    assert bundle.phenotype_count == 20
    assert bundle.specificity_band == "pleiotropic"
    assert len(bundle.top_phenotypes) == 5


@respx.mock
async def test_get_gene_phenotypes_empty_when_symbol_not_resolvable() -> None:
    respx.get(f"{_HGNC_BASE}/fetch/symbol/UNKNOWN").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    respx.get(f"{_HGNC_BASE}/search/alias_symbol/UNKNOWN").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    respx.get(f"{_HGNC_BASE}/search/prev_symbol/UNKNOWN").mock(
        return_value=httpx.Response(200, json={"response": {"docs": []}})
    )
    bundle = await get_gene_phenotypes("UNKNOWN")

    assert bundle.phenotype_count == 0
    assert bundle.specificity_band == "unknown"


@respx.mock
async def test_get_gene_phenotypes_empty_when_monarch_returns_404() -> None:
    _mock_hgnc("GENE1", "HGNC:1")
    respx.get(f"{_MONARCH_BASE}/entity/HGNC:1").mock(return_value=httpx.Response(404))
    bundle = await get_gene_phenotypes("GENE1")

    assert bundle.phenotype_count == 0
    assert "No HPO phenotype data" in bundle.text


# ---------------------------------------------------------------------------
# xref_parents
# ---------------------------------------------------------------------------

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def _term(obo_id: str, label: str, *, xrefs=(), synonyms=(), obsolete=False, iri=""):
    # OLS returns the parents link over http; the tool must build its own https URL.
    links = {"hierarchicalParents": {"href": f"http://www.ebi.ac.uk/ols4/x/{obo_id}"}}
    return {
        "obo_id": obo_id,
        **({"iri": iri} if iri else {}),
        "label": label,
        "obo_xref": [{"database": db, "id": i} for db, i in xrefs],
        "synonyms": list(synonyms),
        "is_obsolete": obsolete,
        "_links": links,
    }


def _parents_url(ontology: str, iri: str) -> str:
    iri2 = quote(quote(iri, safe=""), safe="")
    return f"{_OLS_BASE}/ontologies/{ontology}/terms/{iri2}/hierarchicalParents"


def _terms(*terms: dict) -> dict:
    return {"_embedded": {"terms": list(terms)}}


def _mock_term(ontology: str, obo_id: str, body: dict) -> None:
    respx.get(f"{_OLS_BASE}/ontologies/{ontology}/terms", params={"obo_id": obo_id}).mock(
        return_value=httpx.Response(200, json=body)
    )


@pytest.fixture()
def _no_ncbi_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_servers.pubmed import tools as pubmed_tools

    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(pubmed_tools, "_backoff_sleep", _no_sleep)


_DOID10_IRI = "http://purl.obolibrary.org/obo/DOID_10"
_NCIT20_IRI = "http://purl.obolibrary.org/obo/NCIT_C20"


@respx.mock
async def test_xref_parents_reads_each_allowed_ontology_and_skips_the_rest(_no_ncbi_backoff):
    http_link = respx.get(url__startswith="http://www.ebi.ac.uk/").mock(
        return_value=httpx.Response(500)
    )
    _mock_term(
        "mondo",
        "MONDO:1",
        _terms(
            _term(
                "MONDO:1",
                "disease one",
                xrefs=[
                    ("DOID", "10"),
                    ("NCIT", "C20"),
                    ("EFO", "30"),
                    ("MESH", "D000040"),
                    ("SCTID", "999"),  # not allowed
                    ("UMLS", "C999"),  # not allowed
                ],
            )
        ),
    )
    _mock_term(
        "doid",
        "DOID:10",
        _terms(_term("DOID:10", "disease one", iri=_DOID10_IRI)),
    )
    respx.get(_parents_url("doid", _DOID10_IRI)).mock(
        return_value=httpx.Response(
            200,
            json=_terms(_term("DOID:11", "group disease", synonyms=["group of diseases", "GD"])),
        )
    )
    _mock_term(
        "ncit",
        "NCIT:C20",
        _terms(_term("NCIT:C20", "Disease One", iri=_NCIT20_IRI)),
    )
    respx.get(_parents_url("ncit", _NCIT20_IRI)).mock(
        return_value=httpx.Response(200, json=_terms(_term("NCIT:C21", "Group Disease")))
    )
    _mock_term("efo", "EFO:30", _terms(_term("EFO:30", "obsolete one", obsolete=True)))
    respx.get(_EUTILS, params={"id": "68000040"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "68000040": {
                        "ds_idxlinks": [
                            {"parent": 68000041, "treenum": "C14.100.200"},
                            {
                                "parent": 68000042,
                                "treenum": "C23.550.100",
                            },  # a sign, not a disease group
                        ]
                    }
                }
            },
        )
    )
    respx.get(_EUTILS, params={"id": "68000041"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "68000041": {
                        "ds_meshui": "D000041",
                        "ds_meshterms": ["Disease Group", "Group, Disease"],
                    }
                }
            },
        )
    )

    parents = await xref_parents("MONDO_1")

    assert [(p.ontology, p.term_id, p.label) for p in parents] == [
        ("DOID", "DOID:11", "group disease"),
        ("NCIT", "NCIT:C21", "Group Disease"),
        ("MESH", "MESH:D000041", "Disease Group"),
    ]
    assert parents[0].exact_synonyms == ["group of diseases", "GD"]
    assert parents[2].exact_synonyms == ["Group, Disease"]
    assert not http_link.called


@respx.mock
async def test_xref_parents_skips_a_cross_referenced_term_without_an_iri():
    _mock_term("mondo", "MONDO:4", _terms(_term("MONDO:4", "disease four", xrefs=[("DOID", "40")])))
    _mock_term("doid", "DOID:40", _terms(_term("DOID:40", "disease four")))
    assert await xref_parents("MONDO_4") == []


@respx.mock
async def test_xref_parents_of_a_term_without_cross_references_is_empty():
    _mock_term("mondo", "MONDO:2", _terms(_term("MONDO:2", "disease two")))
    assert await xref_parents("MONDO_2") == []
    assert await xref_parents("HP_0000001") == []  # not a disease id this reads


@respx.mock
async def test_xref_parents_raises_when_ols_is_down():
    respx.get(f"{_OLS_BASE}/ontologies/mondo/terms").mock(return_value=httpx.Response(503))
    with pytest.raises(MCPToolError):
        await xref_parents("MONDO_3")
