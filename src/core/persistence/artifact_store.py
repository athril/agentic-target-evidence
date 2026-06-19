# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import csv
import os
from pathlib import Path

from core.persistence.models import EvidenceRow

_RESULTS_ROOT = Path(os.getenv("RESULTS_DIR", "./results"))

_CSV_FIELDS = [
    "evidence_id",
    "run_id",
    "schema_version",
    "gene",
    "gene_id",
    "disease",
    "disease_id",
    "population",
    "evidence_type",
    "scope",
    "source",
    "source_link",
    "query_used",
    "artifact_uri",
    "classification",
    "screening_verdict",
    "screening_rationale",
    "prov_agent_name",
    "prov_tool_name",
    "prov_timestamp",
    "prov_model_used",
    "prov_trace_id",
]


def _safe_id(disease_id: str) -> str:
    """Return a filesystem-safe version of an EFO/MONDO ID (replaces '/' with '_')."""
    return disease_id.replace("/", "_") if disease_id else "_unknown"


def archive_raw(
    gene: str,
    disease_id: str,
    source_type: str,
    filename: str,
    content: str,
    results_root: Path | None = None,
) -> str:
    """Write content to results/original/<gene>/<disease_id>/<source_type>/<filename> and return a file:// URI."""
    root = results_root or _RESULTS_ROOT
    dest = root / "original" / gene / _safe_id(disease_id) / source_type / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return f"file://{dest.resolve()}"


def export_summary_csv(
    gene: str,
    disease_id: str,
    rows: list[EvidenceRow],
    results_root: Path | None = None,
) -> str:
    """Write evidence rows to results/data/<gene>/<disease_id>/summary.csv and return a file:// URI."""
    root = results_root or _RESULTS_ROOT
    dest = root / "data" / gene / _safe_id(disease_id) / "summary.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            extra = row.extra or {}
            sv = extra.get("screening_verdict") or {}
            record = {f: str(getattr(row, f, "") or "") for f in _CSV_FIELDS}
            record["screening_verdict"] = sv.get("verdict", "")
            record["screening_rationale"] = sv.get("rationale", "")
            writer.writerow(record)
    return f"file://{dest.resolve()}"
