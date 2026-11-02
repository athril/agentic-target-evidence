# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for IMPC knockout-mouse phenotype MCP tools.

Record shapes are the live bulkdata API's, checked against
``/bulkdata-api/genes/MGI:88388`` (Cftr, withdrawn), ``MGI:99918`` (Mecp2, no data) and
``MGI:2140260`` (Pcsk9, phenotyped) on 2026-11-02.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.impc.tools import ImpcBundle, get_impc_phenotypes

_GENE = "https://www.ebi.ac.uk/mi/impc/bulkdata-api/genes/{mgi}"


def _record(**over: object) -> dict[str, object]:
    """A phenotyped gene record, in the API's own field names."""
    return {
        "mgiAccessionId": "MGI:2140260",
        "markerName": "proprotein convertase subtilisin/kexin type 9",
        "humanGeneSymbol": ["PCSK9"],
        "assignmentStatus": "Selected for production",
        "mouseProductionStatus": "Mice Produced",
        "phenotypeStatus": "Phenotyping data available",
        "phenotypingDataAvailable": True,
        "significantPhenotypeSystem": ["homeostasis/metabolism phenotype"],
        "nonSignificantPhenotypeSystem": ["mortality/aging", "cardiovascular system phenotype"],
        "significantMpTerms": [
            {
                "mpTermName": "decreased circulating cholesterol level",
                "mpTermId": "MP:0005179",
                "topLevelAncestors": [{"mpTermName": "homeostasis/metabolism phenotype"}],
            }
        ],
        **over,
    }


@respx.mock
async def test_phenotyped_gene_reports_terms_and_systems() -> None:
    respx.get(_GENE.format(mgi="MGI:2140260")).mock(
        return_value=httpx.Response(200, json=_record())
    )

    bundle = await get_impc_phenotypes("PCSK9", mgi_id="MGI:2140260")

    assert isinstance(bundle, ImpcBundle)
    assert bundle.coverage == "covered"
    assert bundle.tested is True
    assert bundle.total == 1
    assert bundle.phenotypes[0].top_level_systems == ["homeostasis/metabolism phenotype"]
    assert bundle.tested_not_significant_systems == [
        "mortality/aging",
        "cardiovascular system phenotype",
    ]
    assert bundle.source_link == "https://www.mousephenotype.org/data/genes/MGI:2140260"


@respx.mock
async def test_no_phenotyping_data_is_coverage_not_a_negative() -> None:
    """Cftr's real record: assignment withdrawn, no phenotyping data. The old SOLR connector
    rendered this as "no statistically significant phenotypes found"."""
    respx.get(_GENE.format(mgi="MGI:88388")).mock(
        return_value=httpx.Response(
            200,
            json={
                "mgiAccessionId": "MGI:88388",
                "markerName": "cystic fibrosis transmembrane conductance regulator",
                "assignmentStatus": "Withdrawn",
                "esCellProductionStatus": "ES Cells Produced",
                "phenotypeStatus": None,
                "phenotypingDataAvailable": False,
                "significantMpTerms": None,
                "nonSignificantPhenotypeSystem": None,
            },
        )
    )

    bundle = await get_impc_phenotypes("CFTR", mgi_id="MGI:88388")

    assert bundle.coverage == "not_covered"
    assert bundle.tested is False
    assert bundle.total == 0
    assert bundle.viability == "unknown"
    assert "holds no knockout-mouse phenotyping data" in bundle.text
    assert "Withdrawn" in bundle.coverage_note
    # The wording must not read as a finding about the knockout.
    assert "no significant" not in bundle.text.lower()


@respx.mock
async def test_phenotyped_with_no_significant_call_is_a_scoped_negative() -> None:
    respx.get(_GENE.format(mgi="MGI:99999")).mock(
        return_value=httpx.Response(
            200,
            json=_record(
                mgiAccessionId="MGI:99999",
                significantMpTerms=[],
                significantPhenotypeSystem=[],
                nonSignificantPhenotypeSystem=["mortality/aging", "nervous system phenotype"],
            ),
        )
    )

    bundle = await get_impc_phenotypes("GENE", mgi_id="MGI:99999")

    assert bundle.coverage == "covered"
    assert bundle.total == 0
    # Screened for mortality without a call, so viability is established, not unknown.
    assert bundle.viability == "viable"
    assert "no significant knockout phenotype" in bundle.text
    assert "2 organ system(s) screened" in bundle.text


@respx.mock
async def test_lethality_term_sets_viability() -> None:
    respx.get(_GENE.format(mgi="MGI:1")).mock(
        return_value=httpx.Response(
            200,
            json=_record(
                mgiAccessionId="MGI:1",
                significantMpTerms=[
                    {
                        "mpTermName": "preweaning lethality, complete penetrance",
                        "mpTermId": "MP:0011100",
                    }
                ],
            ),
        )
    )

    bundle = await get_impc_phenotypes("PKD1", mgi_id="MGI:1")

    assert bundle.viability == "lethal"


@respx.mock
async def test_gene_absent_from_the_index_is_not_covered() -> None:
    respx.get(_GENE.format(mgi="MGI:0000000")).mock(return_value=httpx.Response(404))

    bundle = await get_impc_phenotypes("NOTAGENE", mgi_id="MGI:0000000")

    assert bundle.coverage == "not_covered"
    assert "not in the IMPC gene index" in bundle.coverage_note


@respx.mock
async def test_missing_mgi_accession_is_not_checked() -> None:
    """A gene with no mouse ortholog (HLA-B) cannot be queried: the API has no symbol search."""
    respx.get("https://rest.genenames.org/fetch/symbol/HLA-B").mock(
        return_value=httpx.Response(200, json={"response": {"docs": [{"symbol": "HLA-B"}]}})
    )

    bundle = await get_impc_phenotypes("HLA-B")

    assert bundle.coverage == "unknown"
    assert bundle.total == 0
    assert "not checked" in bundle.text.lower()


@respx.mock
async def test_resolves_the_mgi_accession_from_the_symbol() -> None:
    """The pipeline calls with a human symbol only."""
    respx.get("https://rest.genenames.org/fetch/symbol/PCSK9").mock(
        return_value=httpx.Response(
            200,
            json={"response": {"docs": [{"symbol": "PCSK9", "mgd_id": ["MGI:2140260"]}]}},
        )
    )
    route = respx.get(_GENE.format(mgi="MGI:2140260")).mock(
        return_value=httpx.Response(200, json=_record())
    )

    bundle = await get_impc_phenotypes("PCSK9")

    assert route.called
    assert bundle.coverage == "covered"
    assert bundle.mgi_id == "MGI:2140260"


@respx.mock
async def test_raises_on_server_error() -> None:
    respx.get(_GENE.format(mgi="MGI:1")).mock(return_value=httpx.Response(503))

    with pytest.raises(MCPToolError, match="HTTP 503"):
        await get_impc_phenotypes("PKD1", mgi_id="MGI:1")


@respx.mock
async def test_unparsable_response_never_becomes_a_negative() -> None:
    respx.get(_GENE.format(mgi="MGI:1")).mock(
        return_value=httpx.Response(200, text="<!doctype html><html>maintenance</html>")
    )

    with pytest.raises(MCPToolError):
        await get_impc_phenotypes("PKD1", mgi_id="MGI:1")
