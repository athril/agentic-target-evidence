# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for GWAS Catalog MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.gwas_catalog.tools import GWASBundle, GWASHit, get_gwas_associations

_BASE = "https://www.ebi.ac.uk/gwas/rest/api"

_SNP_PAGE = {
    "_embedded": {
        "singleNucleotidePolymorphisms": [
            {"rsId": "rs123456"},
            {"rsId": "rs654321"},
        ]
    },
    "page": {"size": 200, "totalElements": 2, "totalPages": 1, "number": 0},
}

_ASSOC_PAGE_RS123456 = {
    "_embedded": {
        "associations": [
            {
                "pvalue": 1e-10,
                "pvalueMantissa": 1,
                "pvalueExponent": -10,
                "betaNum": 0.05,
                "betaUnit": "SD",
                "betaDirection": "increase",
                "orPerCopyNum": None,
                "riskFrequency": "0.35",
                "standardError": 0.004,
                "study": {
                    "accessionId": "GCST001234",
                    "initialSampleSize": "10,000 European",
                    "diseaseTrait": {"trait": "lung cancer"},
                    "publicationInfo": {
                        "pubmedId": "12345678",
                        "publicationDate": "2020-01-15",
                        "publication": "Nat Genet",
                        "title": "GWAS of lung cancer risk.",
                    },
                },
                "efoTraits": [
                    {
                        "trait": "lung carcinoma",
                        "uri": "http://www.ebi.ac.uk/efo/EFO_0001071",
                        "shortForm": "EFO_0001071",
                    }
                ],
                "backgroundEfoTraits": [],
                "_links": {
                    "self": {"href": f"{_BASE}/associations/9001"},
                    "association": {
                        "href": f"{_BASE}/associations/9001{{?projection}}",
                        "templated": True,
                    },
                },
            }
        ]
    },
    "page": {"size": 100, "totalElements": 1, "totalPages": 1, "number": 0},
}

_ASSOC_PAGE_EMPTY = {
    "_embedded": {"associations": []},
    "page": {"size": 100, "totalElements": 0, "totalPages": 1, "number": 0},
}


@respx.mock
async def test_get_gwas_associations_returns_bundle() -> None:
    respx.get(
        f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene",
        params={"geneName": "PRMT5", "size": "200", "page": "0"},
    ).mock(return_value=httpx.Response(200, json=_SNP_PAGE))
    respx.get(
        f"{_BASE}/singleNucleotidePolymorphisms/rs123456/associations",
    ).mock(return_value=httpx.Response(200, json=_ASSOC_PAGE_RS123456))
    respx.get(
        f"{_BASE}/singleNucleotidePolymorphisms/rs654321/associations",
    ).mock(return_value=httpx.Response(200, json=_ASSOC_PAGE_EMPTY))

    bundle = await get_gwas_associations("PRMT5")

    assert isinstance(bundle, GWASBundle)
    assert bundle.gene_symbol == "PRMT5"
    assert len(bundle.hits) == 1
    hit = bundle.hits[0]
    assert isinstance(hit, GWASHit)
    assert hit.rs_id == "rs123456"
    assert hit.pvalue == pytest.approx(1e-10)
    assert hit.trait == "lung cancer"
    assert hit.study_accession == "GCST001234"
    assert hit.pmid == "12345678"
    assert hit.efo_id == "EFO_0001071"
    assert "PRMT5" in bundle.source_link
    assert "1" in bundle.text  # count of hits


@respx.mock
async def test_get_gwas_associations_no_hits() -> None:
    respx.get(
        f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene",
    ).mock(return_value=httpx.Response(200, json=_SNP_PAGE))
    respx.get(
        f"{_BASE}/singleNucleotidePolymorphisms/rs123456/associations",
    ).mock(return_value=httpx.Response(200, json=_ASSOC_PAGE_EMPTY))
    respx.get(
        f"{_BASE}/singleNucleotidePolymorphisms/rs654321/associations",
    ).mock(return_value=httpx.Response(200, json=_ASSOC_PAGE_EMPTY))

    bundle = await get_gwas_associations("PRMT5")

    assert bundle.hits == []
    assert "No genome-wide significant" in bundle.text


@respx.mock
async def test_get_gwas_associations_raises_on_snp_http_error() -> None:
    respx.get(
        f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene",
    ).mock(return_value=httpx.Response(503))

    with pytest.raises(MCPToolError, match="HTTP 503"):
        await get_gwas_associations("PRMT5")


@respx.mock
async def test_get_gwas_associations_filters_by_p_threshold() -> None:
    """Associations above threshold must be excluded."""
    above_threshold = {
        "_embedded": {
            "associations": [
                {
                    "pvalue": 1e-5,  # above 5e-8 threshold
                    "pvalueMantissa": 1,
                    "pvalueExponent": -5,
                    "study": {
                        "accessionId": "GCST999",
                        "diseaseTrait": {"trait": "foo"},
                        "publicationInfo": {},
                    },
                    "efoTraits": [],
                    "_links": {"self": {"href": f"{_BASE}/associations/8000"}},
                }
            ]
        },
        "page": {"size": 100, "totalElements": 1, "totalPages": 1, "number": 0},
    }
    respx.get(
        f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene",
        params={"geneName": "BRCA1", "size": "200", "page": "0"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "_embedded": {"singleNucleotidePolymorphisms": [{"rsId": "rs99999"}]},
                "page": {"size": 200, "totalElements": 1, "totalPages": 1, "number": 0},
            },
        )
    )
    respx.get(
        f"{_BASE}/singleNucleotidePolymorphisms/rs99999/associations",
    ).mock(return_value=httpx.Response(200, json=above_threshold))

    bundle = await get_gwas_associations("BRCA1", p_threshold=5e-8)
    assert bundle.hits == []


@respx.mock
async def test_get_gwas_associations_deduplicates() -> None:
    """Same association_id appearing via two SNPs must appear only once."""
    snp_page = {
        "_embedded": {"singleNucleotidePolymorphisms": [{"rsId": "rs111"}, {"rsId": "rs222"}]},
        "page": {"size": 200, "totalElements": 2, "totalPages": 1, "number": 0},
    }
    same_assoc = {
        "_embedded": {
            "associations": [
                {
                    "pvalue": 1e-9,
                    "pvalueMantissa": 1,
                    "pvalueExponent": -9,
                    "study": {
                        "accessionId": "GCST777",
                        "diseaseTrait": {"trait": "trait X"},
                        "publicationInfo": {},
                    },
                    "efoTraits": [],
                    "_links": {"self": {"href": f"{_BASE}/associations/7777"}},
                }
            ]
        },
        "page": {"size": 100, "totalElements": 1, "totalPages": 1, "number": 0},
    }
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene").mock(
        return_value=httpx.Response(200, json=snp_page)
    )
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/rs111/associations").mock(
        return_value=httpx.Response(200, json=same_assoc)
    )
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/rs222/associations").mock(
        return_value=httpx.Response(200, json=same_assoc)
    )

    bundle = await get_gwas_associations("GENE1")
    assert len(bundle.hits) == 1


# ---------------------------------------------------------------------------
# EFO disease-scope filtering tests
# ---------------------------------------------------------------------------


def _make_snp_page(rs_ids: list[str]) -> dict:
    return {
        "_embedded": {"singleNucleotidePolymorphisms": [{"rsId": r} for r in rs_ids]},
        "page": {"size": 200, "totalElements": len(rs_ids), "totalPages": 1, "number": 0},
    }


def _make_assoc_page(
    assoc_id: str,
    trait: str,
    efo_short: str,
    pvalue: float = 1e-9,
) -> dict:
    return {
        "_embedded": {
            "associations": [
                {
                    "pvalue": pvalue,
                    "pvalueMantissa": 1,
                    "pvalueExponent": -9,
                    "study": {
                        "accessionId": f"GCST{assoc_id}",
                        "diseaseTrait": {"trait": trait},
                        "publicationInfo": {},
                    },
                    "efoTraits": [
                        {
                            "trait": trait,
                            "uri": f"http://www.ebi.ac.uk/efo/{efo_short}",
                            "shortForm": efo_short,
                        }
                    ],
                    "_links": {"self": {"href": f"{_BASE}/associations/{assoc_id}"}},
                }
            ]
        },
        "page": {"size": 100, "totalElements": 1, "totalPages": 1, "number": 0},
    }


@respx.mock
async def test_efo_scope_keeps_matching_hits() -> None:
    """Hits whose efo_id is in the efo_ids set must be kept."""
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene").mock(
        return_value=httpx.Response(200, json=_make_snp_page(["rs_cancer", "rs_height"]))
    )
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/rs_cancer/associations").mock(
        return_value=httpx.Response(
            200, json=_make_assoc_page("001", "pancreatic cancer", "EFO_0003860")
        )
    )
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/rs_height/associations").mock(
        return_value=httpx.Response(200, json=_make_assoc_page("002", "height", "EFO_0004339"))
    )

    bundle = await get_gwas_associations(
        "PRMT5",
        efo_ids={"EFO_0003860", "EFO_0002618"},
    )

    assert len(bundle.hits) == 1
    assert bundle.hits[0].efo_id == "EFO_0003860"
    assert bundle.dropped_off_target == 1
    assert "EFO_0004339" not in {h.efo_id for h in bundle.hits}
    assert "pancreatic cancer" in bundle.kept_traits
    assert "height" in bundle.all_traits
    assert "height" not in bundle.kept_traits


@respx.mock
async def test_efo_scope_drops_all_off_target() -> None:
    """When no hits match the EFO scope, all are dropped and the text explains why."""
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene").mock(
        return_value=httpx.Response(200, json=_make_snp_page(["rs_height"]))
    )
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/rs_height/associations").mock(
        return_value=httpx.Response(200, json=_make_assoc_page("003", "height", "EFO_0004339"))
    )

    bundle = await get_gwas_associations(
        "PRMT5",
        efo_ids={"EFO_0003860"},
    )

    assert bundle.hits == []
    assert bundle.dropped_off_target == 1
    assert "height" in bundle.all_traits
    assert bundle.kept_traits == []
    assert "off-indication" in bundle.text


@respx.mock
async def test_efo_scope_none_preserves_old_behavior() -> None:
    """When efo_ids=None and trait_terms=None, all hits are returned unchanged."""
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene").mock(
        return_value=httpx.Response(200, json=_make_snp_page(["rs_height"]))
    )
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/rs_height/associations").mock(
        return_value=httpx.Response(200, json=_make_assoc_page("004", "height", "EFO_0004339"))
    )

    bundle = await get_gwas_associations("GENE_X")

    assert len(bundle.hits) == 1
    assert bundle.dropped_off_target == 0


@respx.mock
async def test_trait_term_fallback_matches_substring() -> None:
    """When efo_id does not match but trait_terms substring does, hit is kept."""
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene").mock(
        return_value=httpx.Response(200, json=_make_snp_page(["rs_panc"]))
    )
    # efo_id is not in the provided set, but trait text contains "pancreatic"
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/rs_panc/associations").mock(
        return_value=httpx.Response(
            200, json=_make_assoc_page("005", "pancreatic adenocarcinoma", "MONDO_0005105")
        )
    )

    bundle = await get_gwas_associations(
        "PRMT5",
        efo_ids={"EFO_0003860"},  # not MONDO_0005105
        trait_terms=["pancreatic"],
    )

    assert len(bundle.hits) == 1
    assert bundle.dropped_off_target == 0


@respx.mock
async def test_max_hits_cap_applied_after_filter() -> None:
    """max_hits caps the number of retained hits after scoping."""
    # Two disease-matching hits
    snp_page = _make_snp_page(["rs_a", "rs_b"])
    assoc_a = _make_assoc_page("100", "pancreatic cancer", "EFO_0003860", pvalue=1e-10)
    assoc_b = _make_assoc_page("101", "pancreatic cancer", "EFO_0003860", pvalue=1e-9)
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/search/findByGene").mock(
        return_value=httpx.Response(200, json=snp_page)
    )
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/rs_a/associations").mock(
        return_value=httpx.Response(200, json=assoc_a)
    )
    respx.get(f"{_BASE}/singleNucleotidePolymorphisms/rs_b/associations").mock(
        return_value=httpx.Response(200, json=assoc_b)
    )

    bundle = await get_gwas_associations(
        "PRMT5",
        efo_ids={"EFO_0003860"},
        max_hits=1,
    )

    assert len(bundle.hits) == 1
    # Lead hit (lowest p-value) should be first
    assert bundle.hits[0].pvalue == pytest.approx(1e-10)
