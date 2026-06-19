# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for artifact_store path layout."""

from __future__ import annotations

from pathlib import Path

from core.persistence.artifact_store import (
    _safe_id,
    archive_raw,
    export_summary_csv,
)


class TestSafeId:
    def test_replaces_slash(self) -> None:
        assert _safe_id("EFO/0000305") == "EFO_0000305"

    def test_passthrough_already_safe(self) -> None:
        assert _safe_id("EFO_0000305") == "EFO_0000305"

    def test_empty_string_returns_unknown(self) -> None:
        assert _safe_id("") == "_unknown"


class TestArchiveRaw:
    def test_path_includes_disease_id(self, tmp_path: Path) -> None:
        uri = archive_raw(
            "BRCA1",
            "EFO_0000305",
            "papers",
            "12345.md",
            "content",
            results_root=tmp_path,
        )
        expected = tmp_path / "original" / "BRCA1" / "EFO_0000305" / "papers" / "12345.md"
        assert expected.exists()
        assert uri == f"file://{expected.resolve()}"

    def test_slash_in_disease_id_is_sanitised(self, tmp_path: Path) -> None:
        archive_raw(
            "BRCA1",
            "EFO/0000305",
            "papers",
            "12345.md",
            "content",
            results_root=tmp_path,
        )
        expected = tmp_path / "original" / "BRCA1" / "EFO_0000305" / "papers" / "12345.md"
        assert expected.exists()

    def test_different_diseases_do_not_collide(self, tmp_path: Path) -> None:
        archive_raw("BRCA1", "EFO_0000305", "papers", "1.md", "breast", results_root=tmp_path)
        archive_raw("BRCA1", "EFO_1000048", "papers", "1.md", "ovarian", results_root=tmp_path)
        breast = (tmp_path / "original" / "BRCA1" / "EFO_0000305" / "papers" / "1.md").read_text()
        ovarian = (tmp_path / "original" / "BRCA1" / "EFO_1000048" / "papers" / "1.md").read_text()
        assert breast == "breast"
        assert ovarian == "ovarian"

    def test_content_is_written(self, tmp_path: Path) -> None:
        archive_raw(
            "TP53", "EFO_0000305", "genetics", "tp53.json", '{"x":1}', results_root=tmp_path
        )
        path = tmp_path / "original" / "TP53" / "EFO_0000305" / "genetics" / "tp53.json"
        assert path.read_text() == '{"x":1}'


class TestExportSummaryCsv:
    def test_path_includes_disease_id(self, tmp_path: Path) -> None:
        uri = export_summary_csv("BRCA1", "EFO_0000305", [], results_root=tmp_path)
        expected = tmp_path / "data" / "BRCA1" / "EFO_0000305" / "summary.csv"
        assert expected.exists()
        assert uri == f"file://{expected.resolve()}"

    def test_two_diseases_produce_separate_csvs(self, tmp_path: Path) -> None:
        export_summary_csv("BRCA1", "EFO_0000305", [], results_root=tmp_path)
        export_summary_csv("BRCA1", "EFO_1000048", [], results_root=tmp_path)
        assert (tmp_path / "data" / "BRCA1" / "EFO_0000305" / "summary.csv").exists()
        assert (tmp_path / "data" / "BRCA1" / "EFO_1000048" / "summary.csv").exists()

    def test_csv_has_header_with_gene_and_disease_id(self, tmp_path: Path) -> None:
        export_summary_csv("BRCA1", "EFO_0000305", [], results_root=tmp_path)
        header = (
            (tmp_path / "data" / "BRCA1" / "EFO_0000305" / "summary.csv")
            .read_text()
            .splitlines()[0]
        )
        assert "gene" in header
        assert "gene_id" in header
        assert "disease_id" in header
