# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claim-vs-prose contradiction checks (config/fact_checks.yaml).

Each check pairs a fact a deterministic claim states ("label carries a boxed
warning") with prose that denies it ("no black-box warnings"). The checks are data,
so a new contradiction pattern is a config entry, not a new guard.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path("config/fact_checks.yaml")


def _fill(text: str, gene: str, disease: str) -> str:
    return text.replace("{gene}", (gene or "").lower()).replace(
        "{disease}", (disease or "").lower()
    )


@dataclass(frozen=True)
class FactCheck:
    id: str
    claims: tuple[str, ...]  # case-insensitive substrings; a shown claim must contain one
    denial: str  # regex; may contain {gene} / {disease}
    message: str

    def claim_matches(self, claim_text: str, gene: str, disease: str) -> bool:
        """Whether a (lower-cased) claim text states this check's fact."""
        return any(_fill(c, gene, disease) in claim_text for c in self.claims)

    def denial_re(self, gene: str, disease: str) -> re.Pattern[str]:
        pattern = self.denial.replace("{gene}", re.escape(gene or "")).replace(
            "{disease}", re.escape(disease or "")
        )
        return re.compile(pattern, re.IGNORECASE)


@functools.lru_cache(maxsize=4)
def _load(path_str: str) -> tuple[FactCheck, ...]:
    import yaml

    path = Path(path_str)
    if not path.exists():
        return ()
    with path.open() as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}
    return tuple(
        FactCheck(
            id=str(c["id"]),
            # `claim` is one phrase or a list of alternatives; {gene}/{disease} allowed.
            claims=tuple(
                str(x).lower()
                for x in (c["claim"] if isinstance(c["claim"], list) else [c["claim"]])
            ),
            denial=" ".join(str(c["denial"]).split()),
            message=" ".join(str(c.get("message") or "").split()),
        )
        for c in data.get("checks") or []
    )


def fact_checks(*, path: Path | None = None) -> tuple[FactCheck, ...]:
    return _load(str(path or _CONFIG_PATH))


def reload_fact_checks() -> None:
    _load.cache_clear()
