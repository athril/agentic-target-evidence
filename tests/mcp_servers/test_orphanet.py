# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for Orphanet gene-disease association MCP tools.

Orphadata publishes a bulk XML cross-reference (product 6). These tests fake
that file as a small temp XML document and patch the module's
`_ensure_cached` boundary, mirroring the pattern used for ClinGen's bulk
tar.gz in test_clingen.py.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import mcp_servers.orphanet.tools as orphanet_tools
from core.exceptions import MCPToolError
from mcp_servers.orphanet.tools import OrphanetBundle, get_orphanet_associations


@pytest.fixture(autouse=True)
def _reset_module_caches():
    orphanet_tools._index = None
    orphanet_tools._index_mtime = None
    yield
    orphanet_tools._index = None
    orphanet_tools._index_mtime = None


def _disorder_xml(
    orphacode: str,
    name: str,
    gene_symbol: str,
    assoc_type: str,
    assoc_status: str,
) -> str:
    return f"""
    <Disorder id="{orphacode}">
      <OrphaCode>{orphacode}</OrphaCode>
      <Name lang="en">{name}</Name>
      <DisorderGeneAssociationList count="1">
        <DisorderGeneAssociation>
          <Gene id="1">
            <Symbol>{gene_symbol}</Symbol>
          </Gene>
          <DisorderGeneAssociationType id="1">
            <Name lang="en">{assoc_type}</Name>
          </DisorderGeneAssociationType>
          <DisorderGeneAssociationStatus id="1">
            <Name lang="en">{assoc_status}</Name>
          </DisorderGeneAssociationStatus>
        </DisorderGeneAssociation>
      </DisorderGeneAssociationList>
    </Disorder>
    """


def _make_xml(disorders_xml: list[str]) -> Path:
    fd, name = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    path = Path(name)
    body = "\n".join(disorders_xml)
    path.write_text(f"<JDBOR><DisorderList>{body}</DisorderList></JDBOR>", encoding="utf-8")
    return path


def _patch_ensure_cached(xml_path: Path):
    return patch.object(orphanet_tools, "_ensure_cached", AsyncMock(return_value=xml_path))


async def test_get_orphanet_associations_returns_bundle() -> None:
    xml_path = _make_xml(
        [
            _disorder_xml(
                "166024",
                "Multiple sulfatase deficiency",
                "SUMF1",
                "Disease-causing germline mutation(s) in",
                "Assessed",
            ),
            _disorder_xml(
                "99999",
                "Some susceptibility disorder",
                "SUMF1",
                "Major susceptibility factor in",
                "Not yet assessed",
            ),
        ]
    )
    try:
        with _patch_ensure_cached(xml_path):
            bundle = await get_orphanet_associations("SUMF1")

        assert isinstance(bundle, OrphanetBundle)
        assert bundle.total == 2
        # "Assessed" sorts before "Not yet assessed"
        assert bundle.associations[0].association_status == "Assessed"
        assert bundle.associations[0].orphacode == "166024"
        assert bundle.associations[0].association_type == "Disease-causing germline mutation(s) in"
        assert "SUMF1" in bundle.text
    finally:
        xml_path.unlink()


async def test_get_orphanet_associations_empty_when_gene_not_in_dataset() -> None:
    xml_path = _make_xml(
        [
            _disorder_xml(
                "1",
                "Some disorder",
                "OTHERGENE",
                "Disease-causing germline mutation(s) in",
                "Assessed",
            )
        ]
    )
    try:
        with _patch_ensure_cached(xml_path):
            bundle = await get_orphanet_associations("SUMF1")

        assert bundle.total == 0
        assert bundle.associations == []
        assert "No Orphanet" in bundle.text
    finally:
        xml_path.unlink()


async def test_get_orphanet_associations_raises_on_malformed_xml() -> None:
    fd, name = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    path = Path(name)
    path.write_text("<JDBOR><Unclosed>", encoding="utf-8")
    try:
        with (
            _patch_ensure_cached(path),
            pytest.raises(MCPToolError, match="not valid XML"),
        ):
            await get_orphanet_associations("SUMF1")
    finally:
        path.unlink()


async def test_get_orphanet_associations_raises_when_bulk_download_fails() -> None:
    with (
        patch.object(
            orphanet_tools, "_ensure_cached", AsyncMock(side_effect=MCPToolError("HTTP 503"))
        ),
        pytest.raises(MCPToolError, match="HTTP 503"),
    ):
        await get_orphanet_associations("SUMF1")
