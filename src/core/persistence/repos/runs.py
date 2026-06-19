# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.persistence.models import Run


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        run_id: uuid.UUID,
        target_gene: str,
        disease: str,
        user_request: str,
        direction: str = "unspecified",
        population: str | None = None,
        tissue: str | None = None,
        step_budget_total: int = 200,
        model_fingerprint: str | None = None,
        force_refresh: bool = False,
    ) -> Run:
        run = Run(
            id=run_id,
            status="pending",
            target_gene=target_gene,
            disease=disease,
            direction=direction,
            population=population,
            tissue=tissue,
            user_request=user_request,
            step_budget_total=step_budget_total,
            step_budget_consumed=0,
            model_fingerprint=model_fingerprint,
            force_refresh=force_refresh,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: uuid.UUID) -> Run | None:
        result = await self._session.execute(select(Run).where(Run.id == run_id))
        return result.scalar_one_or_none()

    async def find_prior_run(
        self,
        target_gene: str,
        disease: str,
        direction: str,
        population: str | None,
        tissue: str | None,
    ) -> Run | None:
        """Return the most recent done run for the same (gene, disease, direction, population, tissue)."""
        conditions = [
            Run.target_gene == target_gene,
            Run.disease == disease,
            Run.direction == direction,
            Run.status == "done",
            Run.population == population if population is not None else Run.population.is_(None),
            Run.tissue == tissue if tissue is not None else Run.tissue.is_(None),
        ]
        result = await self._session.execute(
            select(Run).where(and_(*conditions)).order_by(desc(Run.created_at)).limit(1)
        )
        return result.scalar_one_or_none()

    async def update_status(self, run_id: uuid.UUID, status: str) -> None:
        await self._session.execute(
            update(Run).where(Run.id == run_id).values(status=status, updated_at=datetime.now(UTC))
        )

    async def increment_step_budget(self, run_id: uuid.UUID, n: int = 1) -> None:
        run = await self.get(run_id)
        if run is not None:
            run.step_budget_consumed += n
            run.updated_at = datetime.now(UTC)

    async def increment_rerun_count(self, run_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(rerun_count=Run.rerun_count + 1, updated_at=datetime.now(UTC))
        )
