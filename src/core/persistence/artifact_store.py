# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import csv
import os
from datetime import UTC, datetime
from pathlib import Path

from core.persistence.models import EvidenceRow

_RESULTS_ROOT = Path(os.getenv("RESULTS_DIR", "./results"))

# Run-constant fields hoisted into the leading "# key=value ..." comment line rather
# than repeated on every row.
_CSV_HEADER_FIELDS = [
    "gene",
    "gene_id",
    "disease",
    "disease_id",
    "direction",
    "run_id",
    "schema_version",
]

_CSV_FIELDS = [
    "evidence_type",
    "source",
    "classification",
    "screening_verdict",
    "screening_rationale",
    "source_link",
    "artifact_uri",
    "query_used",
    "population",
    "prov_agent_name",
    "prov_tool_name",
    "prov_timestamp",
    "prov_model_used",
    "evidence_id",
]


def _safe_id(disease_id: str) -> str:
    """Return a filesystem-safe version of an EFO/MONDO ID (replaces '/' with '_')."""
    return disease_id.replace("/", "_") if disease_id else "_unknown"


def _safe_direction(direction: str | None) -> str:
    """Return a filesystem-safe direction segment, defaulting to 'unspecified'."""
    return (direction or "unspecified").replace("/", "_")


def archive_raw(
    gene: str,
    disease_id: str,
    direction: str,
    source_type: str,
    filename: str,
    content: str,
    results_root: Path | None = None,
) -> str:
    """Write content to results/data/<gene>/<disease_id>/<direction>/<source_type>/<filename>

    Returns a file:// URI. This is the durable raw-source archive: nothing in this
    codebase deletes from it or treats it as a regenerable cache.
    """
    root = results_root or _RESULTS_ROOT
    dest = (
        root
        / "data"
        / gene
        / _safe_id(disease_id)
        / _safe_direction(direction)
        / source_type
        / filename
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return f"file://{dest.resolve()}"


def _relativize_artifact_uri(uri: str, base_dir: Path) -> str:
    """Shorten a local file:// artifact_uri to a path relative to base_dir.

    Falls back to the original URI unchanged when it isn't a local path under
    base_dir (e.g. a future s3:// URI, or an artifact written outside this tree).
    """
    if not uri.startswith("file://"):
        return uri
    try:
        return str(Path(uri.removeprefix("file://")).resolve().relative_to(base_dir.resolve()))
    except ValueError:
        return uri


def export_summary_csv(
    gene: str,
    disease_id: str,
    direction: str,
    rows: list[EvidenceRow],
    results_root: Path | None = None,
) -> str:
    """Write evidence rows to results/data/<gene>/<disease_id>/<direction>/summary.csv.

    Run-constant fields (gene, disease, ids, run_id, schema_version) are hoisted into
    a single leading "# key=value ..." comment line instead of repeated per row.
    Rows are sorted by (evidence_type, source) and artifact_uri is relativized to this
    file's own directory. Returns a file:// URI.
    """
    root = results_root or _RESULTS_ROOT
    base_dir = root / "data" / gene / _safe_id(disease_id) / _safe_direction(direction)
    dest = base_dir / "summary.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)

    ordered = sorted(rows, key=lambda r: (str(r.evidence_type), str(r.source)))
    generated = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    with dest.open("w", newline="", encoding="utf-8") as fh:
        header = {f: getattr(rows[0], f, "") if rows else "" for f in _CSV_HEADER_FIELDS}
        header["direction"] = direction
        comment = (
            " ".join(f"{k}={header[k]!s}" for k in _CSV_HEADER_FIELDS) + f" generated={generated}"
        )
        fh.write(f"# {comment}\n")

        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in ordered:
            extra = row.extra or {}
            sv = extra.get("screening_verdict") or {}
            record = {f: str(getattr(row, f, "") or "") for f in _CSV_FIELDS}
            record["screening_verdict"] = sv.get("verdict", "")
            record["screening_rationale"] = sv.get("rationale", "")
            if record["artifact_uri"]:
                record["artifact_uri"] = _relativize_artifact_uri(record["artifact_uri"], base_dir)
            writer.writerow(record)
    return f"file://{dest.resolve()}"
