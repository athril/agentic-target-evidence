# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.persistence.models import Critique, Hypothesis, Review


class HypothesisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        run_id: uuid.UUID,
        name: str,
        verdict: bool,
        confidence: float,
        rationale: str,
    ) -> Hypothesis:
        hyp = Hypothesis(
            run_id=run_id,
            name=name,
            verdict=verdict,
            confidence=confidence,
            rationale=rationale,
        )
        self._session.add(hyp)
        await self._session.flush()
        return hyp

    async def get_by_run(self, run_id: uuid.UUID) -> list[Hypothesis]:
        result = await self._session.execute(select(Hypothesis).where(Hypothesis.run_id == run_id))
        return list(result.scalars().all())


class CritiqueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        run_id: uuid.UUID,
        evidence_id: uuid.UUID,
        quality_challenge: str,
        impact_factor: float | None = None,
        sjr_score: float | None = None,
        novelty_flag: bool = False,
    ) -> Critique:
        critique = Critique(
            run_id=run_id,
            evidence_id=evidence_id,
            quality_challenge=quality_challenge,
            impact_factor=impact_factor,
            sjr_score=sjr_score,
            novelty_flag=novelty_flag,
        )
        self._session.add(critique)
        await self._session.flush()
        return critique


class ReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        run_id: uuid.UUID,
        stage: str,
        missing_aspects: list[str],
        completeness_score: int,
    ) -> Review:
        review = Review(
            run_id=run_id,
            stage=stage,
            missing_aspects=missing_aspects,
            completeness_score=completeness_score,
        )
        self._session.add(review)
        await self._session.flush()
        return review

    async def get_by_run(self, run_id: uuid.UUID) -> list[Review]:
        result = await self._session.execute(select(Review).where(Review.run_id == run_id))
        return list(result.scalars().all())
