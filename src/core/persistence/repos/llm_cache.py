# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.persistence.models import LlmCache


class LlmCacheRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, cache_key: str, model_used: str) -> dict | None:
        result = await self._session.execute(
            select(LlmCache).where(
                LlmCache.cache_key == cache_key,
                LlmCache.model_used == model_used,
            )
        )
        row = result.scalar_one_or_none()
        return row.payload if row is not None else None

    async def set(
        self,
        cache_key: str,
        model_used: str,
        decision_type: str,
        payload: dict,
    ) -> None:
        stmt = pg_insert(LlmCache).values(
            cache_key=cache_key,
            model_used=model_used,
            decision_type=decision_type,
            payload=payload,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["cache_key", "model_used"],
            set_={"decision_type": decision_type, "payload": payload},
        )
        await self._session.execute(stmt)
