# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for OMIM Mendelian phenotype-gene association MCP tools.

OMIM's bulk `genemap2.txt` export is faked as a small temp tab-delimited file
and the module's `_ensure_cached` boundary is patched, mirroring the pattern
used for ClinGen's bulk tar.gz in test_clingen.py. OMIM is an *optional*
source (requires a registered, free academic API key) — a separate set of
tests confirms it degrades to an empty bundle, not an exception, when
`OMIM_API_KEY` is unset.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import mcp_servers.omim.tools as omim_tools
from core.exceptions import MCPToolError
from mcp_servers.omim.tools import OmimBundle, get_omim_validity

_HEADER = (
    "# Chromosome\tGenomic Position Start\tGenomic Position End\tCyto Location\t"
    "Computed Cyto Location\tMIM Number\tGene Symbols\tGene Name\t"
    "Approved Gene Symbol\tEntrez Gene ID\tEnsembl Gene ID\tComments\tPhenotypes\t"
    "Mouse Gene Symbol/ID"
)


@pytest.fixture(autouse=True)
def _reset_module_caches(monkeypatch):
    # OMIM is gated behind OMIM_ENABLED (non-commercial license, off by default);
    # enable it so these tests exercise the lookup path. The disabled path has its
    # own dedicated test below.
    monkeypatch.setenv("OMIM_ENABLED", "true")
    omim_tools._index = None
    omim_tools._index_mtime = None
    yield
    omim_tools._index = None
    omim_tools._index_mtime = None


async def test_get_omim_validity_returns_empty_bundle_when_disabled(monkeypatch) -> None:
    # Default/commercial posture: even with a key present, OMIM stays off.
    monkeypatch.setenv("OMIM_ENABLED", "false")
    monkeypatch.setenv("OMIM_API_KEY", "testkey")
    bundle = await get_omim_validity("LDLR")
    assert bundle.total == 0
    assert bundle.associations == []
    assert "disabled" in bundle.text.lower()


def _make_genemap2(rows: list[tuple[str, str]]) -> Path:
    """rows: list of (approved_gene_symbol, phenotypes_field)."""
    fd, name = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    path = Path(name)
    lines = ["# comment line", _HEADER]
    for symbol, phenotypes in rows:
        cols = [
            "1",
            "1",
            "2",
            "1p1",
            "1p1",
            "100000",
            symbol,
            "Gene name",
            symbol,
            "1",
            "ENSG1",
            "",
            phenotypes,
            "",
        ]
        lines.append("\t".join(cols))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _patch_ensure_cached(path: Path):
    return patch.object(omim_tools, "_ensure_cached", AsyncMock(return_value=path))


async def test_get_omim_validity_returns_empty_bundle_when_key_unset() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OMIM_API_KEY", None)
        bundle = await get_omim_validity("LDLR")

    assert isinstance(bundle, OmimBundle)
    assert bundle.total == 0
    assert bundle.associations == []
    assert "not configured" in bundle.text.lower()


async def test_get_omim_validity_returns_bundle() -> None:
    phenotypes = (
        "?Disputed phenotype, 100001 (1), Autosomal recessive; "
        "Linked phenotype, 100002 (2); "
        "Hypercholesterolemia, familial, 143890 (3), Autosomal dominant"
    )
    path = _make_genemap2([("LDLR", phenotypes)])
    try:
        with (
            patch.dict(os.environ, {"OMIM_API_KEY": "testkey"}),
            _patch_ensure_cached(path),
        ):
            bundle = await get_omim_validity("LDLR")

        assert bundle.total == 3
        # Molecularly confirmed (3) sorts first
        assert bundle.associations[0].phenotype_label == "Hypercholesterolemia, familial"
        assert bundle.associations[0].mim_number == "143890"
        assert bundle.associations[0].mapping_confidence == "molecularly confirmed"
        assert bundle.associations[0].inheritance == "Autosomal dominant"
        assert bundle.associations[0].provisional is False
        # Disputed (1) sorts last and is flagged provisional
        assert bundle.associations[-1].provisional is True
        assert "Hypercholesterolemia" in bundle.text
    finally:
        path.unlink()


async def test_get_omim_validity_empty_when_gene_not_in_dataset() -> None:
    path = _make_genemap2([("OTHERGENE", "Some disease, 100003 (3)")])
    try:
        with (
            patch.dict(os.environ, {"OMIM_API_KEY": "testkey"}),
            _patch_ensure_cached(path),
        ):
            bundle = await get_omim_validity("LDLR")

        assert bundle.total == 0
        assert "No OMIM" in bundle.text
    finally:
        path.unlink()


async def test_get_omim_validity_skips_unparseable_phenotype_entries() -> None:
    path = _make_genemap2([("LDLR", "Just a gene name with no MIM number")])
    try:
        with (
            patch.dict(os.environ, {"OMIM_API_KEY": "testkey"}),
            _patch_ensure_cached(path),
        ):
            bundle = await get_omim_validity("LDLR")

        assert bundle.total == 0
    finally:
        path.unlink()


async def test_get_omim_validity_raises_when_bulk_download_fails() -> None:
    with (
        patch.dict(os.environ, {"OMIM_API_KEY": "testkey"}),
        patch.object(omim_tools, "_ensure_cached", AsyncMock(side_effect=MCPToolError("HTTP 503"))),
        pytest.raises(MCPToolError, match="HTTP 503"),
    ):
        await get_omim_validity("LDLR")
