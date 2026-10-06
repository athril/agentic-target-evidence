# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for DepMap MCP tools."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from core.exceptions import MCPToolError
from mcp_servers.depmap.tools import (
    _FIGSHARE_PINNED_RELEASE_ID,
    DependencyBundle,
    LineageSummary,
    _compute_lineage_breakdown,
    _extract_gene_scores,
    _load_model_lineages,
    bundle_has_dependency_data,
    get_dependency,
)
from mcp_servers.depmap.tools import _figshare_release_files as _real_figshare_release_files

_GENE = "KRAS"


@pytest.fixture(autouse=True)
def _figshare_unavailable_by_default():
    """Unit tests never reach figshare; tests of the fallback patch it explicitly."""
    with patch(
        "mcp_servers.depmap.tools._figshare_release_files",
        AsyncMock(side_effect=MCPToolError("figshare unavailable in unit tests")),
    ):
        yield


_ENTREZ = "3845"

# ---------------------------------------------------------------------------
# Fixtures: fake CSV payloads
# ---------------------------------------------------------------------------

_SUMMARY_CSV = """\
Entrez Id,Gene,Dataset,Dependent Cell Lines,Cell Lines with Data,Strongly Selective,Common Essential
3845,KRAS,DependencyEnum.Chronos_Combined,312,850,True,False
"""

# The portal's file listing is CSV (release, release_date, filename, url, md5_hash),
# newest release first.
_DOWNLOADS_CSV = """\
release,release_date,filename,url,md5_hash
DepMap Public 25Q1,2025-05-01,CRISPRGeneEffect.csv,https://depmap.org/fake/CRISPRGeneEffect.csv,x
DepMap Public 25Q1,2025-05-01,Model.csv,https://depmap.org/fake/Model.csv,y
"""

_HTML_CHALLENGE = httpx.Response(
    200,
    text="<!DOCTYPE html><html><head><title>DepMap — Verification</title>",
    headers={"content-type": "text/html; charset=utf-8"},
)

_EFFECT_CSV = f"""\
ModelID,KRAS ({_ENTREZ}),TP53 (7157)
ACH-000001,-1.8,-0.1
ACH-000002,-1.2,-0.2
ACH-000003,-0.3,-0.0
ACH-000004,-1.5,-0.3
ACH-000005,-1.6,-0.2
"""

_MODEL_CSV = """\
ModelID,OncotreeLineage,OncotreePrimaryDisease
ACH-000001,Lung,Lung Adenocarcinoma
ACH-000002,Lung,Lung Squamous Cell Carcinoma
ACH-000003,Breast,Breast Cancer
ACH-000004,Pancreas,Pancreatic Adenocarcinoma
ACH-000005,Lung,Lung Small Cell
"""


# ---------------------------------------------------------------------------
# Unit tests for parsing helpers
# ---------------------------------------------------------------------------


def _write_tmp(content: str, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
        f.write(content)
    return Path(f.name)


def test_extract_gene_scores_finds_gene():
    path = _write_tmp(_EFFECT_CSV, ".csv")
    try:
        result = _extract_gene_scores(path, _GENE, _ENTREZ)
        assert set(result.keys()) == {
            "ACH-000001",
            "ACH-000002",
            "ACH-000003",
            "ACH-000004",
            "ACH-000005",
        }
        assert result["ACH-000001"] == pytest.approx(-1.8)
        assert result["ACH-000003"] == pytest.approx(-0.3)
    finally:
        path.unlink()


def test_extract_gene_scores_unknown_gene_returns_empty():
    path = _write_tmp(_EFFECT_CSV, ".csv")
    try:
        result = _extract_gene_scores(path, "NONEXISTENT", None)
        assert result == {}
    finally:
        path.unlink()


def test_load_model_lineages():
    path = _write_tmp(_MODEL_CSV, ".csv")
    try:
        lineages = _load_model_lineages(path)
        assert lineages["ACH-000001"] == "Lung"
        assert lineages["ACH-000004"] == "Pancreas"
    finally:
        path.unlink()


def test_compute_lineage_breakdown():
    model_to_score = {
        "ACH-000001": -1.8,
        "ACH-000002": -1.2,
        "ACH-000003": -0.3,
        "ACH-000004": -1.5,
    }
    lineages = {
        "ACH-000001": "Lung",
        "ACH-000002": "Lung",
        "ACH-000003": "Breast",
        "ACH-000004": "Pancreas",
    }
    breakdown = _compute_lineage_breakdown(model_to_score, lineages)
    assert isinstance(breakdown, list)
    assert all(isinstance(x, LineageSummary) for x in breakdown)

    lung = next(x for x in breakdown if x.lineage == "Lung")
    assert lung.n_total == 2
    assert lung.n_dependent == 2

    breast = next(x for x in breakdown if x.lineage == "Breast")
    assert breast.n_dependent == 0

    # Lung should rank first (100% dependent)
    assert breakdown[0].lineage in {"Lung", "Pancreas"}


# ---------------------------------------------------------------------------
# Integration tests for get_dependency (with mocked HTTP)
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_get_dependency_summary_only() -> None:
    """Bulk file fetch fails gracefully; summary data is still returned."""
    respx.get("https://depmap.org/portal/api/download/gene_dep_summary").mock(
        return_value=httpx.Response(200, text=_SUMMARY_CSV)
    )
    respx.get("https://depmap.org/portal/api/download/files").mock(return_value=httpx.Response(500))

    bundle = await get_dependency(_GENE)

    assert isinstance(bundle, DependencyBundle)
    assert bundle.gene_symbol == _GENE
    assert bundle.num_dependent_lines == 312
    assert bundle.total_lines == 850
    assert bundle.is_common_essential is False
    assert bundle.is_strongly_selective is True
    # Quantitative scores unavailable when bulk fetch fails
    assert bundle.gene_effect_mean is None
    assert bundle.lineage_breakdown == []
    assert _GENE in bundle.text


@respx.mock
@pytest.mark.asyncio
async def test_get_dependency_full_with_lineages() -> None:
    """Full path: summary + bulk files produce quantitative scores and lineage breakdown."""
    respx.get("https://depmap.org/portal/api/download/gene_dep_summary").mock(
        return_value=httpx.Response(200, text=_SUMMARY_CSV)
    )
    respx.get("https://depmap.org/portal/api/download/files").mock(
        return_value=httpx.Response(200, text=_DOWNLOADS_CSV)
    )

    # Patch _ensure_cached so we avoid actual disk writes and file checks
    async def fake_ensure_cached(filename: str, urls: dict, release: str = "") -> Path:
        if filename == "CRISPRGeneEffect.csv":
            p = _write_tmp(_EFFECT_CSV, ".csv")
        else:
            p = _write_tmp(_MODEL_CSV, ".csv")
        return p

    with patch("mcp_servers.depmap.tools._ensure_cached", side_effect=fake_ensure_cached):
        bundle = await get_dependency(_GENE)

    assert bundle.gene_effect_mean is not None
    assert bundle.gene_effect_std is not None
    assert bundle.gene_effect_q1 is not None
    assert bundle.gene_effect_median is not None
    assert bundle.gene_effect_q3 is not None
    assert bundle.dependency_fraction == pytest.approx(312 / 850, rel=1e-3)

    assert len(bundle.lineage_breakdown) > 0
    lineage_names = [lb.lineage for lb in bundle.lineage_breakdown]
    assert "Lung" in lineage_names
    assert "Breast" in lineage_names

    lung = next(lb for lb in bundle.lineage_breakdown if lb.lineage == "Lung")
    assert lung.n_dependent == 3
    assert lung.n_total == 3

    # Lung ≥90% dependent → should appear in selective_lineages
    assert "Lung" in bundle.selective_lineages

    assert "Chronos" in bundle.text
    assert "Mean" in bundle.text


@respx.mock
@pytest.mark.asyncio
async def test_get_dependency_gene_not_in_summary() -> None:
    """Gene absent from summary returns an empty bundle."""
    respx.get("https://depmap.org/portal/api/download/gene_dep_summary").mock(
        return_value=httpx.Response(200, text=_SUMMARY_CSV)
    )
    respx.get("https://depmap.org/portal/api/download/files").mock(return_value=httpx.Response(500))

    bundle = await get_dependency("FAKEGENE99")

    assert bundle.gene_symbol == "FAKEGENE99"
    assert bundle.num_dependent_lines is None
    assert bundle.gene_effect_mean is None
    assert "unknown" in bundle.text or "FAKEGENE99" in bundle.text


@respx.mock
@pytest.mark.asyncio
async def test_get_dependency_raises_on_summary_500() -> None:
    respx.get("https://depmap.org/portal/api/download/gene_dep_summary").mock(
        return_value=httpx.Response(500)
    )
    respx.get("https://depmap.org/portal/api/download/files").mock(return_value=httpx.Response(500))

    with pytest.raises(MCPToolError, match="HTTP 500"):
        await get_dependency(_GENE)


# ---------------------------------------------------------------------------
# figshare release-mirror fallback (portal behind a bot-verification page)
# ---------------------------------------------------------------------------

_CE_CSV = "Essentials\nRPL3 (6122)\nKRAS (3845)\n"


@respx.mock
@pytest.mark.asyncio
async def test_get_dependency_falls_back_to_figshare_release_when_portal_blocked() -> None:
    """Portal summary + listing both return the verification page: scores, counts and
    common-essential status come from the figshare release, which the text names."""
    respx.get("https://depmap.org/portal/api/download/gene_dep_summary").mock(
        return_value=_HTML_CHALLENGE
    )
    respx.get("https://depmap.org/portal/api/download/files").mock(return_value=_HTML_CHALLENGE)
    urls = {
        "CRISPRGeneEffect.csv": "https://ndownloader.figshare.com/files/1",
        "Model.csv": "https://ndownloader.figshare.com/files/2",
        "CRISPRInferredCommonEssentials.csv": "https://ndownloader.figshare.com/files/3",
    }
    files = {
        "CRISPRGeneEffect.csv": _EFFECT_CSV,
        "Model.csv": _MODEL_CSV,
        "CRISPRInferredCommonEssentials.csv": _CE_CSV,
    }
    seen_releases: set[str] = set()

    async def fake_ensure_cached(filename: str, urls: dict, release: str = "") -> Path:
        seen_releases.add(release)
        return _write_tmp(files[filename], ".csv")

    with (
        patch(
            "mcp_servers.depmap.tools._figshare_release_files",
            AsyncMock(return_value=("DepMap 24Q4 Public (figshare)", urls)),
        ),
        patch("mcp_servers.depmap.tools._ensure_cached", side_effect=fake_ensure_cached),
    ):
        bundle = await get_dependency(_GENE)

    # KRAS fixture scores: -1.8, -1.2, -0.3, -1.5, -1.6 → 4/5 at ≤ −0.5
    assert (bundle.num_dependent_lines, bundle.total_lines) == (4, 5)
    assert bundle.is_common_essential is True
    assert bundle.release == "DepMap 24Q4 Public (figshare)"
    assert "scores from DepMap 24Q4 Public (figshare)" in bundle.text
    assert seen_releases == {"DepMap 24Q4 Public (figshare)"}
    assert bundle_has_dependency_data(bundle)


@respx.mock
@pytest.mark.asyncio
async def test_figshare_release_discovery_picks_newest_official_release() -> None:
    respx.post("https://api.figshare.com/v2/articles/search").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 25880521, "title": "DepMap 24Q2 Public"},
                {"id": 27993248, "title": "DepMap 24Q4 Public"},
                {"id": 31660582, "title": "Chronos parameters (Public 26Q1)"},
                {"id": 99, "title": "DepMap dataset used in Some et al."},
            ],
        )
    )
    article = respx.get("https://api.figshare.com/v2/articles/27993248").mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "DepMap 24Q4 Public",
                "files": [
                    {"name": "CRISPRGeneEffect.csv", "download_url": "https://f/1"},
                    {"name": "Model.csv", "download_url": "https://f/2"},
                    {"name": "OmicsExpression.csv", "download_url": "https://f/9"},
                ],
            },
        )
    )
    release, urls = await _real_figshare_release_files()

    assert article.called
    assert release == "DepMap 24Q4 Public (figshare)"
    assert set(urls) == {"CRISPRGeneEffect.csv", "Model.csv"}


@respx.mock
@pytest.mark.asyncio
async def test_figshare_release_uses_pinned_release_when_search_fails() -> None:
    respx.post("https://api.figshare.com/v2/articles/search").mock(return_value=httpx.Response(503))
    pinned = respx.get(f"https://api.figshare.com/v2/articles/{_FIGSHARE_PINNED_RELEASE_ID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "DepMap 24Q4 Public",
                "files": [
                    {"name": "CRISPRGeneEffect.csv", "download_url": "https://f/1"},
                    {"name": "Model.csv", "download_url": "https://f/2"},
                ],
            },
        )
    )
    release, _urls = await _real_figshare_release_files()
    assert pinned.called
    assert release.startswith("DepMap 24Q4 Public")


@respx.mock
@pytest.mark.asyncio
async def test_get_dependency_raises_on_html_challenge_page() -> None:
    """depmap.org answers with a 200 + HTML bot-verification page; parsed as CSV it
    yields zero rows, indistinguishable from "gene not found" — it must raise."""
    respx.get("https://depmap.org/portal/api/download/gene_dep_summary").mock(
        return_value=_HTML_CHALLENGE
    )
    respx.get("https://depmap.org/portal/api/download/files").mock(return_value=_HTML_CHALLENGE)

    with pytest.raises(MCPToolError, match="HTML page.*bulk release fallback"):
        await get_dependency(_GENE)


def test_bundle_has_dependency_data() -> None:
    assert not bundle_has_dependency_data(DependencyBundle(gene_symbol="X"))
    assert bundle_has_dependency_data(
        DependencyBundle(gene_symbol="X", num_dependent_lines=2, total_lines=1208)
    )
    assert bundle_has_dependency_data(DependencyBundle(gene_symbol="X", gene_effect_mean=-0.1))


# ---------------------------------------------------------------------------
# Regression: TRPC6-shaped "Strongly Selective" with zero actual dependency
# ---------------------------------------------------------------------------

_TRPC6_GENE = "TRPC6"
_TRPC6_ENTREZ = "7222"

# 2/1208 dependent lines — below the 5% threshold; flag is set but gene is non-essential
_TRPC6_SUMMARY_CSV = """\
Entrez Id,Gene,Dataset,Dependent Cell Lines,Cell Lines with Data,Strongly Selective,Common Essential
7222,TRPC6,DependencyEnum.Chronos_Combined,2,1208,True,False
"""

# All Chronos scores near 0 — no cell line actually dependent (threshold ≤ −0.5)
_TRPC6_EFFECT_CSV = f"""\
ModelID,TRPC6 ({_TRPC6_ENTREZ})
ACH-T001,0.005
ACH-T002,-0.010
ACH-T003,0.003
ACH-T004,0.095
ACH-T005,-0.002
ACH-T006,0.008
ACH-T007,0.047
ACH-T008,-0.015
ACH-T009,0.001
"""

_TRPC6_MODEL_CSV = """\
ModelID,OncotreeLineage,OncotreePrimaryDisease
ACH-T001,Lung,Lung Adenocarcinoma
ACH-T002,Lung,Lung Squamous Cell Carcinoma
ACH-T003,Breast,Breast Cancer
ACH-T004,Pancreas,Pancreatic Adenocarcinoma
ACH-T005,Kidney,Kidney Cancer
ACH-T006,Lung,Lung Small Cell
ACH-T007,Breast,Breast Cancer
ACH-T008,Colon,Colorectal Cancer
ACH-T009,Ovary,Ovarian Cancer
"""


@respx.mock
@pytest.mark.asyncio
async def test_get_dependency_strongly_selective_flag_with_no_actual_dependency() -> None:
    """Regression: is_strongly_selective=True but near-zero dependency fraction and
    no high-dependency lineages must produce a corrective text, not the plain
    'Strongly selective.' positive claim."""
    respx.get("https://depmap.org/portal/api/download/gene_dep_summary").mock(
        return_value=httpx.Response(200, text=_TRPC6_SUMMARY_CSV)
    )
    respx.get("https://depmap.org/portal/api/download/files").mock(
        return_value=httpx.Response(200, text=_DOWNLOADS_CSV)
    )

    async def fake_ensure_cached_trpc6(filename: str, urls: dict, release: str = "") -> Path:
        if filename == "CRISPRGeneEffect.csv":
            return _write_tmp(_TRPC6_EFFECT_CSV, ".csv")
        return _write_tmp(_TRPC6_MODEL_CSV, ".csv")

    with patch("mcp_servers.depmap.tools._ensure_cached", side_effect=fake_ensure_cached_trpc6):
        bundle = await get_dependency(_TRPC6_GENE)

    assert bundle.gene_symbol == _TRPC6_GENE
    assert bundle.is_strongly_selective is True
    assert bundle.selective_lineages == []  # no lineage reaches 90% dependency
    assert bundle.dependency_fraction is not None
    assert bundle.dependency_fraction < 0.05  # 2/1208 ≈ 0.0017

    # Corrective phrasing must be present
    assert "not a usable selectivity signal" in bundle.text
    # Plain positive claim must NOT appear
    assert "Strongly selective." not in bundle.text
