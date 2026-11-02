# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Which questions a run searched, not merely which sources it reached.

A source that answered is not an evidence domain covered: a gene-name search does not find a
trial enrolling on the gene's status, and fifty of four hundred trials is not four hundred.
Each dimension names the records that serve it. Its status comes from their manifest entries,
so a dimension no record serves shows as such in every report rather than being assumed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from profile_schema import Manifest, load_raw

SEARCHED, NOTHING, NOT_APPLICABLE, FAILED, NO_SOURCE, NOT_CHECKED = (
    "searched",
    "searched, nothing found",
    "not applicable",
    "failed",
    "no search covers this",
    "not checked",
)
# (dimension, records); empty records declare a dimension nothing searches.
MATRIX: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "Genetics": [
        ("Germline", ("clinvar", "gencc", "clingen", "clingen_dosage", "orphanet_associations")),
        ("Somatic", ("somatic",)),
        ("Rare variants", ("gnomad_lof", "genebass", "allelic_series")),
        ("Common variants", ("gwas", "gwas_support", "ot_coloc")),
    ],
    "Functional biology": [
        ("Biochemical activity", ("uniprot_profile", "uniprot_extra", "reactome")),
        ("Molecular interactions", ("string",)),
        ("Localisation", ("uniprot_locations", "hpa")),
    ],
    "Disease associations": [
        ("Mendelian", ("gencc", "clingen", "orphanet_associations", "omim", "hpo")),
        ("Complex disease", ("gwas", "ot_associations")),
        ("Cancer", ("somatic",)),
    ],
    "Clinical trials": [
        ("Gene", ("ct_gene",)),
        ("Protein name", ("ct_biomarker",)),
        ("Pathway", ()),
        ("Biomarker status", ("ct_biomarker",)),
        ("Drug", ("ct_drugs", "ct_outcomes")),
        ("Indication", ("ct_gene_disease", "ct_condition")),
    ],
    "Approved therapies": [
        ("Direct target", ("drug_landscape", "ot_known_drugs")),
        ("Pathway", ("therapy_relevance",)),
        ("Biomarker-selected", ("therapy_relevance",)),
        ("Companion diagnostics", ("therapy_relevance",)),
    ],
    "Safety": [
        ("Genetic loss or gain", ("gnomad_lof", "gnomad_constraint", "clingen_dosage")),
        ("Pharmacological", ("pubmed_drug_safety", "openfda_labels", "ot_safety")),
        ("Animal models", ("ot_mouse", "impc")),
    ],
    "Pharmacovigilance": [("Per-drug query", ("faers",))],
    "Literature": [
        ("Mechanism", ("pubmed_cited", "pubmed_top", "pubtator_map")),
        ("Recent and reviews", ("pubmed_recent", "pubmed_reviews")),
        ("Clinical outcomes", ("ct_outcomes", "pubmed_drug_safety")),
    ],
}
_PER_DOMAIN_LISTS = ("trials", "records", "drugs")


def _extent(run_dir: Path, key: str) -> str:
    """How much of a capped search the run kept: "50 of 414", "" when it kept everything or
    the record has no total."""
    rec = load_raw(run_dir, key)
    data = rec.data if rec and isinstance(rec.data, dict) else {}
    total = data.get("total")
    if not isinstance(total, int):
        return ""
    kept = next((len(data[k]) for k in _PER_DOMAIN_LISTS if isinstance(data.get(k), list)), None)
    return f"{kept:,} of {total:,}" if kept is not None and kept < total else ""


def _status(entries: Iterable[tuple[str, str]]) -> str:
    """From (status, note) of each record that serves a dimension. A source gated off by a
    licence or a key was not checked; one skipped because the gene does not have the feature
    (no protein, no drug) does not apply."""
    entries = list(entries)
    found = {status for status, _note in entries}
    if "ok" in found:
        return SEARCHED
    if "empty" in found:
        return NOTHING
    if "error" in found:
        return FAILED
    gated = any(status == "skipped" and "gated off" in note for status, note in entries)
    return NOT_CHECKED if gated else NOT_APPLICABLE


def matrix(manifest: Manifest, run_dir: Path) -> list[dict[str, Any]]:
    """One row per domain and dimension: its status, the records behind it, and any record
    that kept part of what it matched."""
    entries = {e.key: e for e in manifest.entries}
    rows = []
    for domain, dimensions in MATRIX.items():
        for dimension, keys in dimensions:
            held = [entries[k] for k in keys if k in entries]
            capped = [
                f"{e.key}: {extent}"
                for e in held
                if e.status == "ok" and (extent := _extent(run_dir, e.key))
            ]
            status = _status((e.status, e.note) for e in held) if keys else NO_SOURCE
            rows.append(
                {
                    "domain": domain,
                    "dimension": dimension,
                    "status": status,
                    "records": [e.key for e in held],
                    "failed": [e.key for e in held if e.status == "error"],
                    "capped": capped,
                }
            )
    return rows


def failed_dimensions(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Dimensions no record answered because every search behind them failed."""
    return [f"{r['domain']} / {r['dimension']}" for r in rows if r["status"] == FAILED]


def uncovered_dimensions(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    return [f"{r['domain']} / {r['dimension']}" for r in rows if r["status"] == NO_SOURCE]


def matrix_md(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    out = ["| Evidence domain | Dimension | Status | Records | Kept of matched |", "|---|---|---|---|---|"]  # fmt: skip
    for r in rows:
        records = ", ".join(f"`{k}`" for k in r["records"]) or "—"
        kept = "; ".join(r["capped"]) or "—"
        out.append(f"| {r['domain']} | {r['dimension']} | {r['status']} | {records} | {kept} |")
    return out
