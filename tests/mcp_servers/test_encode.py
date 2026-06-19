# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ENCODE region-search regulatory-coverage MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.encode.tools import RegulatoryCoverageBundle, get_regulatory_coverage

_REGION_SEARCH_URL = "https://www.encodeproject.org/region-search/"

_COVERAGE_RESPONSE = {
    "total": 1452,
    "coordinates": "chr11:101449470-101586007",
    "coordinates_msg": "TRPC6: (chr11:101451470-101584007) +/- 2kb",
    "facets": [
        {
            "field": "assay_term_name",
            "terms": [
                {"key": "DNase-seq", "doc_count": 641},
                {"key": "ChIP-seq", "doc_count": 542},
                {"key": "ATAC-seq", "doc_count": 269},
                {"key": "RNA-seq", "doc_count": 0},
            ],
        },
        {
            "field": "target.label",
            "terms": [
                {"key": "CTCF", "doc_count": 278},
                {"key": "RAD21", "doc_count": 31},
            ],
        },
        {
            "field": "biosample_ontology.organ_slims",
            "terms": [
                {"key": "epithelium", "doc_count": 234},
                {"key": "lung", "doc_count": 156},
            ],
        },
    ],
}

_NO_ANNOTATIONS_RESPONSE = {
    "facets": [],
    "notification": "No annotations found",
}


@respx.mock
async def test_get_regulatory_coverage_returns_facets() -> None:
    respx.get(_REGION_SEARCH_URL).mock(return_value=httpx.Response(200, json=_COVERAGE_RESPONSE))

    bundle = await get_regulatory_coverage("TRPC6")

    assert isinstance(bundle, RegulatoryCoverageBundle)
    assert bundle.total_experiments == 1452
    assert bundle.top_assays[0].key == "DNase-seq"
    assert bundle.top_assays[0].experiment_count == 641
    assert all(a.experiment_count > 0 for a in bundle.top_assays)
    assert bundle.top_targets[0].key == "CTCF"
    assert "TRPC6" in bundle.text
    assert "CTCF" in bundle.text


@respx.mock
async def test_get_regulatory_coverage_no_gene_match_returns_empty_bundle() -> None:
    respx.get(_REGION_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_NO_ANNOTATIONS_RESPONSE)
    )

    bundle = await get_regulatory_coverage("NOTAGENE")

    assert bundle.total_experiments == 0
    assert bundle.top_assays == []
    assert "no regulatory-assay coverage found" in bundle.text
    assert "No annotations found" in bundle.text


@respx.mock
async def test_get_regulatory_coverage_raises_on_server_error() -> None:
    respx.get(_REGION_SEARCH_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(MCPToolError, match="ENCODE region-search API"):
        await get_regulatory_coverage("TRPC6")


@respx.mock
async def test_get_regulatory_coverage_passes_region_and_genome_params() -> None:
    route = respx.get(_REGION_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_COVERAGE_RESPONSE)
    )

    await get_regulatory_coverage("TRPC6", genome="GRCh38")

    request = route.calls.last.request
    assert request.url.params["region"] == "TRPC6"
    assert request.url.params["genome"] == "GRCh38"
    assert request.headers["accept"] == "application/json"
