# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for Open Targets MCP tools (MP-28)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_servers.opentargets.tools import (
    AssociationBundle,
    ColocBundle,
    DiseaseOntology,
    TractabilityBundle,
    _disease_ontology_cache,
    get_associations,
    get_colocalizations,
    get_disease_descendants,
    get_tractability,
)

_ASSOCIATIONS_RESPONSE = {
    "data": {
        "target": {
            "approvedSymbol": "BRCA1",
            "associatedDiseases": {
                "rows": [
                    {
                        "score": 0.87,
                        "datatypeScores": [
                            {"id": "genetic_association", "score": 0.9},
                            {"id": "literature", "score": 0.6},
                            {"id": "affected_pathway", "score": 0.3},
                            {"id": "animal_model", "score": 0.2},
                            {"id": "clinical", "score": 0.7},
                            {"id": "somatic_mutation", "score": 0.5},
                        ],
                    }
                ]
            },
        }
    }
}

_TRACTABILITY_RESPONSE = {
    "data": {
        "target": {
            "tractability": [
                {"label": "Small molecule", "modality": "sm", "value": True},
                {"label": "Antibody", "modality": "ab", "value": True},
                {"label": "PROTAC", "modality": "pr", "value": True},
                {"label": "No evidence", "modality": "sm", "value": False},
            ]
        }
    }
}


@respx.mock
async def test_get_associations_returns_bundle() -> None:
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(200, json=_ASSOCIATIONS_RESPONSE)
    )
    bundle = await get_associations("ENSG00000012048", "EFO_0000305")

    assert isinstance(bundle, AssociationBundle)
    assert bundle.overall_score == pytest.approx(0.87)
    assert bundle.genetic_score == pytest.approx(0.9)
    assert bundle.known_drugs_score == pytest.approx(0.7)


@respx.mock
async def test_get_associations_empty_returns_zero_scores() -> None:
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"target": {"associatedDiseases": {"rows": []}}}}
        )
    )
    bundle = await get_associations("ENSGXXX", "EFOXXX")
    assert bundle.overall_score == 0.0


@respx.mock
async def test_get_tractability_returns_bundle() -> None:
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(200, json=_TRACTABILITY_RESPONSE)
    )
    bundle = await get_tractability("ENSG00000012048")

    assert isinstance(bundle, TractabilityBundle)
    assert bundle.small_molecule is True
    assert bundle.antibody is True
    assert "PROTAC" in bundle.other_modalities


@respx.mock
async def test_get_tractability_all_false() -> None:
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "target": {"tractability": [{"label": "x", "modality": "sm", "value": False}]}
                }
            },
        )
    )
    bundle = await get_tractability("ENSGXXX")
    assert bundle.small_molecule is False
    assert bundle.antibody is False


@respx.mock
async def test_get_associations_raises_on_graphql_error() -> None:
    from core.exceptions import MCPToolError

    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "bad query"}]})
    )
    with pytest.raises(MCPToolError, match="GraphQL error"):
        await get_associations("X", "Y")


# ---------------------------------------------------------------------------
# get_disease_descendants tests
# ---------------------------------------------------------------------------

_DESCENDANTS_RESPONSE = {
    "data": {
        "disease": {
            "id": "EFO_0003860",
            "descendants": ["EFO_0002618", "MONDO_0005106"],
            "therapeuticAreas": [
                {"id": "MONDO_0045024"},
                {"id": "EFO_0000616"},
            ],
        }
    }
}

_DESCENDANTS_EMPTY = {"data": {"disease": None}}


@respx.mock
async def test_get_disease_descendants_returns_self_plus_children() -> None:
    _disease_ontology_cache.clear()
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(200, json=_DESCENDANTS_RESPONSE)
    )
    onto = await get_disease_descendants("EFO_0003860")

    assert isinstance(onto, DiseaseOntology)
    assert "EFO_0003860" in onto.efo_ids
    assert "EFO_0002618" in onto.efo_ids
    assert "MONDO_0005106" in onto.efo_ids
    assert "MONDO_0045024" in onto.therapeutic_areas
    assert "EFO_0000616" in onto.therapeutic_areas


@respx.mock
async def test_get_disease_descendants_falls_back_on_empty() -> None:
    _disease_ontology_cache.clear()
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(200, json=_DESCENDANTS_EMPTY)
    )
    onto = await get_disease_descendants("EFO_UNKNOWN")

    assert onto.efo_ids == {"EFO_UNKNOWN"}
    assert onto.therapeutic_areas == set()


@respx.mock
async def test_get_disease_descendants_caches_result() -> None:
    _disease_ontology_cache.clear()
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(200, json=_DESCENDANTS_RESPONSE)
    )
    onto1 = await get_disease_descendants("EFO_0003860")
    # Second call should use cache — route is not registered again, so a real
    # network call would raise; if it doesn't raise we know the cache was hit.
    onto2 = await get_disease_descendants("EFO_0003860")
    assert onto1 is onto2


# ---------------------------------------------------------------------------
# get_colocalizations EFO-scope tests
# ---------------------------------------------------------------------------


def _make_coloc_response(
    gwas_trait: str,
    gwas_disease_ids: list[str],
    h4: float = 0.9,
) -> dict:
    return {
        "data": {
            "target": {
                "approvedSymbol": "PRMT5",
                "credibleSets": {
                    "rows": [
                        {
                            "studyLocusId": "SL001",
                            "studyId": "eQTL_PRMT5",
                            "studyType": "eqtl",
                            "colocalisation": {
                                "rows": [
                                    {
                                        "h4": h4,
                                        "clpp": 0.7,
                                        "colocalisationMethod": "coloc",
                                        "rightStudyType": "gwas",
                                        "otherStudyLocus": {
                                            "studyId": "GWAS001",
                                            "study": {
                                                "traitFromSource": gwas_trait,
                                                "diseases": [{"id": d} for d in gwas_disease_ids],
                                            },
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                },
            }
        }
    }


@respx.mock
async def test_get_colocalizations_efo_scope_keeps_matching() -> None:
    _disease_ontology_cache.clear()
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(
            200,
            json=_make_coloc_response("pancreatic cancer", ["EFO_0003860"]),
        )
    )
    bundle = await get_colocalizations(
        "ENSG00000124243",
        efo_ids={"EFO_0003860", "EFO_0002618"},
    )

    assert isinstance(bundle, ColocBundle)
    assert len(bundle.hits) == 1
    assert bundle.dropped_off_target == 0
    assert "EFO_0003860" in bundle.hits[0].gwas_efo_ids


@respx.mock
async def test_get_colocalizations_efo_scope_drops_off_target() -> None:
    _disease_ontology_cache.clear()
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(
            200,
            json=_make_coloc_response("height", ["EFO_0004339"]),
        )
    )
    bundle = await get_colocalizations(
        "ENSG00000124243",
        efo_ids={"EFO_0003860"},
    )

    assert bundle.hits == []
    assert bundle.dropped_off_target == 1
    assert "height" in bundle.all_traits
    assert "off-indication" in bundle.text


@respx.mock
async def test_get_colocalizations_no_scope_returns_all() -> None:
    """Without efo_ids/trait_terms, backward-compatible: all colocs returned."""
    _disease_ontology_cache.clear()
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(
            200,
            json=_make_coloc_response("height", ["EFO_0004339"]),
        )
    )
    bundle = await get_colocalizations("ENSG00000124243")

    assert len(bundle.hits) == 1
    assert bundle.dropped_off_target == 0
