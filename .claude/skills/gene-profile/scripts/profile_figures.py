# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Figures drawn from the run's records (matplotlib, SVG), never screenshots.

A screenshot cannot be tied to the values the profile cites or redrawn when the data are
re-gathered, websites carry their own terms, and the Human Protein Atlas is CC BY-SA (its
share-alike term would bind the report). Each figure here repeats data a table in the
profile also carries, so the Markdown stays complete for a reader who cannot see images:

* ``protein_map``: the protein as residues 1…L with UniProt's features, ClinVar
  pathogenic / likely pathogenic variants as lollipops by consequence, UniProt's disease
  variants, gnomAD high-confidence pLoF positions, AlphaFold per-residue confidence and
  PDB coverage (tables: allelic series, variant spectrum);
* ``expression``: GTEx median TPM by tissue, HPA single-cell-type nCPM and HPA blood and
  immune cells nTPM.

Colours: three categorical slots of the validated default palette (blue, orange, aqua;
all-pairs CVD ΔE ≥ 9.2), each with its own marker shape and a legend; features in neutral
greys, named by text. Static figures, light surface.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
MISSENSE = "#2a78d6"
TRUNCATING = "#eb6834"
UNIPROT = "#1baf7a"
FEATURE_FILL = {
    "Domain": "#cfcdc6",
    "Repeat": "#cfcdc6",
    "Zinc finger": "#cfcdc6",
    "Region": "#e4e3df",
    "Coiled coil": "#e4e3df",
    "Signal": "#b5b3ab",
    "Propeptide": "#e4e3df",
    "Transmembrane": "#8c8a84",
    "Intramembrane": "#8c8a84",
}
_SITE_TYPES = ("Active site", "Binding site", "Motif")
_MAX_LABELS = 10


def _pyplot() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "svg.fonttype": "none",
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT_2,
            "xtick.color": TEXT_2,
            "ytick.color": TEXT_2,
        }
    )
    return plt


def pdb_ranges(pdb: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    """Residue ranges the PDB entries cover ("A/B=31-692" → (31, 692)), merged."""
    spans = sorted(
        (int(a), int(b))
        for entry in pdb
        for a, b in re.findall(r"(\d+)-(\d+)", str(entry.get("chains") or ""))
    )
    merged: list[tuple[int, int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def protein_map(
    path: Path,
    *,
    length: int,
    features: Sequence[Mapping[str, Any]],
    missense: Sequence[int],
    truncating: Sequence[int],
    uniprot_disease: Sequence[int],
    lof: Sequence[int],
    plddt: Sequence[float],
    pdb: Sequence[tuple[int, int]],
    labels: Mapping[int, str],
) -> Path | None:
    """Draw the protein map to ``path`` (SVG); None when there is nothing to draw."""
    if not length or not (features or missense or truncating or uniprot_disease):
        return None
    plt = _pyplot()
    fig, (ax, conf) = plt.subplots(
        2,
        1,
        figsize=(10, 4.4),
        sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1.1], "hspace": 0.08},
    )
    fig.patch.set_facecolor(SURFACE)
    for a in (ax, conf):
        a.set_facecolor(SURFACE)
        for side in ("top", "right", "left"):
            a.spines[side].set_visible(False)

    # backbone, topology and features
    ax.plot([1, length], [0, 0], color="#b5b3ab", linewidth=3, solid_capstyle="butt", zorder=1)
    for f in features:
        if f.get("type") == "Topological domain":
            ax.plot([f["start"], f["end"]], [-0.62, -0.62], color=TEXT_2, linewidth=1.2)
            if (f["end"] - f["start"]) / length > 0.08:
                ax.text(
                    (f["start"] + f["end"]) / 2,
                    -0.78,
                    f.get("description", ""),
                    ha="center",
                    va="top",
                    fontsize=6.5,
                    color=TEXT_2,
                )
    order = ["Region", "Coiled coil", "Propeptide", "Signal", "Domain", "Repeat",
             "Zinc finger", "Transmembrane", "Intramembrane"]  # fmt: skip
    for kind in order:
        for f in (x for x in features if x.get("type") == kind):
            h = 0.22 if kind in ("Region", "Coiled coil", "Propeptide") else 0.34
            ax.add_patch(
                plt.Rectangle(
                    (f["start"], -h), f["end"] - f["start"] + 1, 2 * h,
                    facecolor=FEATURE_FILL[kind], edgecolor=SURFACE, linewidth=1, zorder=2,
                )
            )  # fmt: skip
            name = f.get("description") or ("TM" if kind == "Transmembrane" else "")
            share = (f["end"] - f["start"]) / length
            if name and kind != "Region" and share > 0.03:
                ax.text(
                    (f["start"] + f["end"]) / 2, 0, name if len(name) <= 6 else name[: max(4, int(share * 160))],
                    ha="center", va="center", fontsize=6 if share < 0.06 else 6.5,
                    color=TEXT, zorder=3,
                )  # fmt: skip
    for f in (x for x in features if x.get("type") in _SITE_TYPES):
        ax.plot([f["start"]] * 2, [-0.34, 0.34], color=TEXT, linewidth=0.8, zorder=3)

    # lollipops: stack variants that share a residue
    stack: Counter[int] = Counter()

    def lollipop(positions: Sequence[int], colour: str, marker: str, label: str) -> None:
        xs, ys = [], []
        for p in positions:
            y = 0.95 + 0.32 * stack[p]
            stack[p] += 1
            ax.plot([p, p], [0.36, y], color=GRID, linewidth=1, zorder=1)
            xs.append(p)
            ys.append(y)
        if xs:
            ax.scatter(
                xs, ys, s=30, marker=marker, color=colour, edgecolors=SURFACE,
                linewidths=1.5, zorder=4, label=f"{label} ({len(xs)})",
            )  # fmt: skip

    lollipop(uniprot_disease, UNIPROT, "D", "UniProt disease variant")
    lollipop(missense, MISSENSE, "o", "ClinVar P/LP missense")
    lollipop(truncating, TRUNCATING, "^", "ClinVar P/LP truncating")
    # Direct labels for the leading variants, never two within 3% of the protein.
    placed: list[int] = []
    for p, text in labels.items():
        if len(placed) >= _MAX_LABELS or len(text) > 7:
            continue
        if any(abs(p - q) < 0.03 * length for q in placed):
            continue
        placed.append(p)
        top = 0.95 + 0.32 * max(stack[p] - 1, 0)
        ax.text(p, top + 0.2, text, rotation=50, fontsize=6.5, color=TEXT, ha="left", va="bottom")
    if lof:
        ax.scatter(
            lof, [-1.15] * len(lof), marker="|", s=40, color=TEXT_2, linewidths=1,
            label=f"gnomAD HC pLoF ({len(lof)} shown)",
        )  # fmt: skip
    top = 0.95 + 0.32 * max(max(stack.values(), default=1), 1)
    ax.set_ylim(-1.45, top + 1.1)
    ax.set_yticks([])
    ax.set_xlim(0, length + 1)
    ax.legend(
        loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, fontsize=7, ncol=4,
        labelcolor=TEXT_2, handletextpad=0.3, columnspacing=1.2,
    )  # fmt: skip

    # confidence and structure coverage
    if plddt:
        conf.plot(range(1, len(plddt) + 1), plddt, color=TEXT_2, linewidth=1.2)
        for level in (50, 70, 90):
            conf.axhline(level, color=GRID, linewidth=0.8, zorder=0)
        conf.text(length + 2, 92, "AlphaFold pLDDT", fontsize=6.5, color=TEXT_2, va="bottom",
                  ha="right")  # fmt: skip
    for a, b in pdb:
        conf.add_patch(
            plt.Rectangle((a, -22), b - a + 1, 10, facecolor="#8c8a84", edgecolor="none")
        )
    if pdb:
        conf.text(1, -10, "PDB coverage", fontsize=6.5, color=TEXT_2, va="bottom")
    conf.set_ylim(-26, 105)
    conf.set_yticks([50, 70, 90])
    conf.tick_params(axis="y", labelsize=6.5, length=0)
    conf.set_xlabel("Residue")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        path,
        format=path.suffix.lstrip(".") or "svg",
        facecolor=SURFACE,
        bbox_inches="tight",
        dpi=160,
    )
    plt.close(fig)
    return path


def expression(
    path: Path,
    *,
    tissues: Sequence[tuple[str, float]],
    cell_types: Sequence[tuple[str, float]],
    blood: Sequence[tuple[str, float]] = (),
) -> Path | None:
    """GTEx tissues (median TPM), HPA single-cell types (nCPM) and HPA blood and immune
    cells (nTPM), each its own axis."""
    panels = [(t, "GTEx median TPM") for t in [tissues] if t]
    panels += [(c, "HPA single-cell nCPM") for c in [cell_types] if c]
    panels += [(b, "HPA blood cells nTPM") for b in [blood] if b]
    if not panels:
        return None
    plt = _pyplot()
    width = {1: 6, 2: 10}.get(len(panels), 13)
    fig, axes = plt.subplots(
        1, len(panels), figsize=(width, 0.22 * max(len(p[0]) for p in panels) + 1)
    )  # fmt: skip
    axes = axes if len(panels) > 1 else [axes]
    fig.patch.set_facecolor(SURFACE)
    for a, (rows, title) in zip(axes, panels, strict=True):
        names = [r[0] for r in rows][::-1]
        values = [r[1] for r in rows][::-1]
        a.set_facecolor(SURFACE)
        a.barh(names, values, color=MISSENSE, height=0.7, edgecolor=SURFACE, linewidth=2)
        a.set_title(title, fontsize=8, color=TEXT, loc="left")
        a.tick_params(axis="y", length=0, labelsize=7, labelcolor=TEXT)
        a.tick_params(axis="x", labelsize=7)
        a.grid(axis="x", color=GRID, linewidth=0.8)
        a.set_axisbelow(True)
        for side in ("top", "right", "left"):
            a.spines[side].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        path,
        format=path.suffix.lstrip(".") or "svg",
        facecolor=SURFACE,
        bbox_inches="tight",
        dpi=160,
    )
    plt.close(fig)
    return path
