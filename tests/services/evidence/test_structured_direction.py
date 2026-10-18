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
