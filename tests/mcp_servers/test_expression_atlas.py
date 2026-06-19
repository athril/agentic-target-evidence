# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for EBI Expression Atlas differential-expression MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.expression_atlas.tools import (
    DifferentialExpressionBundle,
    get_differential_expression,
)

_SEARCH_URL = "https://www.ebi.ac.uk/gxa/search"
_DIFFERENTIAL_URL = "https://www.ebi.ac.uk/gxa/json/search/differential_results"

_DIFFERENTIAL_RESPONSE = {
    "results": [
        {
            "experimentAccession": "E-MTAB-1",
            "experimentName": "Some kidney disease study",
            "comparison": "'FSGS kidney' vs 'control kidney'",
            "regulation": "UP",
            "foldChange": 3.2,
            "pValue": 1e-8,
            "factors": ["disease"],
        },
        {
            "experimentAccession": "E-MTAB-2",
            "experimentName": "Unrelated study",
            "comparison": "'treated' vs 'untreated'",
            "regulation": "DOWN",
            "foldChange": -1.5,
            "pValue": 1e-3,
            "factors": ["compound"],
        },
    ]
}


def _mock_redirect(ensembl_id: str = "ensg00000137672") -> None:
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(302, headers={"location": f"/gxa/genes/{ensembl_id}"})
    )


@respx.mock
async def test_get_differential_expression_disease_match() -> None:
    _mock_redirect()
    respx.get(_DIFFERENTIAL_URL).mock(return_value=httpx.Response(200, json=_DIFFERENTIAL_RESPONSE))

    bundle = await get_differential_expression("TRPC6", disease="FSGS")

    assert isinstance(bundle, DifferentialExpressionBundle)
    assert bundle.ensembl_id == "ensg00000137672"
    assert bundle.disease_specific is True
    assert len(bundle.results) == 2
    assert bundle.results[0].experiment_accession == "E-MTAB-1"
    assert "FSGS" in bundle.text


@respx.mock
async def test_get_differential_expression_no_disease_match_falls_back() -> None:
    _mock_redirect()
    route = respx.get(_DIFFERENTIAL_URL)
    route.side_effect = [
        httpx.Response(200, json={"results": []}),  # disease-filtered query: no hits
        httpx.Response(200, json=_DIFFERENTIAL_RESPONSE),  # unfiltered fallback
    ]

    bundle = await get_differential_expression("TRPC6", disease="glomerulosclerosis")

    assert bundle.disease_specific is False
    assert len(bundle.results) == 2
    assert "no differential expression data specific to 'glomerulosclerosis'" in bundle.text


@respx.mock
async def test_get_differential_expression_no_gene_match_returns_empty_bundle() -> None:
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200))  # no redirect

    bundle = await get_differential_expression("NOTAGENE")

    assert bundle.ensembl_id == ""
    assert bundle.results == []
    assert "no gene record found" in bundle.text


@respx.mock
async def test_get_differential_expression_raises_on_server_error() -> None:
    _mock_redirect()
    respx.get(_DIFFERENTIAL_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(MCPToolError, match="Expression Atlas API"):
        await get_differential_expression("TRPC6")


@respx.mock
async def test_get_differential_expression_no_disease_given_no_disease_clause() -> None:
    _mock_redirect()
    respx.get(_DIFFERENTIAL_URL).mock(return_value=httpx.Response(200, json=_DIFFERENTIAL_RESPONSE))

    bundle = await get_differential_expression("TRPC6")

    assert bundle.disease_specific is False
    assert "specific to" not in bundle.text
