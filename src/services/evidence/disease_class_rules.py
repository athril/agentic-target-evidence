# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Disease-class x lens guidance matrix — config, not prose.

Replaces scattered hardcoded disease-specific rules (e.g. the genetics skill's
former "Oncology targets" section) with one config-driven lookup
(config/disease_class_rules.yaml) so every lens gets uniform disease-class
treatment. `build_disease_class_note` is injected via each agent's existing
`extra_context` path in `run_lens` — the same pre-compute-then-inject pattern
as the constraint/tissue guards.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from pathlib import Path

import yaml

from services.evidence.disease_class import DiseaseClass

_CONFIG_PATH = Path("config/disease_class_rules.yaml")


@functools.lru_cache(maxsize=1)
def _load_config(path_str: str) -> dict:
    path = Path(path_str)
    if not path.exists():
        return {}
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def reload_disease_class_rules() -> None:
    """Drop the cached config so the next build_disease_class_note() re-reads disk."""
    _load_config.cache_clear()


def build_disease_class_note(
    disease_classes: Iterable[DiseaseClass | str],
    lens: str,
    *,
    path: Path | None = None,
) -> str:
    """Render the disease-class guidance block for one lens.

    Collects every `(class, lens)` line that matches `disease_classes` — plus the
    `non_oncology` fallback lines when `oncology` is not among them — deduplicated
    by exact text, and joins them into one prose block. Returns "" when nothing
    matches (most calls, for a disease class/lens pair with no curated rule).
    """
    data = _load_config(str(path or _CONFIG_PATH))
    by_class: dict = data.get("by_class") or {}
    non_oncology: dict = data.get("non_oncology") or {}

    classes = {c.value if isinstance(c, DiseaseClass) else str(c) for c in disease_classes}

    lines: list[str] = []
    seen: set[str] = set()
    for cls in sorted(classes):
        text = (by_class.get(cls) or {}).get(lens)
        if text:
            text = text.strip()
            if text not in seen:
                lines.append(text)
                seen.add(text)

    if DiseaseClass.ONCOLOGY.value not in classes:
        text = non_oncology.get(lens)
        if text:
            text = text.strip()
            if text not in seen:
                lines.append(text)
                seen.add(text)

    if not lines:
        return ""

    header = f"Disease-class context ({', '.join(sorted(classes))}):"
    return header + "\n" + "\n".join(f"- {line}" for line in lines)
