# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Named evidence sources (config/source_registry.yaml) and how prose mentions them.

Lens prose cites sources by name ("GenCC classifies...", "ClinVar lists 69 ...")
while claims carry the same names in their text. This registry is the single,
data-driven list of those names and of which deterministic checks apply to each
source, so a new connector is covered by a config entry rather than a code change.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path("config/source_registry.yaml")


@dataclass(frozen=True)
class NamedSource:
    name: str
    aliases: tuple[str, ...]
    attach: bool = False  # citation repair may attach this source's claims
    check_numbers: bool = False  # numbers in prose naming it must be in its claims

    def mentioned_in(self, text: str) -> bool:
        lowered = (text or "").lower()
        return any(_whole_word(alias, lowered) for alias in self.aliases)


def _whole_word(alias: str, lowered_text: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9])"
    return re.search(pattern, lowered_text) is not None


@functools.lru_cache(maxsize=4)
def _load(path_str: str) -> tuple[NamedSource, ...]:
    import yaml

    path = Path(path_str)
    if not path.exists():
        return ()
    with path.open() as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}
    return tuple(
        NamedSource(
            name=str(name),
            aliases=tuple(str(a) for a in (cfg.get("aliases") or [name])),
            attach=bool(cfg.get("attach", False)),
            check_numbers=bool(cfg.get("check_numbers", False)),
        )
        for name, cfg in (data.get("sources") or {}).items()
    )


def named_sources(*, path: Path | None = None) -> tuple[NamedSource, ...]:
    return _load(str(path or _CONFIG_PATH))


def reload_source_registry() -> None:
    _load.cache_clear()
