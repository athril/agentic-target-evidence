# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Programmatic blocks of a profile: tables and figures the scripts write, not the model.

``draft.md`` names a block on a line of its own:

    {{table:allelic_series}}     the allelic series (profile_tables.py), with the tally of
                                 what the disease variants do to the target
    {{table:variant_spectrum}}   ClinVar P/LP by consequence and region; gnomAD pLoF
    {{table:structures}}         PDB entries by resolution and the AlphaFold model
    {{figure:protein_map}}       the protein map (profile_figures.py)
    {{figure:expression}}        GTEx tissues and HPA single-cell types

``check_profile.py`` validates the names and requires the allelic series in *Human
genetics* when the run has one; ``render.py`` replaces each line with its markdown. Table
rows and figure captions carry their own citations, so they number like the prose.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from profile_schema import load_raw

BLOCK_RE = re.compile(r"^\s*\{\{\s*((?:table|figure):[a-z_]+)\s*\}\}\s*$")

# block -> (raw key holding its data, section that must contain it or "")
BLOCKS: dict[str, tuple[str, str]] = {
    "table:allelic_series": ("allelic_series", "genetics"),
    "table:variant_spectrum": ("allelic_series", ""),
    "table:structures": ("uniprot_extra", ""),
    "figure:protein_map": ("uniprot_extra", ""),
    "figure:expression": ("gtex", ""),
}
FIGURE_DIR = "figures"


def find_blocks(draft: str) -> list[tuple[int, str]]:
    """(line number, block name) of every block line in the draft."""
    return [
        (i, m.group(1))
        for i, line in enumerate(draft.splitlines(), start=1)
        if (m := BLOCK_RE.match(line))
    ]


def blank_blocks(draft: str) -> str:
    """The draft with block lines emptied (line numbers kept) for the prose checks."""
    return "\n".join("" if BLOCK_RE.match(line) else line for line in draft.splitlines())


def _data(run_dir: Path, key: str) -> dict[str, Any]:
    rec = load_raw(run_dir, key)
    return rec.data if rec and rec.status == "ok" and isinstance(rec.data, dict) else {}


def required(run_dir: Path) -> dict[str, str]:
    """Blocks the draft must contain, with their section: those whose data has rows."""
    series = (_data(run_dir, "allelic_series").get("series") or {}).get("rows") or []
    return {"table:allelic_series": "genetics"} if series else {}


def _cite(keys: list[str], available: set[str]) -> str:
    keep = [k for k in keys if k in available]
    return "[" + "; ".join(f"@{k}" for k in keep) + "]" if keep else ""


# ── figures ─────────────────────────────────────────────────────────────────────────


def _protein_map(run_dir: Path, gene: str, path: Path) -> Path | None:
    from profile_figures import pdb_ranges, protein_map
    from profile_tables import consequence_class, plp_variants, protein_change

    uni = _data(run_dir, "uniprot_extra")
    clinvar = _data(run_dir, "gnomad_clinvar")
    lof = _data(run_dir, "gnomad_lof")
    series = (_data(run_dir, "allelic_series").get("series") or {}).get("rows") or []
    missense, truncating = [], []
    for v in plp_variants(clinvar):
        c = protein_change(v.get("hgvsp") or "")
        kind = consequence_class(v.get("major_consequence") or "")
        if c and kind == "missense":
            missense.append(c[1])
        elif c and kind == "truncating":
            truncating.append(c[1])
    lof_pos = [
        c[1]
        for v in lof.get("reported_variants") or []
        if (c := protein_change(v.get("hgvsp") or ""))
    ]
    labels: dict[int, str] = {}
    for r in series:
        if r.get("position") and r.get("change") and r["position"] not in labels:
            labels[r["position"]] = r["change"]
        if len(labels) >= 10:
            break
    return protein_map(
        path,
        length=int(uni.get("length") or 0),
        features=uni.get("features") or [],
        missense=missense,
        truncating=truncating,
        uniprot_disease=[v["position"] for v in uni.get("variants") or [] if v.get("diseases")],
        lof=lof_pos,
        plddt=_data(run_dir, "alphafold").get("plddt") or [],
        pdb=pdb_ranges(uni.get("pdb") or []),
        labels=labels,
    )


def _expression(run_dir: Path, gene: str, path: Path) -> Path | None:
    from profile_figures import expression

    gtex = _data(run_dir, "gtex").get("gtex_expressions") or []
    tissues = sorted(
        (
            (t["tissue"].replace("_", " "), float(t["median_tpm"]))
            for t in gtex
            if t.get("median_tpm") is not None
        ),
        key=lambda x: -x[1],
    )[:12]
    cells: list[tuple[str, float]] = []
    for name, value in (
        _data(run_dir, "hpa").get("RNA single cell type specific nCPM") or {}
    ).items():
        try:
            cells.append((name, float(value)))
        except (TypeError, ValueError):
            continue
    cells = sorted(cells, key=lambda x: -x[1])[:12]
    return expression(path, tissues=tissues, cell_types=cells)


_FIGURES: dict[str, tuple[Any, str, list[str]]] = {
    "figure:protein_map": (
        _protein_map,
        "{gene} protein map: UniProt features; ClinVar pathogenic / likely pathogenic "
        "variants (≥1★, not conflicting) by consequence; UniProt disease variants; gnomAD "
        "high-confidence pLoF positions; AlphaFold per-residue pLDDT and PDB coverage. "
        "The variants are listed in the allelic-series and spectrum tables.",
        ["uniprot_extra", "gnomad_clinvar", "gnomad_lof", "alphafold"],
    ),
    "figure:expression": (
        _expression,
        "{gene} expression: GTEx median TPM (top tissues) and Human Protein Atlas "
        "single-cell-type nCPM (enriched cell types).",
        ["gtex", "hpa"],
    ),
}


# ── expansion ───────────────────────────────────────────────────────────────────────


def expand(
    draft: str, run_dir: Path, gene: str, available: set[str], *, figures: bool = True
) -> tuple[str, list[str]]:
    """The draft with every block line replaced by its markdown, and notes on blocks that
    produced nothing (no data, figures off, matplotlib missing)."""
    from profile_tables import (
        allelic_series_md,
        structures_md,
        variant_spectrum_md,
    )

    notes: list[str] = []
    out: list[str] = []
    figure_no = 0
    series = _data(run_dir, "allelic_series")
    for line in draft.splitlines():
        m = BLOCK_RE.match(line)
        if not m:
            out.append(line)
            continue
        name = m.group(1)
        text = ""
        if name == "table:allelic_series":
            text = allelic_series_md(series.get("series") or {}, available)
        elif name == "table:variant_spectrum":
            text = variant_spectrum_md(series.get("spectrum") or {}, available)
        elif name == "table:structures":
            text = structures_md(
                _data(run_dir, "uniprot_extra"), _data(run_dir, "alphafold"), available
            )
        elif name in _FIGURES and figures:
            draw, caption, keys = _FIGURES[name]
            path = run_dir / FIGURE_DIR / f"{name.split(':', 1)[1]}.svg"
            try:
                drawn = draw(run_dir, gene, path)
            except ImportError as exc:
                drawn = None
                notes.append(f"{name}: not drawn ({exc})")
            if drawn is not None:
                figure_no += 1
                rel = drawn.relative_to(run_dir).as_posix()
                text = (
                    f"![Figure {figure_no}]({rel})\n\n"
                    f"*Figure {figure_no}.* {caption.format(gene=gene)} {_cite(keys, available)}"
                )
        elif name in _FIGURES:
            notes.append(f"{name}: figures off")
        if not text and not any(n.startswith(name) for n in notes):
            notes.append(f"{name}: no data")
        out.append(text)
    return "\n".join(out), notes
