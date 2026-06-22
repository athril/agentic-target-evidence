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
from mcp_servers.orphanet.tools import (
    OrphanetBundle,
    OrphanetPrevalenceBundle,
    get_orphanet_associations,
    get_orphanet_prevalence,
)


@pytest.fixture(autouse=True)
def _reset_module_caches():
    orphanet_tools._index = None
    orphanet_tools._index_mtime = None
    orphanet_tools._prevalence_index = None
    orphanet_tools._prevalence_index_mtime = None
    yield
    orphanet_tools._index = None
    orphanet_tools._index_mtime = None
    orphanet_tools._prevalence_index = None
    orphanet_tools._prevalence_index_mtime = None


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


# ── Orphanet prevalence (product 9) ─────────────────────────────────────────


def _prevalence_disorder_xml(
    orphacode: str,
    name: str,
    prevalence_type: str,
    prevalence_class: str,
    geographic_area: str,
    validation_status: str,
) -> str:
    return f"""
    <Disorder id="{orphacode}">
      <OrphaCode>{orphacode}</OrphaCode>
      <Name lang="en">{name}</Name>
      <PrevalenceList count="1">
        <Prevalence id="1">
          <PrevalenceType id="1">
            <Name lang="en">{prevalence_type}</Name>
          </PrevalenceType>
          <PrevalenceClass id="1">
            <Name lang="en">{prevalence_class}</Name>
          </PrevalenceClass>
          <PrevalenceGeographic id="1">
            <Name lang="en">{geographic_area}</Name>
          </PrevalenceGeographic>
          <PrevalenceValidationStatus id="1">
            <Name lang="en">{validation_status}</Name>
          </PrevalenceValidationStatus>
        </Prevalence>
      </PrevalenceList>
    </Disorder>
    """


async def test_get_orphanet_prevalence_returns_bundle() -> None:
    xml_path = _make_xml(
        [
            _prevalence_disorder_xml(
                "166024",
                "Multiple sulfatase deficiency",
                "Point prevalence",
                "1-9 / 1 000 000",
                "Worldwide",
                "Validated",
            )
        ]
    )
    try:
        with _patch_ensure_cached(xml_path):
            bundle = await get_orphanet_prevalence(["166024"])

        assert isinstance(bundle, OrphanetPrevalenceBundle)
        assert bundle.total == 1
        record = bundle.records[0]
        assert record.orphacode == "166024"
        assert record.prevalence_class == "1-9 / 1 000 000"
        assert record.geographic_area == "Worldwide"
        assert record.validation_status == "Validated"
        assert "166024" in bundle.text
    finally:
        xml_path.unlink()


async def test_get_orphanet_prevalence_prefers_validated_records() -> None:
    xml_path = _make_xml(
        [
            _prevalence_disorder_xml(
                "166024",
                "Multiple sulfatase deficiency",
                "Point prevalence",
                "&lt;1 / 1 000 000",
                "Europe",
                "Not yet validated",
            ),
        ]
    )
    # Append a second, validated record for the same OrphaCode by editing the file directly.
    body = xml_path.read_text(encoding="utf-8")
    extra = _prevalence_disorder_xml(
        "166024",
        "Multiple sulfatase deficiency",
        "Point prevalence",
        "1-9 / 1 000 000",
        "Worldwide",
        "Validated",
    )
    body = body.replace("</DisorderList>", extra + "</DisorderList>")
    xml_path.write_text(body, encoding="utf-8")
    try:
        with _patch_ensure_cached(xml_path):
            bundle = await get_orphanet_prevalence(["166024"])

        assert bundle.total == 2
        assert bundle.records[0].validation_status == "Validated"
        assert bundle.records[0].geographic_area == "Worldwide"
    finally:
        xml_path.unlink()


async def test_get_orphanet_prevalence_empty_when_orphacode_not_in_dataset() -> None:
    xml_path = _make_xml(
        [
            _prevalence_disorder_xml(
                "1",
                "Some disorder",
                "Point prevalence",
                "1-9 / 100 000",
                "Worldwide",
                "Validated",
            )
        ]
    )
    try:
        with _patch_ensure_cached(xml_path):
            bundle = await get_orphanet_prevalence(["166024"])

        assert bundle.total == 0
        assert bundle.records == []
        assert "No Orphanet prevalence" in bundle.text
    finally:
        xml_path.unlink()


async def test_get_orphanet_prevalence_raises_on_malformed_xml() -> None:
    fd, name = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    path = Path(name)
    path.write_text("<JDBOR><Unclosed>", encoding="utf-8")
    try:
        with (
            _patch_ensure_cached(path),
            pytest.raises(MCPToolError, match="not valid XML"),
        ):
            await get_orphanet_prevalence(["166024"])
    finally:
        path.unlink()
