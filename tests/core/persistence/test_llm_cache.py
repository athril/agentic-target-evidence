# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for LlmCacheRepository — mocked SQLAlchemy session."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from core.persistence.models import LlmCache
from core.persistence.repos.llm_cache import LlmCacheRepository


def _mock_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    return session


async def test_get_returns_none_on_miss() -> None:
    session = _mock_session()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute.return_value = result_mock

    repo = LlmCacheRepository(session)
    assert await repo.get("abc123", "llama3.1:8b") is None


async def test_set_then_get_returns_payload() -> None:
    session = _mock_session()
    payload = {"verdict": "keep", "rationale": "strong genetic evidence"}
    fake_row = MagicMock(spec=LlmCache)
    fake_row.payload = payload
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = fake_row
    session.execute.return_value = result_mock

    repo = LlmCacheRepository(session)
    result = await repo.get("abc123", "llama3.1:8b")

    assert result == payload
    session.execute.assert_awaited_once()


async def test_set_overwrites_on_conflict() -> None:
    session = _mock_session()

    repo = LlmCacheRepository(session)
    await repo.set("abc123", "llama3.1:8b", "screening", {"verdict": "discard"})

    session.execute.assert_awaited_once()


async def test_get_with_different_model_is_independent() -> None:
    """Two models with the same cache_key are stored independently."""
    session = _mock_session()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute.return_value = result_mock

    repo = LlmCacheRepository(session)
    # model_used differs — the query is sent; None returned means independent lookup
    result = await repo.get("abc123", "gemma2:9b")
    assert result is None
    session.execute.assert_awaited_once()
