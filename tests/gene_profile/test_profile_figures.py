# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Every figure stays legible on crowded input: no text overlaps other text.

Drawn from the recorded runs, the figures had labels on top of each other (22 colliding
variant labels on SCN5A's allelic spectrum), mechanism boxes overlapping their neighbours,
category names rotated into the axis and an effect axis scaled to nothing when no variant
had an estimate. Each figure here is drawn from deliberately crowded synthetic input and
checked with ``text_overlaps``, the same check that found them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "gene-profile" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

pytest.importorskip("matplotlib")

import profile_figures as figs  # noqa: E402


@pytest.fixture
def overlaps(monkeypatch: pytest.MonkeyPatch) -> list[list[tuple[str, str]]]:
    """The text overlaps of each figure saved during the test."""
    found: list[list[tuple[str, str]]] = []
    save = figs._save

    def checked(fig: Any, path: Path) -> Path:
        found.append(figs.text_overlaps(fig))
        return save(fig, path)

    monkeypatch.setattr(figs, "_save", checked)
    return found


def _clustered(n: int, effect: float | None) -> list[dict[str, Any]]:
    return [
        {"af": 1e-6 * (1 + 0.15 * i), "effect": effect if effect is None else effect + 0.01 * i,
         "function": "loss" if i % 2 else "", "label": f"R{100 + i}Q"}
        for i in range(n)
    ]  # fmt: skip


@pytest.mark.parametrize(
    "points",
    [_clustered(24, None), [*_clustered(12, None), *_clustered(12, 0.3)], _clustered(20, 0.5)],
    ids=["no estimates", "mixed", "all estimated"],
)
def test_the_allelic_spectrum_never_overlaps_its_labels(
    tmp_path: Path, overlaps: list[Any], points: list[dict[str, Any]]
) -> None:
    assert figs.allelic_spectrum(tmp_path / "s.svg", points) is not None
    assert overlaps == [[]]


def test_without_estimates_there_is_no_effect_axis(tmp_path: Path) -> None:
    path = figs.allelic_spectrum(tmp_path / "s.svg", _clustered(5, None))
    assert path is not None
    text = path.read_text()
    assert "GWAS effect per alt allele" not in text
    assert "without a GWAS effect estimate" in text


def test_a_label_with_no_free_place_is_left_out() -> None:
    plt = figs._pyplot()
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    items = [(0.5, 0.5, f"LABEL{i}") for i in range(12)]
    ax.scatter([x for x, _y, _t in items], [y for _x, y, _t in items])
    drawn = figs.place_labels(ax, items)
    assert 0 < len(drawn) < len(items)
    assert figs.text_overlaps(fig) == []
    plt.close(fig)


def test_the_protein_map_keeps_labels_and_names_apart(tmp_path: Path, overlaps: list[Any]) -> None:
    features = [
        {"type": "Domain", "start": 150, "end": 230, "description": "Phorbol-ester/DAG-type zinc finger"},
        {"type": "Domain", "start": 450, "end": 720, "description": "Protein kinase"},
        {"type": "Topological domain", "start": 1, "end": 766, "description": "Cytoplasmic"},
        *({"type": "Binding site", "start": p, "end": p} for p in range(160, 230, 9)),
    ]  # fmt: skip
    positions = [p for p in range(440, 620, 4) for _ in range(1 + p % 5)]
    path = figs.protein_map(
        tmp_path / "p.svg", length=766, features=features, missense=positions, truncating=[],
        uniprot_disease=[464, 469, 600], lof=list(range(150, 760, 40)), plddt=[90.0] * 766,
        pdb=[(1, 766)], labels={469: "G469A", 600: "V600E", 601: "K601E", 464: "G464V"},
        somatic={600: 551, 469: 40}, somatic_labels={600: "V600", 469: "G469"},
    )  # fmt: skip
    assert path is not None
    assert overlaps == [[]]
    assert "Phorbol-ester…" in path.read_text() or "Phorbol…" in path.read_text()


def test_the_phenome_reads_long_trait_groups_horizontally(
    tmp_path: Path, overlaps: list[Any]
) -> None:
    groups = [f"measurement: omega-{i} polyunsaturated fatty acid measurement" for i in range(14)]
    points = [
        {"group": g, "mlog10p": 8 + 3 * i + j, "supported": j % 2 == 0, "direction": None}
        for i, g in enumerate(groups)
        for j in range(4)
    ]
    assert figs.phenome(tmp_path / "p.svg", points) is not None
    assert overlaps == [[]]


def test_mechanism_boxes_do_not_overlap(tmp_path: Path, overlaps: list[Any]) -> None:
    steps = [
        {"actor": "Protein", "action": "binds and phosphorylates", "target": "its partner",
         "compartment": ["Cytoplasm", "Cell membrane", "Nucleus"][i % 3],
         "cell_type": "not specified (structures and biochemistry of purified complexes)",
         "strength": "dashed", "route": "r1" if i == 2 else ""}
        for i in range(6)
    ]  # fmt: skip
    assert figs.mechanism(tmp_path / "m.svg", steps) is not None
    assert overlaps == [[]]


def test_every_expression_panel_has_the_same_bar_thickness(tmp_path: Path) -> None:
    captured: list[Any] = []
    save = figs._save

    def keep(fig: Any, path: Path) -> Path:
        captured.extend(a.get_ylim() for a in fig.axes)
        return save(fig, path)

    figs._save = keep
    try:
        tissues = [(f"Tissue {i}", 30.0 - i) for i in range(12)]
        assert figs.expression(
            tmp_path / "e.svg", tissues=tissues, cell_types=[("Neutrophils", 2000.0)]
        )
    finally:
        figs._save = save
    spans = {round(hi - lo, 6) for lo, hi in captured}
    assert len(spans) == 1  # one row is one bar, not a block filling its panel
