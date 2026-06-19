# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Suitability score floor — deterministic post-LLM clamp for Mendelian-grade causality.

The floor value is read from config/scoring.yaml rather than hard-coded
(CLAUDE.md rule 4: routing/thresholds are config-driven).
"""

from __future__ import annotations

from pathlib import Path

import yaml

_DEFAULT_FLOOR = 70
_DEFAULT_CONFIG_PATH = Path("config/scoring.yaml")


def get_mendelian_score_floor(path: Path | None = None) -> int:
    path = path or _DEFAULT_CONFIG_PATH
    if not path.exists():
        return _DEFAULT_FLOOR
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return int(data.get("mendelian_causality_score_floor", _DEFAULT_FLOOR))


def apply_mendelian_score_floor(
    results: list[dict],
    mendelian_grade: bool,
    floor: int | None = None,
) -> list[dict]:
    """Clamp each ExperimentResult's score up to the configured floor when the
    target's genetics evidence is Mendelian-grade. Never lowers a score the
    LLM already set at or above the floor.

    `floor` defaults to the config-driven value (config/scoring.yaml); pass it
    explicitly only in tests that need to decouple from the filesystem.
    """
    if not mendelian_grade:
        return results
    floor = floor if floor is not None else get_mendelian_score_floor()
    floored: list[dict] = []
    for r in results:
        score = r.get("score")
        if isinstance(score, (int, float)) and score < floor:
            note = (
                f"[Mendelian causality floor applied: score raised to {floor} — "
                "Mendelian-grade genetic validation with a clear therapeutic direction "
                "is a dominant positive that sets a score floor; clinical/safety "
                "uncertainty caps the upside, not this floor.]"
            )
            r = {**r, "score": floor, "rationale": f"{r.get('rationale', '')} {note}".strip()}
        floored.append(r)
    return floored
