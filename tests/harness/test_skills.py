# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for load_skill."""

from __future__ import annotations

import pytest

from core.exceptions import SkillNotFound
from harness.skills import _clear_cache, load_skill


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    _clear_cache()


def test_load_skill_returns_content() -> None:
    content = load_skill("pubmed_query_craft")
    assert "PubMed" in content
    assert len(content) > 50


def test_load_skill_druggability() -> None:
    content = load_skill("druggability")
    assert "druggab" in content.lower()


def test_load_skill_source_quality() -> None:
    content = load_skill("source_quality_sjr")
    assert "SJR" in content or "sjr" in content.lower()


def test_load_skill_caches_after_first_load() -> None:
    first = load_skill("pubmed_query_craft")
    second = load_skill("pubmed_query_craft")
    assert first is second  # same object — came from cache


def test_load_skill_raises_on_missing_skill() -> None:
    with pytest.raises(SkillNotFound, match="nonexistent_skill"):
        load_skill("nonexistent_skill")


def test_load_skill_cache_cleared_between_tests() -> None:
    from harness import skills as skills_mod

    assert "pubmed_query_craft" not in skills_mod._cache
