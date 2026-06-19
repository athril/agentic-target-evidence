# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from core.exceptions import SkillNotFound

_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"
_cache: dict[str, str] = {}


def load_skill(name: str) -> str:
    """Return the markdown content of skills/{name}.md.

    Results are cached in memory after the first load.  Raises SkillNotFound
    if the file does not exist — callers should treat this as a configuration
    error, not a retryable condition.
    """
    if name in _cache:
        return _cache[name]

    path = _SKILLS_DIR / f"{name}.md"
    if not path.exists():
        raise SkillNotFound(
            f"Skill {name!r} not found at {path}. Add a markdown file to the skills/ directory."
        )

    content = path.read_text(encoding="utf-8")
    _cache[name] = content
    return content


def _clear_cache() -> None:
    """Clear the in-memory skill cache (used in tests)."""
    _cache.clear()
