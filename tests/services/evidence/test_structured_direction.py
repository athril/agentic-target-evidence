# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Direction of effect from Open Targets' direction-annotated genetic evidence, and the
disease's curated mechanism over the gene-wide variant spectrum.

The failures these prevent: one direction was read from a gene's whole pathogenic-variant
spectrum, pooling disorders that arise from opposite mechanisms (gain of function in one,
loss in another), so every indication of such a gene got the same mechanism.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx

from services.evidence.direction_of_effect import build_direction_reading, from_structured_direction


def _row(source: str, on_target: str | None, on_trait: str | None, disease: str = "disease a"):
    return SimpleNamespace(
        datasource=source,
        disease_name=disease,
        direction_on_target=on_target,
        direction_on_trait=on_trait,
    )


def test_annotated_rows_give_one_observation_per_source_and_direction() -> None:
    rows = [
        _row("orphanet", "LoF", "risk"),
        _row("eva", "LoF", "risk"),
        _row("eva", "LoF", "risk"),
        _row("gene_burden", "LoF", "protect"),
        _row("eva", None, "risk"),  # no direction on the target: nothing to read
    ]

    obs = from_structured_direction(rows)

    by_source = {(o.source, o.favours, o.evidence) for o in obs}
    assert by_source == {
        ("Open Targets orphanet", "increase", "mendelian"),  # loss causes disease: raise it
        ("Open Targets eva", "increase", "mendelian"),
        ("Open Targets gene_burden", "decrease", "genetics_structured"),  # protective loss
    }
    assert next(o for o in obs if o.source == "Open Targets eva").detail.startswith("2 eva row(s)")


def test_the_diseases_curated_mechanism_replaces_the_gene_wide_spectrum() -> None:
    """The spectrum (pooled over the gene's disorders) reads gain of function; this
    disease's curated rows say loss of function causes it."""
    spectrum = {"mechanism": "gof", "direction": "inhibit", "confidence": 0.8}
    curated = [_row("orphanet", "LoF", "risk"), _row("gene2phenotype", "LoF", "risk")]

    without = build_direction_reading(
        "activate", disease="disease a", mechanism=spectrum, mechanism_in_scope=True
    )
    with_rows = build_direction_reading(
        "activate",
        disease="disease a",
        mechanism=spectrum,
        mechanism_in_scope=True,
        structured_rows=curated,
    )

    assert without.verdict == "contradicts"
    assert with_rows.verdict == "supports"
    assert not any(
        o.source == "ClinVar pathogenic variant spectrum" for o in with_rows.observations
    )


def test_burden_rows_alone_do_not_displace_the_spectrum_mechanism() -> None:
    spectrum = {"mechanism": "lof", "direction": "activate", "confidence": 0.8}

    reading = build_direction_reading(
        "activate",
        disease="disease a",
        mechanism=spectrum,
        mechanism_in_scope=True,
        structured_rows=[_row("gene_burden", "LoF", "risk")],
    )

    assert any(o.source == "ClinVar pathogenic variant spectrum" for o in reading.observations)


@respx.mock
async def test_the_fetch_keeps_annotated_rows_and_degrades_to_nothing() -> None:
    from mcp_servers.opentargets import tools

    tools._direction_cache.clear()
    payload = {
        "data": {
            "target": {
                "evidences": {
                    "rows": [
                        {
                            "datasourceId": "orphanet",
                            "disease": {"id": "MONDO_1", "name": "disease a"},
                            "directionOnTarget": "GoF",
                            "directionOnTrait": "risk",
                            "variantFunctionalConsequence": {"label": "missense_variant"},
                        },
                        {
                            "datasourceId": "eva",
                            "disease": {"id": "MONDO_1", "name": "disease a"},
                            "directionOnTarget": None,
                            "directionOnTrait": None,
                        },
                    ]
                }
            }
        }
    }
    route = respx.post("https://api.platform.opentargets.org/api/v4/graphql")
    route.side_effect = [httpx.Response(200, json=payload), httpx.Response(500)]

    rows = await tools.get_direction_evidence("ENSG0001", ["MONDO_1"])
    again = await tools.get_direction_evidence("ENSG0001", ["MONDO_1"])  # cached
    failed = await tools.get_direction_evidence("ENSG0002", ["MONDO_1"])

    assert [(r.datasource, r.direction_on_target, r.direction_on_trait) for r in rows] == [
        ("orphanet", "GoF", "risk")
    ]
    assert again == rows and route.call_count == 2
    assert failed == []
    assert await tools.get_direction_evidence("", ["MONDO_1"]) == []


@respx.mock
async def test_curated_somatic_rows_are_fetched_for_the_scope() -> None:
    from mcp_servers.opentargets import tools

    tools._somatic_cache.clear()
    payload = {
        "data": {
            "target": {
                "evidences": {
                    "rows": [
                        {
                            "datasourceId": "intogen",
                            "disease": {"id": "MONDO_1", "name": "cancer a"},
                        }
                    ]
                }
            }
        }
    }
    route = respx.post("https://api.platform.opentargets.org/api/v4/graphql")
    route.side_effect = [httpx.Response(200, json=payload)]

    rows = await tools.get_somatic_evidence("ENSG0001", ["MONDO_1"])

    assert [(r.datasource, r.disease_id) for r in rows] == [("intogen", "MONDO_1")]
    sent = route.calls[0].request.content.decode()
    assert "cancer_gene_census" in sent and "intogen" in sent
    assert await tools.get_somatic_evidence("ENSG0001", []) == []


# ---------------------------------------------------------------------------
# GWAS–QTL colocalisation
# ---------------------------------------------------------------------------


def _qtl(study_type: str, sign: float):
    return SimpleNamespace(study_type=study_type, beta_ratio_sign=sign)


def test_qtl_colocalisations_give_one_netted_observation_per_type() -> None:
    from services.evidence.direction_of_effect import EVIDENCE_WEIGHTS, from_qtl_colocalisation

    obs = from_qtl_colocalisation(
        [
            _qtl("pqtl", 1.0),
            _qtl("pqtl", 0.5),
            _qtl("eqtl", -1.0),
            _qtl("eqtl", 0.0),  # no sign: dropped
            _qtl("sqtl", 1.0),  # a splice junction's use, not the target's amount
        ],
        "GENE-A",
    )

    by_source = {o.source: o for o in obs}
    assert set(by_source) == {
        "Open Targets GWAS–pqtl colocalisation",
        "Open Targets GWAS–eqtl colocalisation",
    }
    p = by_source["Open Targets GWAS–pqtl colocalisation"]
    e = by_source["Open Targets GWAS–eqtl colocalisation"]
    # raising the target raises the risk → favours lowering it
    assert (p.perturbation, p.outcome, p.favours) == ("increase", "harm", "decrease")
    assert p.weight == EVIDENCE_WEIGHTS["genetics_qtl"] * 0.75  # mean sign
    assert p.detail == (
        "2 strong pqtl colocalisation(s): on balance the allele raising GENE-A raises the risk"
    )
    assert (e.outcome, e.favours) == ("benefit", "increase")
    assert e.weight == EVIDENCE_WEIGHTS["genetics_qtl"]


def test_mixed_signs_of_one_type_weaken_or_cancel_its_observation() -> None:
    from services.evidence.direction_of_effect import EVIDENCE_WEIGHTS, from_qtl_colocalisation

    mostly = from_qtl_colocalisation([_qtl("eqtl", 1.0)] * 3 + [_qtl("eqtl", -1.0)])
    balanced = from_qtl_colocalisation([_qtl("eqtl", 1.0)] * 2 + [_qtl("eqtl", -1.0)] * 2)

    assert [o.outcome for o in mostly] == ["harm"]
    assert mostly[0].weight == EVIDENCE_WEIGHTS["genetics_qtl"] * 0.5
    assert "(3 raising, 1 lowering the risk)" in mostly[0].detail
    assert balanced == []


def test_consistent_qtl_signs_set_the_genetic_direction() -> None:
    reading = build_direction_reading(
        "activate", disease="disease a", qtl_rows=[_qtl("pqtl", 1.0)] * 3
    )

    assert reading.against > reading.support
    assert reading.restricted_to({"genetics_qtl"}).against == reading.against
