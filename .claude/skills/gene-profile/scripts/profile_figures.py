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

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SURFACE = "#fcfcfb"
# Molecular effect of a variant on the protein (profile_variants.effect_class).
EFFECT_COLOUR = {
    "gain": "#eb6834",
    "loss": "#2a78d6",
    "truncating": "#2a78d6",
    "lower expression": "#2a78d6",
    "dominant negative": "#1baf7a",
    "mixed": "#1baf7a",
    "higher expression": "#eb6834",
    "": "#b5b3ab",
}
EFFECT_LABEL = {
    "gain": "gain",
    "loss": "loss / truncating",
    "mixed": "mixed or dominant negative",
    "": "not typed",
}


def _effect_key(kind: str) -> str:
    """The colour-key class of an effect: gain, loss, mixed, or "" (not typed)."""
    if kind in ("truncating", "lower expression", "loss"):
        return "loss"
    if kind in ("higher expression", "gain"):
        return "gain"
    return "mixed" if kind in ("dominant negative", "mixed") else ""


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
# Lanes below the backbone, top to bottom: topology line and its label, the gnomAD pLoF
# ticks, then the somatic track and its label, each clear of the one above.
_TOPOLOGY_Y, _LOF_Y, _SOMATIC_TOP = -0.6, -1.4, -2.0
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


def _backing(pad: float = 0.12) -> dict[str, Any]:
    """A light surface-coloured box behind a label, so it stays legible over markers and
    lines. A path-effect outline would do the same but turns SVG text into paths, which a
    reader can no longer select or search."""
    return {"boxstyle": f"square,pad={pad}", "facecolor": SURFACE, "edgecolor": "none",
            "alpha": 0.85}  # fmt: skip


def _renderer(fig: Any) -> Any:
    fig.canvas.draw()
    return fig.canvas.get_renderer()


def _drawn_texts(fig: Any) -> list[Any]:
    """The texts a render draws: each axes' texts, title, axis labels and legend, its tick
    labels only when the axis is on and the tick is in view, and the figure's own texts."""
    out = list(fig.texts)
    for ax in fig.axes:
        out += [
            *ax.texts,
            ax.title,
            ax._left_title,
            ax._right_title,
            ax.xaxis.label,
            ax.yaxis.label,
        ]
        if ax.get_legend() is not None:
            out += list(ax.get_legend().get_texts())
        if ax.axison:
            for axis in (ax.xaxis, ax.yaxis):
                lo, hi = sorted(axis.get_view_interval())
                out += [
                    t.label1 for t in axis.get_major_ticks()
                    if lo <= t.get_loc() <= hi and t.label1.get_visible()
                ]  # fmt: skip
    return [t for t in out if t.get_visible() and t.get_text().strip()]


def text_overlaps(fig: Any, pad: float = 1.5) -> list[tuple[str, str]]:
    """Pairs of drawn texts whose boxes overlap by more than ``pad`` pixels each way. Two
    rotated tick labels are skipped: their boxes overlap while their glyphs do not."""
    from itertools import combinations

    renderer = _renderer(fig)
    boxes = [(t, t.get_window_extent(renderer)) for t in _drawn_texts(fig)]
    out = []
    for (a, ba), (b, bb) in combinations(boxes, 2):
        if a.get_rotation() and b.get_rotation():
            continue
        dx = min(ba.x1, bb.x1) - max(ba.x0, bb.x0)
        dy = min(ba.y1, bb.y1) - max(ba.y0, bb.y0)
        if dx > pad and dy > pad:
            out.append((a.get_text(), b.get_text()))
    return out


_LABEL_MARGIN = 2.5  # pixels kept clear around a label: its halo and a gap
# Where a point label may go, in points from the point: right, then above or below it.
_LABEL_OFFSETS = ((4, 0), (4, 8), (4, -8), (-4, 8), (-4, -8), (0, 13), (0, -13))


def place_labels(
    ax: Any, items: Sequence[tuple[float, float, str]], avoid: Sequence[Any] = ()
) -> list[str]:
    """Label points so no label overlaps another label, another point or ``avoid`` (window
    boxes, such as a legend); a label with no free place is left out, and the table beside
    the figure names every point. Returns the labels drawn."""
    fig = ax.figure
    renderer = _renderer(fig)
    points = ax.transData.transform([(x, y) for x, y, _t in items]) if items else []
    frame = ax.get_window_extent(renderer).expanded(1.02, 1.08)
    placed = list(avoid)
    drawn = []
    for i, (x, y, text) in enumerate(items):
        for dx, dy in _LABEL_OFFSETS:
            label = ax.annotate(
                text, (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=6,
                color=TEXT, va="center", ha="left" if dx >= 0 else "right", zorder=5,
                bbox=_backing(),
            )  # fmt: skip
            box = label.get_window_extent(renderer).padded(_LABEL_MARGIN)  # halo included
            clash = any(box.overlaps(b) for b in placed) or any(
                j != i and box.x0 - 2 <= px <= box.x1 + 2 and box.y0 - 2 <= py <= box.y1 + 2
                for j, (px, py) in enumerate(points)
            )
            inside = frame.x0 <= box.x0 and box.x1 <= frame.x1 and frame.y0 <= box.y0
            if not clash and inside and box.y1 <= frame.y1:
                placed.append(box)
                drawn.append(text)
                break
            label.remove()
    return drawn


def fit_name(name: str, max_chars: int) -> str:
    """A feature name cut to fit its box at a word or hyphen boundary, with an ellipsis;
    "" when not even its first word fits."""
    if len(name) <= max_chars:
        return name
    cut = max((name.rfind(sep, 0, max_chars) for sep in (" ", "/", "-")), default=-1)
    head = name[:cut].rstrip(" /-") if cut >= 4 else ""
    return f"{head}…" if head else ""


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
    effects: Mapping[int, str] | None = None,
    somatic: Mapping[int, int] | None = None,
    somatic_labels: Mapping[int, str] | None = None,
) -> Path | None:
    """Draw the protein map to ``path`` (SVG); None when there is nothing to draw.

    Lollipops are coloured by what the variant does to the protein (``effects``: position →
    effect class; grey when not typed) and shaped by source; ``somatic`` adds a track of
    TCGA somatic mutation counts per residue below the backbone, recurrent residues
    labelled (``somatic_labels``)."""
    effects = effects or {}
    somatic = somatic or {}
    if not length or not (features or missense or truncating or uniprot_disease or somatic):
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
            ax.plot([f["start"], f["end"]], [_TOPOLOGY_Y] * 2, color=TEXT_2, linewidth=1.2)
            if (f["end"] - f["start"]) / length > 0.08:
                ax.text(
                    (f["start"] + f["end"]) / 2,
                    _TOPOLOGY_Y - 0.14,
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
            shown = fit_name(name, max(4, int(share * 160))) if name else ""
            if shown and kind != "Region" and share > 0.03:
                ax.text(
                    (f["start"] + f["end"]) / 2, 0, shown,
                    ha="center", va="center", fontsize=6 if share < 0.06 else 6.5,
                    color=TEXT, zorder=4, bbox=_backing(0.08),
                )  # fmt: skip
    # Sites as short ticks on the bar's edges, clear of the domain names inside it.
    for f in (x for x in features if x.get("type") in _SITE_TYPES):
        for lo, hi in ((0.2, 0.34), (-0.34, -0.2)):
            ax.plot([f["start"]] * 2, [lo, hi], color=TEXT, linewidth=0.8, zorder=3)

    # lollipops: stack variants that share a residue
    stack: Counter[int] = Counter()

    def colour(p: int, default: str) -> str:
        return EFFECT_COLOUR.get(effects.get(p, ""), EFFECT_COLOUR[""]) if effects else default

    def lollipop(positions: Sequence[int], default: str, marker: str, label: str) -> None:
        xs, ys, cs = [], [], []
        for p in positions:
            y = 0.95 + 0.32 * stack[p]
            stack[p] += 1
            ax.plot([p, p], [0.36, y], color=GRID, linewidth=1, zorder=1)
            xs.append(p)
            ys.append(y)
            cs.append(colour(p, default))
        if xs:
            ax.scatter(
                xs, ys, s=30, marker=marker, c=cs, edgecolors=SURFACE,
                linewidths=1.5, zorder=4, label=f"{label} ({len(xs)})",
            )  # fmt: skip

    lollipop(uniprot_disease, UNIPROT, "D", "UniProt disease variant")
    lollipop(missense, MISSENSE, "o", "ClinVar P/LP missense")
    lollipop(truncating, TRUNCATING, "^", "ClinVar P/LP truncating")
    if effects:  # colour key: the effect classes present
        present = {
            _effect_key(effects.get(p, "")) for p in [*uniprot_disease, *missense, *truncating]
        }
        for kind in ("gain", "loss", "mixed", ""):
            if kind in present:
                ax.scatter([], [], s=30, marker="s", color=EFFECT_COLOUR[kind],
                           label=f"effect: {EFFECT_LABEL[kind]}")  # fmt: skip
    # Direct labels for the leading variants, never two within 3% of the protein, each above
    # the tallest stack near it so neighbouring markers do not cover it.
    placed: list[int] = []
    window = max(1, int(0.02 * length))
    for p, text in labels.items():
        if len(placed) >= _MAX_LABELS or len(text) > 7:
            continue
        if any(abs(p - q) < 0.03 * length for q in placed):
            continue
        placed.append(p)
        near = max((stack[q] for q in range(p - window, p + window + 1)), default=1)
        top = 0.95 + 0.32 * max(near - 1, 0)
        ax.text(p, top + 0.22, text, rotation=50, fontsize=6.5, color=TEXT, ha="left",
                va="bottom", zorder=5, bbox=_backing())  # fmt: skip
    if lof:
        ax.scatter(
            lof, [_LOF_Y] * len(lof), marker="|", s=40, color=TEXT_2, linewidths=1,
            label=f"gnomAD HC pLoF ({len(lof)} shown)",
        )  # fmt: skip
    bottom = _LOF_Y - 0.35
    if somatic:
        peak = max(somatic.values())
        for p, n in somatic.items():
            ax.plot(
                [p, p],
                [_SOMATIC_TOP, _SOMATIC_TOP - 0.9 * n / peak],
                color="#8c8a84",
                linewidth=1.2,
            )
        shown: list[int] = []
        for p, text in (somatic_labels or {}).items():  # most mutated first
            if any(abs(p - q) < 0.03 * length for q in shown):
                continue
            shown.append(p)
            ax.text(p, _SOMATIC_TOP - 0.9 * somatic.get(p, 0) / peak - 0.08, text, fontsize=6, color=TEXT,
                    ha="center", va="top")  # fmt: skip
        ax.text(1, _SOMATIC_TOP + 0.06, f"TCGA somatic mutations per residue (max {peak})", fontsize=6.5,
                color=TEXT_2, va="bottom")  # fmt: skip
        bottom = _SOMATIC_TOP - 1.3
    top = 0.95 + 0.32 * max(max(stack.values(), default=1), 1)
    ax.set_ylim(bottom, top + 1.1)
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
        # Above the scale's top (100), so the curve never runs through it.
        conf.text(length + 2, 104, "AlphaFold pLDDT", fontsize=6.5, color=TEXT_2, va="bottom",
                  ha="right")  # fmt: skip
    for a, b in pdb:
        conf.add_patch(
            plt.Rectangle((a, -22), b - a + 1, 10, facecolor="#8c8a84", edgecolor="none")
        )
    if pdb:
        conf.text(1, -10, "PDB coverage", fontsize=6.5, color=TEXT_2, va="bottom")
    conf.set_ylim(-26, 118)
    conf.set_yticks([50, 70, 90])
    conf.tick_params(axis="y", labelsize=6.5, length=0)
    conf.set_xlabel("Residue")
    return _save(fig, path)


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
    tallest = max(len(p[0]) for p in panels)
    for a, (rows, title) in zip(axes, panels, strict=True):
        names = [r[0] for r in rows][::-1]
        values = [r[1] for r in rows][::-1]
        a.set_facecolor(SURFACE)
        a.barh(names, values, color=MISSENSE, height=0.7, edgecolor=SURFACE, linewidth=2)
        # Every panel on the tallest panel's scale, top-aligned: one cell type is one bar, not
        # a block filling the panel.
        a.set_ylim(len(rows) - tallest - 0.6, len(rows) - 0.4)
        a.set_title(title, fontsize=8, color=TEXT, loc="left")
        a.tick_params(axis="y", length=0, labelsize=7, labelcolor=TEXT)
        a.tick_params(axis="x", labelsize=7)
        a.grid(axis="x", color=GRID, linewidth=0.8)
        a.set_axisbelow(True)
        for side in ("top", "right", "left"):
            a.spines[side].set_visible(False)
    fig.tight_layout()
    return _save(fig, path)


def _save(fig: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format=path.suffix.lstrip(".") or "svg", facecolor=SURFACE,
                bbox_inches="tight", dpi=160)  # fmt: skip
    import matplotlib.pyplot as plt

    plt.close(fig)
    return path


def _axes(plt: Any, size: tuple[float, float]) -> tuple[Any, Any]:
    fig, ax = plt.subplots(figsize=size)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


_DIRECTION_MARKER = {"increase": "^", "decrease": "v", None: "o"}


def phenome(path: Path, points: Sequence[Mapping[str, Any]]) -> Path | None:
    """GWAS associations by trait group: −log10 p (log scale), filled when Open Targets ties
    the association to this gene (L2G or colocalisation), hollow when mapped by position
    only; triangles give the direction for the reported effect allele.

    Groups are rows, strongest first, so their names read horizontally whatever their length
    (rotated, "measurement: omega-6 polyunsaturated fatty acid measurement" ran into the
    axis); the legend sits beside the plot, not over its points."""
    if not points:
        return None
    plt = _pyplot()
    order = sorted(
        {p["group"] for p in points},
        key=lambda g: -max(q["mlog10p"] for q in points if q["group"] == g),
    )
    fig, ax = _axes(plt, (7.5, 0.3 * len(order) + 1.2))
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    seen: set[tuple[bool, Any]] = set()
    for i, group in enumerate(order):
        y = len(order) - 1 - i
        members = [p for p in points if p["group"] == group]
        for j, p in enumerate(members):
            dy = (j - (len(members) - 1) / 2) * min(0.6 / max(len(members), 1), 0.08)
            key = (p["supported"], p["direction"])
            label = None
            if key not in seen:
                seen.add(key)
                label = (
                    "gene supported (L2G / coloc)" if p["supported"] else "mapped by position only"
                ) + (f", effect allele {p['direction']}s" if p["direction"] else "")
            ax.scatter(
                p["mlog10p"], y + dy, s=26, marker=_DIRECTION_MARKER.get(p["direction"], "o"),
                facecolors=MISSENSE if p["supported"] else "none",
                edgecolors=MISSENSE if p["supported"] else TEXT_2, linewidths=1, label=label,
            )  # fmt: skip
    ax.axvline(-math.log10(5e-8), color=GRID, linewidth=1, linestyle="--")
    ax.set_xscale("log")
    ax.set_xlabel("−log10 p (log scale)")
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(order[::-1], fontsize=7)
    ax.set_ylim(-0.6, len(order) - 0.4)
    ax.tick_params(axis="y", length=0)
    ax.legend(frameon=False, fontsize=6.5, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    return _save(fig, path)


def allelic_spectrum(path: Path, points: Sequence[Mapping[str, Any]]) -> Path | None:
    """Rare-large against common-small: x = gnomAD allele frequency (log), y = GWAS effect
    size per alt allele (|β| or |ln OR|, in each study's units). Disease variants without an
    estimate have no y: they sit on their own strip above, on the same frequency axis, and
    with no estimate at all only the strip is drawn. Colour = molecular effect, triangle =
    direction. Labels are placed where they collide with no other label, point or the legend
    (outside the plot); the variant tables name every point."""
    if not points:
        return None
    plt = _pyplot()
    est = [p for p in points if p.get("effect") is not None]
    bare = [p for p in points if p.get("effect") is None]
    if est and bare:
        fig, (strip, ax) = plt.subplots(
            2, 1, figsize=(7.5, 5.0), sharex=True,
            gridspec_kw={"height_ratios": [1, 3.2], "hspace": 0.08},
        )  # fmt: skip
    elif est:
        fig, ax = plt.subplots(figsize=(7.5, 4.2))
        strip = None
    else:
        fig, strip = plt.subplots(figsize=(7.5, 1.9))
        ax = None
    fig.patch.set_facecolor(SURFACE)
    seen: set[str] = set()

    def draw(target: Any, rows: Sequence[Mapping[str, Any]], ys: Sequence[float]) -> None:
        for p, y in zip(rows, ys, strict=True):
            kind = p.get("function") or ""
            label = None
            if _effect_key(kind) not in seen:
                seen.add(_effect_key(kind))
                label = f"effect: {EFFECT_LABEL[_effect_key(kind)]}"
            target.scatter(
                p["af"], y, s=32, marker=_DIRECTION_MARKER.get(p.get("direction"), "o"),
                color=EFFECT_COLOUR.get(kind, EFFECT_COLOUR[""]), edgecolors=SURFACE,
                linewidths=1, label=label, zorder=3,
            )  # fmt: skip

    for target in (t for t in (strip, ax) if t is not None):
        target.set_facecolor(SURFACE)
        for side in ("top", "right"):
            target.spines[side].set_visible(False)
        target.set_xscale("log")
    if ax is not None:
        draw(ax, est, [p["effect"] for p in est])
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_ylabel("GWAS effect per alt allele (|β| or |ln OR|)")
        ax.set_xlabel("gnomAD allele frequency")
    if strip is not None:
        # Spread the strip's points over three rows so close frequencies do not stack.
        rows = [i % 3 for i in range(len(bare))]
        order = sorted(range(len(bare)), key=lambda k: bare[k]["af"])
        ys = [0.0] * len(bare)
        for rank, k in enumerate(order):
            ys[k] = rows[rank]
        draw(strip, bare, ys)
        strip.set_ylim(-0.8, 2.8)
        strip.set_yticks([])
        strip.spines["left"].set_visible(False)
        strip.set_title("disease variants without a GWAS effect estimate", fontsize=7,
                        color=TEXT_2, loc="left")  # fmt: skip
        if ax is None:
            strip.set_xlabel("gnomAD allele frequency")
    legend_ax = strip if ax is None else ax
    handles, names = [], []
    for target in (t for t in (strip, ax) if t is not None):
        h, n = target.get_legend_handles_labels()
        handles += h
        names += n
    legend = legend_ax.legend(
        handles, names, frameon=False, fontsize=6.5, loc="upper left", bbox_to_anchor=(1.01, 1.0)
    )
    avoid = [legend.get_window_extent(_renderer(fig))]
    if ax is not None:
        place_labels(ax, [(p["af"], p["effect"], p["label"]) for p in est if p.get("label")], avoid)
    if strip is not None:
        place_labels(
            strip,
            [(p["af"], y, p["label"]) for p, y in zip(bare, ys, strict=True) if p.get("label")],
            avoid,
        )
    return _save(fig, path)


def forest(path: Path, rows: Sequence[Mapping[str, Any]], title: str) -> Path | None:
    """Estimates with their CIs by outcome (log ratio axis): circles for single studies,
    diamonds for meta-analyses; filled when significant."""
    if not rows:
        return None
    plt = _pyplot()
    ordered = sorted(
        rows, key=lambda r: (((r.get("outcome") or {}).get("name") or ""), r.get("year") or "")
    )
    fig, ax = _axes(plt, (7.0, 0.28 * len(ordered) + 1.2))
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    labels = []
    for i, r in enumerate(ordered):
        y = len(ordered) - 1 - i
        lo, hi = r["ci"]
        ax.plot([lo, hi], [y, y], color=TEXT_2, linewidth=1)
        meta = r.get("meta_analysis")
        ax.scatter(r["value"], y, s=40 if meta else 22, marker="D" if meta else "o",
                   facecolors=MISSENSE if r.get("significant") else "none", edgecolors=MISSENSE, zorder=3)  # fmt: skip
        labels.append(
            f"{(r.get('outcome') or {}).get('name', '')} · PMID {r['pmid']} ({r.get('measure')})"
        )
    ax.axvline(1, color=TEXT, linewidth=0.8)
    ax.set_xscale("log")
    ax.set_yticks(range(len(ordered)))
    ax.set_yticklabels(labels[::-1], fontsize=6.5)
    ax.set_xlabel("ratio (log scale)")
    ax.set_title(title, fontsize=8, color=TEXT, loc="left")
    return _save(fig, path)


def field_map(path: Path, rows: Sequence[Mapping[str, Any]], years: Sequence[int]) -> Path | None:
    """Papers per MeSH branch and year (a heatmap; darker = more papers)."""
    if not rows or not years:
        return None
    plt = _pyplot()
    span = list(range(min(years), max(years) + 1))
    grid = [[(r["by_year"].get(y) or r["by_year"].get(str(y)) or 0) for y in span] for r in rows]
    fig, ax = plt.subplots(figsize=(min(12, 0.18 * len(span) + 4), 0.32 * len(rows) + 1.2))
    fig.patch.set_facecolor(SURFACE)
    image = ax.imshow(grid, aspect="auto", cmap="Blues", interpolation="nearest")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r["label"] for r in rows], fontsize=7)
    step = max(1, len(span) // 12)
    ax.set_xticks(range(0, len(span), step))
    ax.set_xticklabels([str(y) for y in span[::step]], fontsize=7, rotation=45)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    bar = fig.colorbar(image, ax=ax, fraction=0.03, pad=0.02)
    bar.ax.tick_params(labelsize=6.5)
    bar.set_label("papers", fontsize=7)
    return _save(fig, path)


_LINESTYLE = {"solid": "-", "dashed": "--", "dotted": ":"}
_BOX_CHARS = 22  # characters per line in a mechanism box
_BOX_GAP = 0.3  # inches between boxes, and above and below a box in its lane


def mechanism(path: Path, steps: Sequence[Mapping[str, Any]]) -> Path | None:
    """The writer's mechanism steps as swimlanes by compartment, left to right, branching
    where they name parallel routes; each step's box and the arrows into it drawn solid
    (human or in vivo, ≥ 2 groups, or a record), dashed (one group or cells only) or dotted
    (proposed).

    Every line of a box is wrapped, and the step spacing and lane height are set from the
    widest and tallest box once drawn, so no box overlaps another or leaves its lane."""
    import textwrap

    from profile_writer import step_edges

    if not steps:
        return None
    plt = _pyplot()
    lanes = list(dict.fromkeys(str(s.get("compartment") or "unspecified") for s in steps))
    fig, ax = plt.subplots(figsize=(max(6.0, 2.0 * len(steps)), 1.0 * len(lanes) + 0.8))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for i, lane in enumerate(lanes):
        y = len(lanes) - 1 - i
        ax.axhspan(y - 0.5, y + 0.5, color=GRID if i % 2 == 0 else SURFACE, zorder=0)
        ax.text(-0.75, y, lane, ha="right", va="center", fontsize=7, color=TEXT_2)

    def wrap(text: str) -> list[str]:
        return textwrap.wrap(text, _BOX_CHARS) or [""]

    centres, boxes = {}, []
    for i, s in enumerate(steps):
        y = len(lanes) - 1 - lanes.index(str(s.get("compartment") or "unspecified"))
        style = _LINESTYLE.get(s.get("strength", "dotted"), ":")
        lines = wrap(f"{s.get('actor', '')} {s.get('action', '')} {s.get('target', '')}")
        if s.get("cell_type"):
            lines += wrap(f"({s['cell_type']})")
        if s.get("route"):
            lines = [*wrap(f"{s['route']}:"), *lines]
        boxes.append(
            ax.text(i, y, "\n".join(lines), ha="center", va="center", fontsize=6.5, color=TEXT,
                    zorder=3, bbox={"boxstyle": "round,pad=0.35", "facecolor": SURFACE,
                                    "edgecolor": TEXT_2, "linestyle": style, "linewidth": 1})
        )  # fmt: skip
        centres[i] = (y, style)
    renderer = _renderer(fig)
    extents = [b.get_bbox_patch().get_window_extent(renderer) for b in boxes]
    width = max(e.width for e in extents) / fig.dpi + _BOX_GAP  # inches per step
    height = max(e.height for e in extents) / fig.dpi + _BOX_GAP  # inches per lane
    fig.set_size_inches(width * (len(steps) + 0.5) + 1.3, height * len(lanes) + 0.3)
    half = (width - _BOX_GAP) / 2 / width + 0.02  # a box's half-width in step units
    for start, end in step_edges(steps):
        y, style = centres[end]
        ax.annotate("", xy=(end - half, y), xytext=(start + half, centres[start][0]), zorder=2,
                    arrowprops={"arrowstyle": "->", "color": TEXT_2, "linestyle": style, "lw": 1})  # fmt: skip
    ax.set_xlim(-0.8, len(steps) - 0.3)
    ax.set_ylim(-0.6, len(lanes) - 0.4)
    ax.axis("off")
    return _save(fig, path)
