# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for DGIdb (Drug Gene Interaction Database) MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.dgidb.tools import (
    CategoryBundle,
    InteractionBundle,
    get_gene_categories,
    get_gene_drug_interactions,
)

_DGIDB_URL = "https://dgidb.org/api/graphql"

_INTERACTIONS_RESPONSE = {
    "data": {
        "genes": {
            "nodes": [
                {
                    "name": "EGFR",
                    "conceptId": "hgnc:3236",
                    "interactions": [
                        {
                            "drug": {"name": "ERLOTINIB", "conceptId": "chembl:CHEMBL553", "approved": True},
                            "interactionScore": 0.57,
                            "interactionTypes": [{"type": "inhibitor", "directionality": "INHIBITORY"}],
                            "publications": [{"pmid": 26137449}],
                            "sources": [{"sourceDbName": "FDA"}, {"sourceDbName": "PharmGKB"}],
                        },
                        {
                            "drug": {"name": "TGX-221", "conceptId": "iuphar.ligand:8244", "approved": False},
                            "interactionScore": 0.13,
                            "interactionTypes": [],
                            "publications": [],
                            "sources": [{"sourceDbName": "CKB-CORE"}],
                        },
                    ],
                }
            ]
        }
    }
}

_CATEGORIES_RESPONSE = {
    "data": {
        "genes": {
            "nodes": [
                {
                    "name": "BRAF",
                    "geneCategoriesWithSources": [
                        {"name": "DRUGGABLE GENOME", "sourceNames": ["HopkinsGroom", "RussLampel"]},
                        {"name": "KINASE", "sourceNames": ["Pharos", "dGene"]},
                    ],
                }
            ]
        }
    }
}


@respx.mock
async def test_get_gene_drug_interactions_returns_bundle() -> None:
    respx.post(_DGIDB_URL).mock(return_value=httpx.Response(200, json=_INTERACTIONS_RESPONSE))
    bundle = await get_gene_drug_interactions("EGFR")

    assert isinstance(bundle, InteractionBundle)
    assert bundle.gene_concept_id == "hgnc:3236"
    assert bundle.total_count == 2
    # Sorted by interaction_score descending
    assert bundle.interactions[0].drug_name == "ERLOTINIB"
    assert bundle.interactions[0].approved is True
    assert bundle.interactions[0].interaction_types == ["inhibitor"]
    assert bundle.interactions[0].directionality == "INHIBITORY"
    assert bundle.interactions[0].pmids == [26137449]
    assert "ERLOTINIB" in bundle.text


@respx.mock
async def test_get_gene_drug_interactions_approved_only_filters() -> None:
    respx.post(_DGIDB_URL).mock(return_value=httpx.Response(200, json=_INTERACTIONS_RESPONSE))
    bundle = await get_gene_drug_interactions("EGFR", approved_only=True)

    assert bundle.total_count == 1
    assert bundle.interactions[0].drug_name == "ERLOTINIB"


@respx.mock
async def test_get_gene_drug_interactions_respects_max_results() -> None:
    respx.post(_DGIDB_URL).mock(return_value=httpx.Response(200, json=_INTERACTIONS_RESPONSE))
    bundle = await get_gene_drug_interactions("EGFR", max_results=1)

    assert bundle.total_count == 2  # total reflects all matches
    assert len(bundle.interactions) == 1
    assert bundle.interactions[0].drug_name == "ERLOTINIB"


@respx.mock
async def test_get_gene_drug_interactions_unknown_gene_returns_empty() -> None:
    respx.post(_DGIDB_URL).mock(
        return_value=httpx.Response(200, json={"data": {"genes": {"nodes": []}}})
    )
    bundle = await get_gene_drug_interactions("NOTAREALGENE")

    assert bundle.total_count == 0
    assert bundle.interactions == []
    assert "No DGIdb gene record found" in bundle.text


@respx.mock
async def test_get_gene_drug_interactions_raises_on_graphql_error() -> None:
    respx.post(_DGIDB_URL).mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "bad query"}]})
    )
    with pytest.raises(MCPToolError, match="GraphQL error"):
        await get_gene_drug_interactions("EGFR")


@respx.mock
async def test_get_gene_categories_returns_bundle() -> None:
    respx.post(_DGIDB_URL).mock(return_value=httpx.Response(200, json=_CATEGORIES_RESPONSE))
    bundle = await get_gene_categories("BRAF")

    assert isinstance(bundle, CategoryBundle)
    assert bundle.is_druggable_genome is True
    assert {c.name for c in bundle.categories} == {"DRUGGABLE GENOME", "KINASE"}
    assert "DRUGGABLE GENOME" in bundle.text


@respx.mock
async def test_get_gene_categories_unknown_gene_returns_empty() -> None:
    respx.post(_DGIDB_URL).mock(
        return_value=httpx.Response(200, json={"data": {"genes": {"nodes": []}}})
    )
    bundle = await get_gene_categories("NOTAREALGENE")

    assert bundle.categories == []
    assert bundle.is_druggable_genome is False
    assert "No DGIdb gene record found" in bundle.text


@respx.mock
async def test_get_gene_categories_not_druggable() -> None:
    respx.post(_DGIDB_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "genes": {
                        "nodes": [
                            {
                                "name": "FOO",
                                "geneCategoriesWithSources": [
                                    {"name": "ENZYME", "sourceNames": ["GO"]},
                                ],
                            }
                        ]
                    }
                }
            },
        )
    )
    bundle = await get_gene_categories("FOO")

    assert bundle.is_druggable_genome is False
    assert "Druggable genome: no" in bundle.text
