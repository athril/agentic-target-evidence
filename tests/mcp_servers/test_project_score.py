# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for Project Score (Sanger) MCP tools."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.project_score.tools import ProjectScoreBundle, get_project_score

_API_BASE = "https://api.cellmodelpassports.sanger.ac.uk"
_GENE = "KRAS"
_SIDG = "SIDG13960"


def _genes_response(sidg_id: str = _SIDG) -> dict:
    return {"data": [{"id": sidg_id, "attributes": {"symbol": _GENE}, "type": "gene"}]}


def _profile_response(**overrides) -> dict:
    attrs = {
        "common_essential": "false",
        "core_fitness_pancan": False,
        **{f"adm_status_{t}": None for t in ("lung", "colon", "pancreas", "skin")},
        **overrides,
    }
    return {"data": [{"id": 1, "attributes": attrs, "type": "essentiality_profile"}]}


def _crispr_ko_response(bf_scaled_values: list[float]) -> dict:
    return {
        "data": [
            {
                "id": i,
                "attributes": {"bf_scaled": v, "source": "Sanger", "qc_pass": True},
                "type": "crispr_ko",
            }
            for i, v in enumerate(bf_scaled_values)
        ],
        "meta": {"count": len(bf_scaled_values)},
    }


@respx.mock
@pytest.mark.asyncio
async def test_get_project_score_full_path() -> None:
    respx.get(f"{_API_BASE}/genes").mock(return_value=httpx.Response(200, json=_genes_response()))
    respx.get(f"{_API_BASE}/genes/{_SIDG}/essentiality_profiles").mock(
        return_value=httpx.Response(
            200,
            json=_profile_response(
                adm_status_lung="CSCF", adm_status_colon="CSCF", core_fitness_pancan=False
            ),
        )
    )
    respx.get(f"{_API_BASE}/genes/{_SIDG}/datasets/crispr_ko").mock(
        return_value=httpx.Response(200, json=_crispr_ko_response([1.5, 0.8, -0.3, 2.1, -1.0]))
    )

    bundle = await get_project_score(_GENE)

    assert isinstance(bundle, ProjectScoreBundle)
    assert bundle.gene_symbol == _GENE
    assert bundle.sidg_id == _SIDG
    assert bundle.total_lines == 5
    assert bundle.num_fitness_lines == 3  # 1.5, 0.8, 2.1 > 0
    assert bundle.fitness_fraction == pytest.approx(3 / 5)
    assert bundle.is_pancan_core_fitness is False
    assert set(bundle.cancer_specific_core_fitness_tissues) == {"lung", "colon"}
    assert "KRAS" in bundle.text
    assert _SIDG in bundle.source_link


@respx.mock
@pytest.mark.asyncio
async def test_get_project_score_pancan_core_fitness() -> None:
    respx.get(f"{_API_BASE}/genes").mock(return_value=httpx.Response(200, json=_genes_response()))
    respx.get(f"{_API_BASE}/genes/{_SIDG}/essentiality_profiles").mock(
        return_value=httpx.Response(
            200, json=_profile_response(common_essential="true", core_fitness_pancan=True)
        )
    )
    respx.get(f"{_API_BASE}/genes/{_SIDG}/datasets/crispr_ko").mock(
        return_value=httpx.Response(200, json=_crispr_ko_response([3.0, 2.5, 1.8]))
    )

    bundle = await get_project_score(_GENE)

    assert bundle.is_pancan_core_fitness is True
    assert bundle.cancer_specific_core_fitness_tissues == []
    assert "Pan-cancer core fitness" in bundle.text


@respx.mock
@pytest.mark.asyncio
async def test_get_project_score_gene_not_found() -> None:
    respx.get(f"{_API_BASE}/genes").mock(return_value=httpx.Response(200, json={"data": []}))

    bundle = await get_project_score("NOTAREALGENE")

    assert bundle.gene_symbol == "NOTAREALGENE"
    assert bundle.sidg_id == ""
    assert bundle.total_lines is None
    assert "no gene record found" in bundle.text


@respx.mock
@pytest.mark.asyncio
async def test_get_project_score_no_fitness_lines_returns_none_stats() -> None:
    respx.get(f"{_API_BASE}/genes").mock(return_value=httpx.Response(200, json=_genes_response()))
    respx.get(f"{_API_BASE}/genes/{_SIDG}/essentiality_profiles").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    respx.get(f"{_API_BASE}/genes/{_SIDG}/datasets/crispr_ko").mock(
        return_value=httpx.Response(200, json=_crispr_ko_response([]))
    )

    bundle = await get_project_score(_GENE)

    assert bundle.bf_scaled_mean is None
    assert bundle.total_lines is None
    assert bundle.is_pancan_core_fitness is False


@respx.mock
@pytest.mark.asyncio
async def test_get_project_score_raises_on_genes_lookup_error() -> None:
    respx.get(f"{_API_BASE}/genes").mock(return_value=httpx.Response(500))

    with pytest.raises(MCPToolError, match="HTTP 500"):
        await get_project_score(_GENE)


@respx.mock
@pytest.mark.asyncio
async def test_get_project_score_raises_on_essentiality_profile_error() -> None:
    respx.get(f"{_API_BASE}/genes").mock(return_value=httpx.Response(200, json=_genes_response()))
    respx.get(f"{_API_BASE}/genes/{_SIDG}/essentiality_profiles").mock(
        return_value=httpx.Response(503)
    )

    with pytest.raises(MCPToolError, match="HTTP 503"):
        await get_project_score(_GENE)


@respx.mock
@pytest.mark.asyncio
async def test_get_project_score_raises_on_crispr_ko_error() -> None:
    respx.get(f"{_API_BASE}/genes").mock(return_value=httpx.Response(200, json=_genes_response()))
    respx.get(f"{_API_BASE}/genes/{_SIDG}/essentiality_profiles").mock(
        return_value=httpx.Response(200, json=_profile_response())
    )
    respx.get(f"{_API_BASE}/genes/{_SIDG}/datasets/crispr_ko").mock(
        return_value=httpx.Response(502)
    )

    with pytest.raises(MCPToolError, match="HTTP 502"):
        await get_project_score(_GENE)
