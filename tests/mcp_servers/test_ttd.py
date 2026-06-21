# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for TTD (Therapeutic Target Database) target development-status tools.

TTD's bulk per-target text file is faked as a small temp file and the module's
`_ensure_cached` boundary is patched, mirroring the pattern used for OMIM's
bulk `genemap2.txt` in test_omim.py. TTD is gated behind `TTD_ENABLED` (its
commercial-use terms are unconfirmed — see tools.py module docstring), so a
separate test confirms it degrades to an empty bundle, not an exception, when
disabled.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import mcp_servers.ttd.tools as ttd_tools
from core.exceptions import MCPToolError
from mcp_servers.ttd.tools import TtdBundle, get_ttd_target_status


@pytest.fixture(autouse=True)
def _reset_module_caches(monkeypatch):
    # TTD's commercial-use terms are unconfirmed, so it's off by default; enable it
    # so these tests exercise the lookup path. The disabled path has its own test.
    monkeypatch.setenv("TTD_ENABLED", "true")
    ttd_tools._index = None
    ttd_tools._index_mtime = None
    yield
    ttd_tools._index = None
    ttd_tools._index_mtime = None


def _make_bulk_file(blocks: list[dict[str, str | list[tuple[str, str]]]]) -> Path:
    """blocks: list of dicts of plain fields plus an optional 'drugs' list of (id, name)."""
    fd, name = tempfile.mkstemp(suffix=".txt")
    import os

    os.close(fd)
    path = Path(name)
    lines: list[str] = []
    for block in blocks:
        drugs = block.pop("drugs", [])
        for key, value in block.items():
            lines.append(f"{key}\t{value}")
        for drug_id, drug_name in drugs:  # type: ignore[union-attr]
            lines.append(f"DRUGINFO\t{drug_id}\t{drug_name}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _patch_ensure_cached(path: Path):
    return patch.object(ttd_tools, "_ensure_cached", AsyncMock(return_value=path))


async def test_get_ttd_target_status_returns_empty_bundle_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("TTD_ENABLED", "false")
    bundle = await get_ttd_target_status("EGFR")
    assert bundle.record is None
    assert "disabled" in bundle.text.lower()


async def test_get_ttd_target_status_returns_bundle() -> None:
    path = _make_bulk_file(
        [
            {
                "TARGETID": "T47101",
                "GENENAME": "EGFR",
                "TARGNAME": "Epidermal growth factor receptor",
                "UNIPROID": "P00533",
                "TARGTYPE": "Successful target",
                "drugs": [("D0K7QN", "Cetuximab"), ("D08TWS", "Erlotinib")],
            }
        ]
    )
    try:
        with _patch_ensure_cached(path):
            bundle = await get_ttd_target_status("EGFR")

        assert isinstance(bundle, TtdBundle)
        assert bundle.record is not None
        assert bundle.record.ttd_target_id == "T47101"
        assert bundle.record.development_status == "Successful target"
        assert bundle.record.uniprot_id == "P00533"
        assert len(bundle.record.drugs) == 2
        assert {d.drug_name for d in bundle.record.drugs} == {"Cetuximab", "Erlotinib"}
        assert "EGFR" in bundle.text
        assert "Successful target" in bundle.text
    finally:
        path.unlink()


async def test_get_ttd_target_status_empty_when_gene_not_in_dataset() -> None:
    path = _make_bulk_file(
        [
            {
                "TARGETID": "T00001",
                "GENENAME": "OTHERGENE",
                "TARGTYPE": "Research target",
            }
        ]
    )
    try:
        with _patch_ensure_cached(path):
            bundle = await get_ttd_target_status("EGFR")

        assert bundle.record is None
        assert "No TTD" in bundle.text
    finally:
        path.unlink()


async def test_get_ttd_target_status_raises_when_bulk_download_fails() -> None:
    with (
        patch.object(ttd_tools, "_ensure_cached", AsyncMock(side_effect=MCPToolError("HTTP 503"))),
        pytest.raises(MCPToolError, match="HTTP 503"),
    ):
        await get_ttd_target_status("EGFR")
