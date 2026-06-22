# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Disease-class taxonomy — replaces the oncology-only binary that gated DepMap/
tissue/genetics reasoning (`is_oncology_indication` / `_ONCOLOGY_AREA_IDS`).

Classes are NOT mutually exclusive — MASH resolves to both `metabolic` and
`fibrosis`. Two sources feed `resolve_disease_class`:

1. OT `therapeutic_areas` (broad classes) + a curated EFO override list for
   overlaps OT's therapeutic areas miss (config/disease_class.yaml).
2. `rare_mendelian`, inferred from the same genetics floor signals
   (`compute_mendelian_grade`) already used for the Mendelian causality
   floor — never from therapeutic area, since rare Mendelian disease genes
   span every therapeutic area.

See docs/lenses.md for the disease-class generalization design.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

import yaml

from services.evidence.constraint_interpret import compute_mendelian_grade

_CONFIG_PATH = Path("config/disease_class.yaml")


class DiseaseClass(StrEnum):
    ONCOLOGY = "oncology"
    METABOLIC = "metabolic"
    FIBROSIS = "fibrosis"
    RARE_MENDELIAN = "rare_mendelian"
    AUTOIMMUNE = "autoimmune"
    INFECTIOUS = "infectious"
    NEUROLOGY = "neurology"
    OTHER = "other"


@functools.lru_cache(maxsize=1)
def _load_config(path_str: str) -> dict:
    path = Path(path_str)
    if not path.exists():
        return {}
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def reload_disease_class_map() -> None:
    """Drop the cached config so the next resolve_disease_class() re-reads disk."""
    _load_config.cache_clear()


def _is_rare_mendelian(floor_signals: dict) -> bool:
    """Mirrors the Mendelian causality floor check (genetics_lens) — a
    gene-disease pair with gold-star P/LP, ClinGen Definitive/Strong, or strong
    graph corroboration is Mendelian-grade regardless of its therapeutic area.
    """
    return bool(
        compute_mendelian_grade(
            high_star_plp=floor_signals.get("high_star_plp") or 0,
            plp_count=floor_signals.get("plp_count") or 0,
            clingen_classification=floor_signals.get("clingen_classification"),
            graph_association=floor_signals.get("graph_association"),
        )
    )


def resolve_disease_class(
    disease_id: str | None,
    therapeutic_areas: Iterable[str] | None = None,
    floor_signals: dict | None = None,
    *,
    path: Path | None = None,
) -> set[DiseaseClass]:
    """Resolve the (non-exclusive) set of disease classes for a gene-disease pair.

    Falls back to `{DiseaseClass.OTHER}` when nothing matches, so callers always
    get a non-empty set rather than having to special-case "unknown".
    """
    data = _load_config(str(path or _CONFIG_PATH))
    classes: set[DiseaseClass] = set()

    overrides: dict = data.get("efo_overrides") or {}
    if disease_id and disease_id in overrides:
        classes.update(DiseaseClass(c) for c in overrides[disease_id])

    area_map: dict = data.get("therapeutic_area_map") or {}
    for area_id in therapeutic_areas or ():
        for c in area_map.get(area_id, []):
            classes.add(DiseaseClass(c))

    if floor_signals and _is_rare_mendelian(floor_signals):
        classes.add(DiseaseClass.RARE_MENDELIAN)

    if not classes:
        classes.add(DiseaseClass.OTHER)

    return classes
