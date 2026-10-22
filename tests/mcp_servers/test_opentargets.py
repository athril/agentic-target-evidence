# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for Open Targets MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.ontology import tools as ontology_tools
from mcp_servers.ontology.tools import OntologyParent
from mcp_servers.opentargets.tools import (
    AssociationBundle,
    ColocBundle,
    DiseaseOntology,
    TractabilityBundle,
    _disease_ontology_cache,
    get_associations,
    get_colocalizations,
    get_disease_descendants,
    get_known_drugs,
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


@respx.mock
async def test_the_ontology_carries_names_by_relation_for_subtypes_and_ancestors() -> None:
    """The disease scope needs subtype and ancestor names, and synonyms by relation."""
    _disease_ontology_cache.clear()
    main = {
        "data": {
            "disease": {
                "id": "MONDO_1",
                "name": "disease a",
                "synonyms": [
                    {"relation": "hasExactSynonym", "terms": ["DA"]},
                    {"relation": "hasNarrowSynonym", "terms": ["juvenile disease a"]},
                    {"relation": "hasBroadSynonym", "terms": ["disease group"]},
                ],
                "parents": [{"id": "MONDO_P", "name": "parent disorder"}],
                "descendants": ["MONDO_2"],
                "ancestors": ["MONDO_P", "MONDO_R"],
                "therapeuticAreas": [],
            }
        }
    }
    names = {
        "data": {
            "diseases": [
                {
                    "id": "MONDO_2",
                    "name": "disease a type 2",
                    "synonyms": [{"relation": "hasExactSynonym", "terms": ["DA2 disease"]}],
                },
                {"id": "MONDO_P", "name": "parent disorder", "synonyms": []},
                {"id": "MONDO_R", "name": "root disorder", "synonyms": []},
            ]
        }
    }
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        side_effect=[httpx.Response(200, json=main), httpx.Response(200, json=names)]
    )

    onto = await get_disease_descendants("MONDO_1")

    assert onto.names == {"disease a", "DA"}
    assert onto.narrow_names == {"juvenile disease a"}
    assert onto.broad_names == {"disease group"}
    assert onto.descendant_names == {"disease a type 2", "DA2 disease"}
    assert onto.ancestor_names == {"parent disorder", "root disorder"}
    assert not onto.names_partial


@pytest.fixture(autouse=True)
def _no_xref_parents(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ontology tests above mock Open Targets only; cross-reference parents come from
    OLS4/NCBI and are tested on their own below."""

    async def none(_disease_id: str) -> list[OntologyParent]:
        return []

    monkeypatch.setattr(ontology_tools, "xref_parents", none)


_ONE_DISEASE = {
    "data": {
        "disease": {
            "id": "MONDO_9",
            "name": "disease nine",
            "synonyms": [{"relation": "hasExactSynonym", "terms": ["D9"]}],
            "parents": [],
            "descendants": [],
            "ancestors": [],
            "therapeuticAreas": [{"id": "TA_1", "name": "body system disease"}],
        }
    }
}


@respx.mock
async def test_cross_reference_parents_become_names_with_their_source(monkeypatch):
    _disease_ontology_cache.clear()
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(200, json=_ONE_DISEASE)
    )

    async def parents(_disease_id: str) -> list[OntologyParent]:
        return [
            OntologyParent(
                ontology="DOID",
                term_id="DOID:2",
                label="group disease",
                exact_synonyms=["GD", "disease nine"],  # the disease's own name: excluded
            ),
            # Overlapping parents from two ontologies: the first source is kept.
            OntologyParent(ontology="MESH", term_id="MESH:D2", label="group disease"),
            # A therapeutic area is far too broad to name the disease group.
            OntologyParent(ontology="NCIT", term_id="NCIT:C3", label="Body System Disease"),
        ]

    monkeypatch.setattr(ontology_tools, "xref_parents", parents)
    onto = await get_disease_descendants("MONDO_9")

    assert onto.xref_parent_names == {"group disease", "GD"}
    assert onto.xref_parent_sources == {"group disease": "DOID:2", "GD": "DOID:2"}
    assert not onto.names_partial


@respx.mock
async def test_a_failed_cross_reference_lookup_keeps_the_scope_and_marks_it_partial(monkeypatch):
    _disease_ontology_cache.clear()
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(200, json=_ONE_DISEASE)
    )

    async def down(_disease_id: str) -> list[OntologyParent]:
        raise MCPToolError("OLS API returned HTTP 503")

    monkeypatch.setattr(ontology_tools, "xref_parents", down)
    onto = await get_disease_descendants("MONDO_9")

    assert onto.names == {"disease nine", "D9"}
    assert onto.xref_parent_names == set()
    assert onto.names_partial


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


@respx.mock
async def test_get_known_drugs_marks_approval_stage_as_approved() -> None:
    """Open Targets reports approved drugs with maximumClinicalStage == "APPROVAL"."""

    def row(name: str, row_stage: str, drug_stage: str) -> dict[str, object]:
        return {
            "maxClinicalStage": row_stage,
            "drug": {
                "id": f"CHEMBL_{name}",
                "name": name,
                "drugType": "Antibody",
                "maximumClinicalStage": drug_stage,
                "mechanismsOfAction": {"rows": []},
            },
            "diseases": [{"disease": {"id": "EFO_1", "name": "hypercholesterolemia"}}],
            "clinicalReports": [],
        }

    payload = {
        "data": {
            "target": {
                "approvedSymbol": "PCSK9",
                "drugAndClinicalCandidates": {
                    "count": 2,
                    "rows": [
                        row("EVOLOCUMAB", "PHASE_4", "APPROVAL"),
                        row("FROVOCIMAB", "PHASE_2", "PHASE_2"),
                    ],
                },
            }
        }
    }
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(200, json=payload)
    )
    bundle = await get_known_drugs("ENSG00000169174")
    approved = {d.drug_name: d.is_approved for d in bundle.drugs}
    assert approved == {"EVOLOCUMAB": True, "FROVOCIMAB": False}
    assert "Approved: 1" in bundle.text


@respx.mock
async def test_get_known_drugs_keeps_every_clinical_report_with_all_its_stop_categories() -> None:
    """Only Negative/Safety stops were kept, so a phase 3 stopped for funding and one that
    completed with no reason were indistinguishable from 'nothing recorded'. A fragment in
    the shape of the live response (trialStopReasonCategories as returned 2026-10-16)."""

    def report(
        rid: str,
        stage: str,
        status: str,
        why: str | None,
        categories: list[str],
        disease: tuple[str, str] = ("MONDO_1", "disease one"),
    ) -> dict[str, object]:
        return {
            "id": rid,
            "clinicalStage": stage,
            "trialOverallStatus": status,
            "trialWhyStopped": why,
            "trialStopReasonCategories": categories,
            "diseases": [{"disease": {"id": disease[0], "name": disease[1]}}],
        }

    payload = {
        "data": {
            "target": {
                "approvedSymbol": "GENEX",
                "drugAndClinicalCandidates": {
                    "count": 1,
                    "rows": [
                        {
                            "maxClinicalStage": "PHASE_3",
                            "drug": {
                                "id": "CHEMBL1",
                                "name": "DRUGA",
                                "drugType": "Small molecule",
                                "maximumClinicalStage": "PHASE_3",
                                "mechanismsOfAction": {
                                    "rows": [
                                        {
                                            "mechanismOfAction": "X inhibitor",
                                            "actionType": "INHIBITOR",
                                        }
                                    ]
                                },
                            },
                            "diseases": [],
                            "clinicalReports": [
                                report(
                                    "nct1",
                                    "PHASE_3",
                                    "TERMINATED",
                                    "Low accrual and futility\n at interim",
                                    ["Insufficient_Enrollment", "Negative"],
                                ),
                                report("nct2", "PHASE_2", "COMPLETED", None, []),
                                report(
                                    "nct3",
                                    "PHASE_2",
                                    "WITHDRAWN",
                                    "Funding",
                                    ["Business_Administrative"],
                                    ("MONDO_2", "disease two"),
                                ),
                            ],
                        }
                    ],
                },
            }
        }
    }
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        return_value=httpx.Response(200, json=payload)
    )
    bundle = await get_known_drugs("ENSG00000000001")
    by_disease = {d.disease_id: d for d in bundle.drugs}

    one = by_disease["MONDO_1"]
    assert [r["trial_id"] for r in one.stop_reports] == ["nct1", "nct2"]
    assert one.stop_reports[0] == {
        "trial_id": "nct1",
        "stage": 3.0,
        "status": "TERMINATED",
        "categories": ["Insufficient_Enrollment", "Negative"],
        "why": "Low accrual and futility at interim",
    }
    assert one.stop_reports[1]["categories"] == [] and one.stop_reports[1]["why"] == ""
    # The existing field is unchanged: only the efficacy/safety stop, as before.
    assert len(one.stopped_for_cause) == 1 and "[Negative]" in one.stopped_for_cause[0]

    two = by_disease["MONDO_2"]
    assert two.stopped_for_cause == []
    assert two.stop_reports[0]["categories"] == ["Business_Administrative"]


# ---------------------------------------------------------------------------
# GWAS–QTL colocalisation
# ---------------------------------------------------------------------------

_OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"


def _coloc_row(target: str, study_type: str, sign: float, h4=None, clpp=None) -> dict:
    return {
        "h4": h4,
        "clpp": clpp,
        "betaRatioSignAverage": sign,
        "otherStudyLocus": {"study": {"studyType": study_type, "target": {"id": target}}},
    }


@respx.mock
async def test_qtl_colocalisations_keep_strong_rows_of_the_target_only(monkeypatch) -> None:
    from mcp_servers.opentargets import tools

    tools._qtl_cache.clear()
    loci = {
        "data": {
            "target": {
                "evidences": {
                    "rows": [
                        {"disease": {"id": "MONDO_1"}, "credibleSet": {"studyLocusId": "L1"}},
                        {"disease": {"id": "MONDO_2"}, "credibleSet": {"studyLocusId": "L1"}},
                        {"disease": {"id": "MONDO_1"}, "credibleSet": {"studyLocusId": "L2"}},
                    ]
                }
            }
        }
    }
    coloc = {
        "data": {
            "cs0": {
                "colocalisation": {
                    "count": 5,
                    "rows": [
                        _coloc_row("ENSG1", "pqtl", 1.0, h4=0.95),
                        _coloc_row("ENSG_OTHER", "pqtl", 1.0, h4=0.99),  # another gene's QTL
                        _coloc_row("ENSG1", "eqtl", -0.5, clpp=0.02),
                        _coloc_row("ENSG1", "eqtl", 1.0, h4=0.5, clpp=0.001),  # weak
                        _coloc_row("ENSG1", "sqtl", 0.0, h4=0.9),  # no sign
                    ],
                }
            },
            "cs1": {"colocalisation": {"count": 0, "rows": []}},
        }
    }
    route = respx.post(_OT_URL)
    route.side_effect = [httpx.Response(200, json=loci), httpx.Response(200, json=coloc)]

    rows = await tools.get_qtl_colocalisations("ENSG1", ["MONDO_1", "MONDO_2"])
    again = await tools.get_qtl_colocalisations("ENSG1", ["MONDO_2", "MONDO_1"])

    assert [
        (r.study_type, r.beta_ratio_sign, r.gwas_study_locus_id, r.disease_id) for r in rows
    ] == [
        ("pqtl", 1.0, "L1", "MONDO_1"),
        ("eqtl", -0.5, "L1", "MONDO_1"),
    ]
    assert again == rows and route.call_count == 2  # cached
    coloc_query = route.calls[1].request.content.decode()
    assert "cs0: credibleSet(studyLocusId: $id0)" in coloc_query
    assert "cs1: credibleSet(studyLocusId: $id1)" in coloc_query


@respx.mock
async def test_qtl_colocalisation_failure_is_empty_and_a_degradation(monkeypatch) -> None:
    from mcp_servers.opentargets import tools

    tools._qtl_cache.clear()
    recorded: list[str] = []
    monkeypatch.setattr(tools, "record_degradation", lambda c, *_: recorded.append(c))
    respx.post(_OT_URL).mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "boom"}]})
    )

    assert await tools.get_qtl_colocalisations("ENSG1", ["MONDO_1"]) == []
    assert recorded == ["genetics: QTL colocalisation"]
    assert await tools.get_qtl_colocalisations("", ["MONDO_1"]) == []


@respx.mock
async def test_the_disease_scope_carries_mesh_ids_of_the_disease_and_its_subtypes() -> None:
    _disease_ontology_cache.clear()
    disease = {
        "data": {
            "disease": {
                "id": "MONDO_0004975",
                "name": "Alzheimer disease",
                "descendants": ["MONDO_0007088"],
                "dbXRefs": ["DOID:10652", "MESH:D000544"],
            }
        }
    }
    names = {
        "data": {
            "diseases": [
                {"id": "MONDO_0007088", "name": "early-onset AD", "dbXRefs": ["MESH:C536594"]}
            ]
        }
    }
    respx.post("https://api.platform.opentargets.org/api/v4/graphql").mock(
        side_effect=[httpx.Response(200, json=disease), httpx.Response(200, json=names)]
        + [httpx.Response(200, json={"data": {}})] * 10
    )

    onto = await get_disease_descendants("MONDO_0004975")

    assert onto.mesh_ids == {"D000544", "C536594"}
