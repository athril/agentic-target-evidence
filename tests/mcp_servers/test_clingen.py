# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ClinGen gene validity MCP tools.

ClinGen's GraphQL API is gone; gene-disease validity now comes from a bulk
JSON-LD dataset (see mcp_servers/clingen/tools.py module docstring). These
tests fake that dataset as a small in-memory tar.gz and patch the module's
`_ensure_cached` boundary, mirroring the pattern used for DepMap's bulk CSVs
in test_depmap.py. HGNC/MONDO resolution (a separate, already-tested module)
is patched directly rather than re-mocking its HTTP calls.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import mcp_servers.clingen.tools as clingen_tools
from core.exceptions import MCPToolError
from mcp_servers.clingen.tools import ClinGenBundle, get_clingen_validity
from mcp_servers.ontology.tools import HGNCResult, MondoResult


@pytest.fixture(autouse=True)
def _reset_module_caches():
    clingen_tools._index = None
    clingen_tools._index_mtime = None
    clingen_tools._mondo_label_cache = {}
    yield
    clingen_tools._index = None
    clingen_tools._index_mtime = None
    clingen_tools._mondo_label_cache = {}


def _make_record(
    hgnc_id: str,
    classification: str,
    mondo_curie: str,
    evaluated_date: str | None = "2022-03-15T00:00:00.000Z",
    moi_curie: str | None = None,
) -> dict:
    proposition = {"objectCondition": f"obo:{mondo_curie.replace(':', '_')}"}
    if moi_curie:
        proposition["qualifierModeOfInheritance"] = f"obo:{moi_curie.replace(':', '_')}"
    return {
        "subject": hgnc_id.lower(),
        "classification": classification,
        "proposition": proposition,
        "contributions": (
            [{"activityType": "Evaluated", "date": evaluated_date}] if evaluated_date else []
        ),
    }


def _make_tar(records: dict[str, dict]) -> Path:
    fd, name = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    path = Path(name)
    with tarfile.open(path, "w:gz") as tf:
        for name, record in records.items():
            data = json.dumps(record).encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _patch_ensure_cached(tar_path: Path):
    return patch.object(clingen_tools, "_ensure_cached", AsyncMock(return_value=tar_path))


def _patch_hgnc(hgnc_id: str | None):
    if hgnc_id is None:
        return patch.object(
            clingen_tools, "resolve_hgnc_symbol", AsyncMock(side_effect=MCPToolError("not found"))
        )
    return patch.object(
        clingen_tools,
        "resolve_hgnc_symbol",
        AsyncMock(return_value=HGNCResult(symbol="TRPC6", hgnc_id=hgnc_id)),
    )


def _patch_mondo(labels: dict[str, str]):
    async def fake_resolve(curie: str) -> MondoResult:
        if curie not in labels:
            raise MCPToolError(f"No MONDO term found for '{curie}'")
        return MondoResult(mondo_id=curie, label=labels[curie])

    return patch.object(clingen_tools, "resolve_mondo_term", AsyncMock(side_effect=fake_resolve))


async def test_get_clingen_validity_returns_bundle() -> None:
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record("HGNC:12338", "Definitive", "MONDO:0001085"),
            "b--1.0.0.json": _make_record("HGNC:12338", "Limited", "MONDO:0005511"),
        }
    )
    try:
        with (
            _patch_hgnc("HGNC:12338"),
            _patch_ensure_cached(tar_path),
            _patch_mondo(
                {
                    "MONDO:0001085": "focal segmental glomerulosclerosis",
                    "MONDO:0005511": "nephrotic syndrome",
                }
            ),
        ):
            bundle = await get_clingen_validity("TRPC6")

        assert isinstance(bundle, ClinGenBundle)
        assert bundle.gene_symbol == "TRPC6"
        assert bundle.total == 2
        assert len(bundle.associations) == 2
        # Strongest classification should sort first
        assert bundle.associations[0].classification == "Definitive"
        assert bundle.associations[0].disease_label == "focal segmental glomerulosclerosis"
        assert bundle.associations[0].hgnc_id == "HGNC:12338"
        assert bundle.associations[0].report_date == "2022-03-15"
        assert "Definitive" in bundle.text
        assert "TRPC6" in bundle.text
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_empty_when_gene_not_in_dataset() -> None:
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record("HGNC:99999", "Definitive", "MONDO:0001085"),
        }
    )
    try:
        with _patch_hgnc("HGNC:12338"), _patch_ensure_cached(tar_path), _patch_mondo({}):
            bundle = await get_clingen_validity("TRPC6")

        assert bundle.total == 0
        assert bundle.associations == []
        assert "No ClinGen" in bundle.text
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_empty_when_symbol_not_resolvable() -> None:
    with _patch_hgnc(None):
        bundle = await get_clingen_validity("UNKNOWN")

    assert bundle.total == 0
    assert bundle.associations == []
    assert "No ClinGen" in bundle.text


async def test_get_clingen_validity_raises_when_bulk_download_fails() -> None:
    with (
        _patch_hgnc("HGNC:12338"),
        patch.object(
            clingen_tools, "_ensure_cached", AsyncMock(side_effect=MCPToolError("HTTP 503"))
        ),
        pytest.raises(MCPToolError, match="HTTP 503"),
    ):
        await get_clingen_validity("TRPC6")


async def test_get_clingen_validity_sorts_by_classification_strength() -> None:
    """Definitive > Strong > Moderate > Limited in sort order."""
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record("HGNC:1", "Limited", "MONDO:0000001"),
            "b--1.0.0.json": _make_record("HGNC:1", "Strong", "MONDO:0000002"),
            "c--1.0.0.json": _make_record("HGNC:1", "Moderate", "MONDO:0000003"),
        }
    )
    try:
        with _patch_hgnc("HGNC:1"), _patch_ensure_cached(tar_path), _patch_mondo({}):
            bundle = await get_clingen_validity("GENE1")

        classifications = [a.classification for a in bundle.associations]
        assert classifications == ["Strong", "Moderate", "Limited"]
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_normalizes_no_known_disease_relationship() -> None:
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record("HGNC:1", "NoKnownDiseaseRelationship", "MONDO:0000001"),
        }
    )
    try:
        with _patch_hgnc("HGNC:1"), _patch_ensure_cached(tar_path), _patch_mondo({}):
            bundle = await get_clingen_validity("GENE1")

        assert bundle.associations[0].classification == "No Known Disease Relationship"
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_parses_mode_of_inheritance() -> None:
    """proposition.qualifierModeOfInheritance ('obo:HP_0000006') must resolve to
    a readable label + the raw HPO curie on the association."""
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record(
                "HGNC:12338", "Definitive", "MONDO:0001085", moi_curie="HP:0000006"
            ),
        }
    )
    try:
        with (
            _patch_hgnc("HGNC:12338"),
            _patch_ensure_cached(tar_path),
            _patch_mondo(
                {
                    "MONDO:0001085": "focal segmental glomerulosclerosis",
                }
            ),
        ):
            bundle = await get_clingen_validity("TRPC6")

        assoc = bundle.associations[0]
        assert assoc.mode_of_inheritance == "Autosomal dominant"
        assert assoc.mode_of_inheritance_curie == "HP:0000006"
        assert "Autosomal dominant" in bundle.text
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_mode_of_inheritance_absent_when_not_in_record() -> None:
    tar_path = _make_tar(
        {
            "a--1.0.0.json": _make_record("HGNC:12338", "Definitive", "MONDO:0001085"),
        }
    )
    try:
        with (
            _patch_hgnc("HGNC:12338"),
            _patch_ensure_cached(tar_path),
            _patch_mondo(
                {
                    "MONDO:0001085": "focal segmental glomerulosclerosis",
                }
            ),
        ):
            bundle = await get_clingen_validity("TRPC6")

        assert bundle.associations[0].mode_of_inheritance is None
    finally:
        tar_path.unlink()


async def test_get_clingen_validity_skips_digenic_multi_subject_records_safely() -> None:
    """Records with a list 'subject' (digenic) should not crash indexing; single-gene
    records in the same dataset are still found."""
    tar_path = _make_tar(
        {
            "digenic--1.0.0.json": {
                "subject": ["hgnc:1", "hgnc:2"],
                "classification": "Limited",
                "proposition": {"objectCondition": "obo:MONDO_0000009"},
                "contributions": [],
            },
            "single--1.0.0.json": _make_record("HGNC:1", "Definitive", "MONDO:0000001"),
        }
    )
    try:
        with _patch_hgnc("HGNC:1"), _patch_ensure_cached(tar_path), _patch_mondo({}):
            bundle = await get_clingen_validity("GENE1")

        assert bundle.total == 2
        classifications = {a.classification for a in bundle.associations}
        assert classifications == {"Definitive", "Limited"}
    finally:
        tar_path.unlink()
