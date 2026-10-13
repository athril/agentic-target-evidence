# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Axis → structured evidence types map (config/axis_evidence.yaml)."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path("config/axis_evidence.yaml")


@dataclass(frozen=True)
class FloorRule:
    """A claim that settles a favourable axis on its own.

    Matches when the claim contains one of ``tokens`` (if any) and, if ``pattern`` is
    set, some number it captures satisfies ``below``/``above`` — e.g. a trial-results
    claim with p < 0.05.
    """

    floor: float
    tokens: tuple[str, ...] = ()
    pattern: re.Pattern[str] | None = None
    below: float | None = None
    above: float | None = None

    def matches(self, claim_text: str) -> bool:
        text = (claim_text or "").lower()
        if self.tokens and not any(t in text for t in self.tokens):
            return False
        if self.pattern is None:
            return bool(self.tokens)
        for m in self.pattern.finditer(text):
            try:
                value = float(m.group(1))
            except (IndexError, ValueError):
                continue
            if (self.below is None or value < self.below) and (
                self.above is None or value > self.above
            ):
                return True
        return False


@dataclass(frozen=True)
class AxisEvidenceMap:
    types: dict[tuple[str, str], frozenset[str]]  # (lens, axis) → evidence_type values
    exclude: tuple[str, ...]  # lower-cased claim phrases never attached
    descriptions: dict[tuple[str, str], str] = field(default_factory=dict)
    include: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    floors: dict[tuple[str, str], tuple[FloorRule, ...]] = field(default_factory=dict)

    def types_for(self, lens: str, axis: str) -> frozenset[str]:
        return self.types.get((lens, axis), frozenset())

    def attaches(self, lens: str, axis: str, evidence_type: str, claim_text: str) -> bool:
        """Whether a claim of this type and text is attached to the axis."""
        if evidence_type not in self.types_for(lens, axis) or self.excluded(claim_text):
            return False
        phrases = self.include.get((lens, axis))
        text = (claim_text or "").lower()
        return not phrases or any(p in text for p in phrases)

    def floor_for(self, lens: str, axis: str, claim_text: str) -> float:
        """The highest definitive floor among the axis's rules the claim matches, else 0."""
        rules = self.floors.get((lens, axis), ())
        return max((r.floor for r in rules if r.matches(claim_text)), default=0.0)

    def describe(self, lens: str, axis: str) -> str:
        """The axis's fixed description, or its name in words when none is configured."""
        return self.descriptions.get((lens, axis)) or axis.replace("_", " ")

    def excluded(self, claim_text: str) -> bool:
        text = (claim_text or "").lower()
        return any(phrase in text for phrase in self.exclude)


@functools.lru_cache(maxsize=4)
def _load(path_str: str) -> AxisEvidenceMap:
    import yaml

    path = Path(path_str)
    data: dict[str, Any] = {}
    if path.exists():
        with path.open() as fh:
            data = yaml.safe_load(fh) or {}
    types: dict[tuple[str, str], frozenset[str]] = {}
    descriptions: dict[tuple[str, str], str] = {}
    include: dict[tuple[str, str], tuple[str, ...]] = {}
    floors: dict[tuple[str, str], tuple[FloorRule, ...]] = {}
    for lens, axes in (data.get("lenses") or {}).items():
        for axis, cfg in (axes or {}).items():
            key = (str(lens), str(axis))
            # An axis is either a bare list of types or {types: [...], describe: "..."}.
            spec = cfg if isinstance(cfg, dict) else {"types": cfg}
            types[key] = frozenset(str(t) for t in (spec.get("types") or []))
            if spec.get("describe"):
                descriptions[key] = " ".join(str(spec["describe"]).split())
            if spec.get("include"):
                include[key] = tuple(str(p).lower() for p in spec["include"])
            # `definitive` is one rule or a list of rules.
            definitive = spec.get("definitive") or []
            rules = definitive if isinstance(definitive, list) else [definitive]
            parsed = tuple(_floor_rule(r) for r in rules if r.get("tokens") or r.get("pattern"))
            if parsed:
                floors[key] = parsed
    exclude = tuple(str(p).lower() for p in data.get("exclude") or [])
    return AxisEvidenceMap(
        types=types, exclude=exclude, descriptions=descriptions, include=include, floors=floors
    )


def _floor_rule(cfg: dict[str, Any]) -> FloorRule:
    pattern = cfg.get("pattern")
    return FloorRule(
        floor=float(cfg.get("floor", 0.0)),
        tokens=tuple(str(t).lower() for t in cfg.get("tokens") or []),
        pattern=re.compile(str(pattern), re.IGNORECASE) if pattern else None,
        below=float(cfg["below"]) if cfg.get("below") is not None else None,
        above=float(cfg["above"]) if cfg.get("above") is not None else None,
    )


def axis_evidence_map(*, path: Path | None = None) -> AxisEvidenceMap:
    return _load(str(path or _CONFIG_PATH))


def reload_axis_evidence() -> None:
    _load.cache_clear()
