# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for IMPC knockout-mouse phenotype MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.impc.tools import ImpcBundle, get_impc_phenotypes

_SOLR_BASE = "https://www.ebi.ac.uk/mi/impc/solr/genotype-phenotype/select"


@respx.mock
async def test_get_impc_phenotypes_derives_lethal_viability() -> None:
    respx.get(_SOLR_BASE).mock(
        return_value=httpx.Response(
            200,
            json={
                "response": {
                    "docs": [
                        {
                            "mp_term_name": "preweaning lethality, complete penetrance",
                            "mp_term_id": "MP:0011100",
                            "p_value": 1e-8,
                            "zygosity": "homozygote",
                            "life_stage_name": "Early adult",
                            "procedure_name": "Viability Primary Screen",
                        }
                    ]
                }
            },
        )
    )

    bundle = await get_impc_phenotypes("Pkd1")

    assert isinstance(bundle, ImpcBundle)
    assert bundle.viability == "lethal"
    assert bundle.total == 1
    assert "Pkd1" in bundle.text


@respx.mock
async def test_get_impc_phenotypes_viable_when_no_lethality_terms() -> None:
    respx.get(_SOLR_BASE).mock(
        return_value=httpx.Response(
            200,
            json={
                "response": {
                    "docs": [
                        {
                            "mp_term_name": "abnormal circulating cholesterol level",
                            "mp_term_id": "MP:0005281",
                            "p_value": 1e-5,
                            "zygosity": "homozygote",
                            "life_stage_name": "Early adult",
                            "procedure_name": "Clinical Chemistry",
                        }
                    ]
                }
            },
        )
    )

    bundle = await get_impc_phenotypes("Ldlr")

    assert bundle.viability == "viable"
    assert bundle.total == 1


@respx.mock
async def test_get_impc_phenotypes_unknown_viability_when_no_results() -> None:
    respx.get(_SOLR_BASE).mock(return_value=httpx.Response(200, json={"response": {"docs": []}}))

    bundle = await get_impc_phenotypes("UNKNOWNGENE")

    assert bundle.viability == "unknown"
    assert bundle.total == 0
    assert "No statistically significant" in bundle.text


@respx.mock
async def test_get_impc_phenotypes_raises_on_non_200() -> None:
    respx.get(_SOLR_BASE).mock(return_value=httpx.Response(503))

    with pytest.raises(MCPToolError, match="HTTP 503"):
        await get_impc_phenotypes("Pkd1")
