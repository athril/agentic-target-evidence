# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared data contracts for the gene-dossier skill.

Every script in this folder reads/writes the same run directory:

    <run_dir>/
      manifest.json      # identity + per-source retrieval status (written by gather.py)
      sources.json       # citable sources, keyed by citation key
      raw/<key>.json     # one RawRecord per retrieval — the system of record for numbers
      digest.md          # compact, truncated view of raw/ for the model to read
      draft.md           # model-written body with [@key] citations and {{key:path}} anchors
      <GENE>-report.md   # rendered dossier (render.py)
      <GENE>-report.pdf  # optional PDF (render.py --pdf)
      check_report.json  # deterministic check results (check_dossier.py)

Named ``dossier_schema`` (not ``schemas``) so it never shadows the repo's ``schemas`` package
when a script's folder is first on ``sys.path``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceKind = Literal["database", "article", "trial", "patent", "label", "web"]
RetrievalStatus = Literal["ok", "empty", "error", "skipped"]

# Ordered (key, heading) pairs. The draft must contain every heading except
# "indication" (required only when a disease was given).
SECTIONS: list[tuple[str, str]] = [
    ("snapshot", "Snapshot"),
    ("identity", "Identity & nomenclature"),
    ("biology", "Biology"),
    ("genetics", "Human genetics"),
    ("associations", "Disease associations"),
    ("druggability", "Druggability & modalities"),
    ("clinical", "Clinical landscape & pipeline"),
    ("safety", "Safety considerations"),
    ("commercial", "Commercial & IP"),
    ("regulatory", "Regulatory"),
    ("literature", "Key literature"),
    ("indication", "Indication fit"),
    ("gaps", "Evidence gaps & conflicts"),
]
SECTION_TITLES = dict(SECTIONS)

CITE_GROUP_RE = re.compile(r"\[(@[^\[\]]+)\]")
CITE_KEY_RE = re.compile(r"@([A-Za-z0-9_:.\-]+)")
# {{key:path|fmt}} — path segments may contain spaces (HPA field names), not '|', '{' or '}'.
ANCHOR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_:.\-]+)\s*:\s*([^|{}]+?)\s*(?:\|\s*([^}]*?))?\s*\}\}")


class Source(BaseModel):
    """One citable source. Metadata always comes from an API response, never typed by hand."""

    key: str
    kind: SourceKind
    tier: Literal[1, 2, 3] = 1
    title: str
    url: str
    accessed: str  # ISO date
    db_name: str = ""
    db_version: str = ""
    accession: str = ""
    authors: list[str] = Field(default_factory=list)
    venue: str = ""
    year: int | None = None
    pmid: str = ""
    doi: str = ""
    nct_id: str = ""
    patent_number: str = ""
    retrieved_via: str = ""
    verified: bool = False  # True when metadata was fetched from an authoritative API
    raw_file: str = ""


class RawRecord(BaseModel):
    key: str
    section: str
    db_name: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: RetrievalStatus
    note: str = ""
    retrieved_at: str
    url: str = ""
    data: Any = None


class ManifestEntry(BaseModel):
    key: str
    section: str
    db_name: str
    status: RetrievalStatus
    note: str = ""


class Manifest(BaseModel):
    query: str
    gene: str
    hgnc_id: str = ""
    ensembl_id: str = ""
    entrez_id: str = ""
    uniprot: str = ""
    disease: str = ""
    disease_id: str = ""
    depth: str = "standard"
    generated_at: str
    identity_note: str = ""
    db_versions: dict[str, str] = Field(default_factory=dict)
    entries: list[ManifestEntry] = Field(default_factory=list)


# --------------------------------------------------------------------------- io


def load_sources(run_dir: Path) -> dict[str, Source]:
    path = run_dir / "sources.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text())
    return {r["key"]: Source.model_validate(r) for r in rows}


def save_sources(run_dir: Path, sources: dict[str, Source]) -> None:
    rows = [s.model_dump() for s in sorted(sources.values(), key=lambda s: s.key)]
    (run_dir / "sources.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))


def load_manifest(run_dir: Path) -> Manifest:
    return Manifest.model_validate_json((run_dir / "manifest.json").read_text())


def load_raw(run_dir: Path, key: str) -> RawRecord | None:
    path = run_dir / "raw" / f"{safe_filename(key)}.json"
    if not path.exists():
        return None
    return RawRecord.model_validate_json(path.read_text())


def safe_filename(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", key)


# ------------------------------------------------------------------- anchors


_MISSING = object()


def get_path(data: Any, path: str) -> Any:
    """Resolve a dotted path (``rows.0.score``; ``@`` = the whole value). _MISSING if absent."""
    if path == "@":
        return data
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return _MISSING
            cur = cur[part]
        elif isinstance(cur, list):
            if not part.lstrip("-").isdigit():
                return _MISSING
            idx = int(part)
            if not -len(cur) <= idx < len(cur):
                return _MISSING
            cur = cur[idx]
        else:
            return _MISSING
    return cur


def format_value(value: Any, fmt: str | None) -> str:
    fmt = (fmt or "").strip()
    if fmt == "len":
        return str(len(value))
    if fmt == "pct" and isinstance(value, int | float):
        return f"{value * 100:.0f}%"
    if fmt and isinstance(value, str):
        try:  # some APIs (e.g. HPA) return numbers as strings
            value = float(value)
        except ValueError:
            return value
    if fmt and isinstance(value, int | float) and not isinstance(value, bool):
        return format(value, fmt)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def resolve_anchor(run_dir: Path, key: str, path: str, fmt: str | None) -> str | None:
    """Return the formatted value, or None when the raw record / path does not exist."""
    rec = load_raw(run_dir, key)
    if rec is None or rec.data is None:
        return None
    value = get_path(rec.data, path)
    if value is _MISSING or value is None:
        return None
    try:
        return format_value(value, fmt)
    except (TypeError, ValueError):
        return None


def cited_keys(text: str) -> list[str]:
    """All citation keys in order of appearance (duplicates kept)."""
    keys: list[str] = []
    for group in CITE_GROUP_RE.finditer(text):
        keys.extend(CITE_KEY_RE.findall(group.group(1)))
    return keys
