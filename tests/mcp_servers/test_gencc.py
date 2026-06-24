# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for GenCC gene-disease validity MCP tools.

GenCC publishes a bulk CSV export of per-submitter classifications. These
tests fake that export as a small temp CSV and patch the module's
`_ensure_cached` boundary, mirroring the pattern used for ClinGen's bulk
tar.gz in test_clingen.py.
"""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import mcp_servers.gencc.tools as gencc_tools
from core.exceptions import MCPToolError
from mcp_servers.gencc.tools import GenCCBundle, get_gencc_validity


@pytest.fixture(autouse=True)
def _reset_module_caches():
    gencc_tools._index = None
    gencc_tools._index_mtime = None
    yield
    gencc_tools._index = None
    gencc_tools._index_mtime = None


def _make_csv(rows: list[dict[str, str]], fieldnames: list[str]) -> Path:
    fd, name = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    path = Path(name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _patch_ensure_cached(csv_path: Path):
    return patch.object(gencc_tools, "_ensure_cached", AsyncMock(return_value=csv_path))


async def test_get_gencc_validity_returns_bundle() -> None:
    fieldnames = [
        "gene_curie",
        "gene_symbol",
        "disease_title",
        "disease_curie",
        "classification_title",
        "moi_title",
        "submitter_title",
        "submitted_as_date",
    ]
    rows = [
        {
            "gene_curie": "HGNC:12338",
            "gene_symbol": "TRPC6",
            "disease_title": "focal segmental glomerulosclerosis",
            "disease_curie": "MONDO:0001085",
            "classification_title": "Limited",
            "moi_title": "Autosomal dominant",
            "submitter_title": "Orphanet",
            "submitted_as_date": "2021-01-01",
        },
        {
            "gene_curie": "HGNC:12338",
            "gene_symbol": "TRPC6",
            "disease_title": "focal segmental glomerulosclerosis",
            "disease_curie": "MONDO:0001085",
            "classification_title": "Definitive",
            "moi_title": "Autosomal dominant",
            "submitter_title": "ClinGen",
            "submitted_as_date": "2022-03-15",
        },
    ]
    csv_path = _make_csv(rows, fieldnames)
    try:
        with _patch_ensure_cached(csv_path):
            bundle = await get_gencc_validity("TRPC6")

        assert isinstance(bundle, GenCCBundle)
        assert bundle.total == 2
        assert bundle.hgnc_id == "HGNC:12338"
        # Strongest classification sorts first
        assert bundle.associations[0].classification == "Definitive"
        assert bundle.associations[0].submitter == "ClinGen"
        assert bundle.associations[1].submitter == "Orphanet"
        assert "Definitive" in bundle.text
    finally:
        csv_path.unlink()


async def test_get_gencc_validity_empty_when_gene_not_in_dataset() -> None:
    fieldnames = ["gene_symbol", "disease_title", "classification_title"]
    rows = [{"gene_symbol": "OTHERGENE", "disease_title": "x", "classification_title": "Strong"}]
    csv_path = _make_csv(rows, fieldnames)
    try:
        with _patch_ensure_cached(csv_path):
            bundle = await get_gencc_validity("TRPC6")

        assert bundle.total == 0
        assert bundle.associations == []
        assert "No GenCC" in bundle.text
    finally:
        csv_path.unlink()


async def test_get_gencc_validity_falls_back_to_submitted_as_fields() -> None:
    """Older export revisions use `submitted_as_*` column names."""
    fieldnames = [
        "submitted_as_hgnc_id",
        "submitted_as_hgnc_symbol",
        "submitted_as_disease_name",
        "submitted_as_classification_name",
        "submitted_as_moi_name",
        "submitted_as_submitter",
    ]
    rows = [
        {
            "submitted_as_hgnc_id": "HGNC:1100",
            "submitted_as_hgnc_symbol": "BRCA1",
            "submitted_as_disease_name": "hereditary breast cancer",
            "submitted_as_classification_name": "Strong",
            "submitted_as_moi_name": "Autosomal dominant",
            "submitted_as_submitter": "PanelApp",
        }
    ]
    csv_path = _make_csv(rows, fieldnames)
    try:
        with _patch_ensure_cached(csv_path):
            bundle = await get_gencc_validity("BRCA1")

        assert bundle.total == 1
        assert bundle.hgnc_id == "HGNC:1100"
        assert bundle.associations[0].disease_title == "hereditary breast cancer"
        assert bundle.associations[0].submitter == "PanelApp"
    finally:
        csv_path.unlink()


async def test_get_gencc_validity_raises_when_bulk_download_fails() -> None:
    with (
        patch.object(
            gencc_tools, "_ensure_cached", AsyncMock(side_effect=MCPToolError("HTTP 503"))
        ),
        pytest.raises(MCPToolError, match="HTTP 503"),
    ):
        await get_gencc_validity("TRPC6")
