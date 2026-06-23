# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for artifact_store path layout."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from core.persistence.artifact_store import (
    _safe_direction,
    _safe_id,
    archive_raw,
    export_summary_csv,
)
from core.persistence.models import EvidenceRow


def _row(**overrides: object) -> EvidenceRow:
    defaults: dict = {
        "evidence_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "gene": "BRCA1",
        "gene_id": "ENSG00000012048",
        "disease": "Breast Cancer",
        "disease_id": "EFO_0000305",
        "direction": "inhibit",
        "evidence_type": "article",
        "scope": "abstract",
        "source": "PMID:1",
        "source_link": "https://pubmed.ncbi.nlm.nih.gov/1/",
        "classification": "NON_SENSITIVE",
        "prov_agent_name": "literature",
        "prov_timestamp": datetime.now(UTC),
        "prov_trace_id": "trace-1",
        "extra": {},
    }
    defaults.update(overrides)
    return EvidenceRow(**defaults)


class TestSafeId:
    def test_replaces_slash(self) -> None:
        assert _safe_id("EFO/0000305") == "EFO_0000305"

    def test_passthrough_already_safe(self) -> None:
        assert _safe_id("EFO_0000305") == "EFO_0000305"

    def test_empty_string_returns_unknown(self) -> None:
        assert _safe_id("") == "_unknown"


class TestSafeDirection:
    def test_passthrough(self) -> None:
        assert _safe_direction("inhibit") == "inhibit"

    def test_none_defaults_to_unspecified(self) -> None:
        assert _safe_direction(None) == "unspecified"

    def test_empty_defaults_to_unspecified(self) -> None:
        assert _safe_direction("") == "unspecified"


class TestArchiveRaw:
    def test_path_includes_direction(self, tmp_path: Path) -> None:
        uri = archive_raw(
            "BRCA1",
            "EFO_0000305",
            "inhibit",
            "papers",
            "12345.md",
            "content",
            results_root=tmp_path,
        )
        expected = tmp_path / "data" / "BRCA1" / "EFO_0000305" / "inhibit" / "papers" / "12345.md"
        assert expected.exists()
        assert uri == f"file://{expected.resolve()}"

    def test_slash_in_disease_id_is_sanitised(self, tmp_path: Path) -> None:
        archive_raw(
            "BRCA1",
            "EFO/0000305",
            "inhibit",
            "papers",
            "12345.md",
            "content",
            results_root=tmp_path,
        )
        expected = tmp_path / "data" / "BRCA1" / "EFO_0000305" / "inhibit" / "papers" / "12345.md"
        assert expected.exists()

    def test_missing_direction_defaults_to_unspecified(self, tmp_path: Path) -> None:
        archive_raw("BRCA1", "EFO_0000305", "", "papers", "1.md", "x", results_root=tmp_path)
        expected = tmp_path / "data" / "BRCA1" / "EFO_0000305" / "unspecified" / "papers" / "1.md"
        assert expected.exists()

    def test_different_directions_do_not_collide(self, tmp_path: Path) -> None:
        archive_raw(
            "BRCA1", "EFO_0000305", "inhibit", "papers", "1.md", "inh", results_root=tmp_path
        )
        archive_raw(
            "BRCA1", "EFO_0000305", "activate", "papers", "1.md", "act", results_root=tmp_path
        )
        inh = (
            tmp_path / "data" / "BRCA1" / "EFO_0000305" / "inhibit" / "papers" / "1.md"
        ).read_text()
        act = (
            tmp_path / "data" / "BRCA1" / "EFO_0000305" / "activate" / "papers" / "1.md"
        ).read_text()
        assert inh == "inh"
        assert act == "act"

    def test_content_is_written(self, tmp_path: Path) -> None:
        archive_raw(
            "TP53",
            "EFO_0000305",
            "inhibit",
            "genetics",
            "tp53.json",
            '{"x":1}',
            results_root=tmp_path,
        )
        path = tmp_path / "data" / "TP53" / "EFO_0000305" / "inhibit" / "genetics" / "tp53.json"
        assert path.read_text() == '{"x":1}'


class TestExportSummaryCsv:
    def test_path_includes_direction(self, tmp_path: Path) -> None:
        uri = export_summary_csv("BRCA1", "EFO_0000305", "inhibit", [], results_root=tmp_path)
        expected = tmp_path / "data" / "BRCA1" / "EFO_0000305" / "inhibit" / "summary.csv"
        assert expected.exists()
        assert uri == f"file://{expected.resolve()}"

    def test_two_directions_produce_separate_csvs(self, tmp_path: Path) -> None:
        export_summary_csv("BRCA1", "EFO_0000305", "inhibit", [], results_root=tmp_path)
        export_summary_csv("BRCA1", "EFO_0000305", "activate", [], results_root=tmp_path)
        assert (tmp_path / "data" / "BRCA1" / "EFO_0000305" / "inhibit" / "summary.csv").exists()
        assert (tmp_path / "data" / "BRCA1" / "EFO_0000305" / "activate" / "summary.csv").exists()

    def test_comment_header_carries_run_constant_fields(self, tmp_path: Path) -> None:
        export_summary_csv("BRCA1", "EFO_0000305", "inhibit", [_row()], results_root=tmp_path)
        comment = (
            (tmp_path / "data" / "BRCA1" / "EFO_0000305" / "inhibit" / "summary.csv")
            .read_text()
            .splitlines()[0]
        )
        assert comment.startswith("# ")
        assert "gene=BRCA1" in comment
        assert "gene_id=ENSG00000012048" in comment
        assert "disease_id=EFO_0000305" in comment
        assert "direction=inhibit" in comment

    def test_data_rows_omit_run_constant_columns(self, tmp_path: Path) -> None:
        export_summary_csv("BRCA1", "EFO_0000305", "inhibit", [_row()], results_root=tmp_path)
        header_row = (
            (tmp_path / "data" / "BRCA1" / "EFO_0000305" / "inhibit" / "summary.csv")
            .read_text()
            .splitlines()[1]
        )
        assert "gene_id" not in header_row
        assert "run_id" not in header_row
        assert "evidence_type" in header_row
        assert "source" in header_row

    def test_rows_sorted_by_evidence_type_then_source(self, tmp_path: Path) -> None:
        rows = [
            _row(evidence_type="patent", source="z-patent"),
            _row(evidence_type="article", source="b-pmid"),
            _row(evidence_type="article", source="a-pmid"),
        ]
        export_summary_csv("BRCA1", "EFO_0000305", "inhibit", rows, results_root=tmp_path)
        lines = (
            (tmp_path / "data" / "BRCA1" / "EFO_0000305" / "inhibit" / "summary.csv")
            .read_text()
            .splitlines()[2:]
        )
        sources = [line.split(",")[1] for line in lines]
        assert sources == ["a-pmid", "b-pmid", "z-patent"]

    def test_artifact_uri_relativized_to_csv_directory(self, tmp_path: Path) -> None:
        archive_uri = archive_raw(
            "BRCA1",
            "EFO_0000305",
            "inhibit",
            "papers",
            "1.md",
            "content",
            results_root=tmp_path,
        )
        rows = [_row(artifact_uri=archive_uri)]
        export_summary_csv("BRCA1", "EFO_0000305", "inhibit", rows, results_root=tmp_path)
        lines = (
            (tmp_path / "data" / "BRCA1" / "EFO_0000305" / "inhibit" / "summary.csv")
            .read_text()
            .splitlines()
        )
        data_row = lines[2]
        assert "file://" not in data_row
        assert "papers/1.md" in data_row
