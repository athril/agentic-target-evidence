# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for GTEx + HPA MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.gtex.tools import ExpressionBundle, get_expression

_GTEX_GENE_URL = "https://gtexportal.org/api/v2/reference/gene"
_GTEX_URL = "https://gtexportal.org/api/v2/expression/medianGeneExpression"
_HPA_SEARCH_URL = "https://www.proteinatlas.org/api/search_download.php"
_UNIPROT_URL = "https://rest.uniprot.org/uniprotkb/P12345.json"

_GTEX_GENE_RESPONSE = {"data": [{"gencodeId": "ENSG00000012048.20"}]}

_GTEX_RESPONSE = {
    "data": [
        {"tissueSiteDetailId": "Liver", "median": 42.3},
        {"tissueSiteDetailId": "Brain_Frontal_Cortex_BA9", "median": 1.2},
        {"tissueSiteDetailId": "Breast_Mammary_Tissue", "median": 28.7},
    ]
}

# HPA search API returns a list; first entry is the exact gene match
_HPA_SEARCH_RESPONSE = [
    {
        "Gene": "BRCA1",
        "Ensembl": "ENSG00000012048",
        "Uniprot": ["P12345"],
        "RNA tissue specificity": "Tissue enhanced (breast)",
    }
]

_UNIPROT_RESPONSE = {
    "comments": [
        {
            "commentType": "SUBCELLULAR LOCATION",
            "subcellularLocations": [
                {"location": {"value": "Nucleus"}},
                {"location": {"value": "Cytoplasm"}},
            ],
        }
    ]
}


@respx.mock
async def test_get_expression_returns_bundle() -> None:
    respx.get(_GTEX_GENE_URL).mock(return_value=httpx.Response(200, json=_GTEX_GENE_RESPONSE))
    respx.get(_GTEX_URL).mock(return_value=httpx.Response(200, json=_GTEX_RESPONSE))
    respx.get(_HPA_SEARCH_URL).mock(return_value=httpx.Response(200, json=_HPA_SEARCH_RESPONSE))
    respx.get(_UNIPROT_URL).mock(return_value=httpx.Response(200, json=_UNIPROT_RESPONSE))

    bundle = await get_expression("BRCA1", "ENSG00000012048")

    assert isinstance(bundle, ExpressionBundle)
    assert bundle.gene_symbol == "BRCA1"
    assert len(bundle.gtex_expressions) == 3
    assert bundle.gtex_expressions[0].tissue == "Liver"
    assert bundle.gtex_expressions[0].median_tpm == pytest.approx(42.3)
    assert bundle.hpa_tissue_specificity == "Tissue enhanced (breast)"
    assert "Nucleus" in bundle.hpa_subcellular_location
    assert "Cytoplasm" in bundle.hpa_subcellular_location


@respx.mock
async def test_get_expression_gtex_404_returns_empty_expressions() -> None:
    respx.get(_GTEX_GENE_URL).mock(return_value=httpx.Response(200, json=_GTEX_GENE_RESPONSE))
    respx.get(_GTEX_URL).mock(return_value=httpx.Response(404))
    respx.get(_HPA_SEARCH_URL).mock(return_value=httpx.Response(200, json=_HPA_SEARCH_RESPONSE))
    respx.get(_UNIPROT_URL).mock(return_value=httpx.Response(200, json=_UNIPROT_RESPONSE))

    bundle = await get_expression("BRCA1", "ENSG00000012048")
    assert bundle.gtex_expressions == []
    assert bundle.hpa_tissue_specificity == "Tissue enhanced (breast)"


@respx.mock
async def test_get_expression_raises_on_gtex_server_error() -> None:
    respx.get(_GTEX_GENE_URL).mock(return_value=httpx.Response(200, json=_GTEX_GENE_RESPONSE))
    respx.get(_GTEX_URL).mock(return_value=httpx.Response(500))
    respx.get(_HPA_SEARCH_URL).mock(return_value=httpx.Response(200, json=_HPA_SEARCH_RESPONSE))
    respx.get(_UNIPROT_URL).mock(return_value=httpx.Response(200, json=_UNIPROT_RESPONSE))

    with pytest.raises(MCPToolError, match="GTEx API"):
        await get_expression("BRCA1", "ENSG00000012048")


@respx.mock
async def test_get_expression_hpa_404_returns_empty_hpa_fields() -> None:
    respx.get(_GTEX_GENE_URL).mock(return_value=httpx.Response(200, json=_GTEX_GENE_RESPONSE))
    respx.get(_GTEX_URL).mock(return_value=httpx.Response(200, json=_GTEX_RESPONSE))
    # HPA search returns no match for the gene
    respx.get(_HPA_SEARCH_URL).mock(return_value=httpx.Response(200, json=[]))

    bundle = await get_expression("BRCA1", "ENSG00000012048")
    assert bundle.hpa_tissue_specificity == ""
    assert bundle.hpa_subcellular_location == []


@respx.mock
async def test_get_expression_hpa_no_uniprot_skips_subcellular() -> None:
    """When HPA returns a match with no UniProt accession, subcellular is empty."""
    respx.get(_GTEX_GENE_URL).mock(return_value=httpx.Response(200, json=_GTEX_GENE_RESPONSE))
    respx.get(_GTEX_URL).mock(return_value=httpx.Response(200, json=_GTEX_RESPONSE))
    respx.get(_HPA_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "Gene": "BRCA1",
                    "Ensembl": "ENSG00000012048",
                    "Uniprot": [],
                    "RNA tissue specificity": "Tissue enhanced (breast)",
                }
            ],
        )
    )

    bundle = await get_expression("BRCA1", "ENSG00000012048")
    assert bundle.hpa_tissue_specificity == "Tissue enhanced (breast)"
    assert bundle.hpa_subcellular_location == []


@respx.mock
async def test_get_expression_resolves_renamed_gene_by_ensembl_id() -> None:
    """GTEx v8 knows SEPTIN9 only as SEPT9; the Ensembl ID must be tried first."""
    by_ensembl = respx.get(_GTEX_GENE_URL, params={"geneId": "ENSG00000184640"}).mock(
        return_value=httpx.Response(200, json={"data": [{"gencodeId": "ENSG00000184640.17"}]})
    )
    by_symbol = respx.get(_GTEX_GENE_URL, params={"geneId": "SEPTIN9"}).mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    expr = respx.get(_GTEX_URL, params={"gencodeId": "ENSG00000184640.17"}).mock(
        return_value=httpx.Response(200, json=_GTEX_RESPONSE)
    )
    respx.get(_HPA_SEARCH_URL).mock(return_value=httpx.Response(404))

    bundle = await get_expression("SEPTIN9", "ENSG00000184640.20")

    assert by_ensembl.called and expr.called and not by_symbol.called
    assert len(bundle.gtex_expressions) == 3
    assert bundle.text.startswith("GTEx top tissues")


@respx.mock
async def test_get_expression_unresolved_gene_reports_absence() -> None:
    respx.get(_GTEX_GENE_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    expr = respx.get(_GTEX_URL).mock(return_value=httpx.Response(200, json=_GTEX_RESPONSE))
    respx.get(_HPA_SEARCH_URL).mock(return_value=httpx.Response(404))

    bundle = await get_expression("NOTAGENE1")

    assert not expr.called  # never query expression with an unresolved id
    assert bundle.gtex_expressions == []
    assert "GTEx v8: no expression record for NOTAGENE1." in bundle.text
