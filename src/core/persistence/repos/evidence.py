# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.persistence.models import EvidenceRow
from schemas.evidence import Evidence

# asyncpg's limit on bind parameters per statement is 32,767 (a 16-bit count).
MAX_BIND_PARAMS = 32_000


def _to_row(ev: Evidence) -> dict[str, Any]:
    return {
        "evidence_id": ev.evidence_id,
        "run_id": ev.run_id,
        "schema_version": ev.schema_version,
        "gene": ev.gene,
        "gene_id": ev.gene_id,
        "disease": ev.disease,
        "disease_id": ev.disease_id,
        "direction": ev.direction.value,
        "availability_date": ev.availability_date,
        "population": ev.population,
        "evidence_type": ev.evidence_type.value,
        "scope": ev.scope,
        "source": ev.source,
        "source_link": str(ev.source_link),
        "claim_text": ev.claim_text,
        "source_evidence_id": ev.source_evidence_id,
        "query_used": ev.query_used,
        "artifact_uri": ev.artifact_uri,
        "extra": ev.extra,
        "classification": ev.classification.value,
        "prov_agent_name": ev.provenance.agent_name,
        "prov_tool_name": ev.provenance.tool_name,
        "prov_timestamp": ev.provenance.timestamp,
        "prov_model_used": ev.provenance.model_used,
        "prov_trace_id": ev.provenance.trace_id,
    }


class ScreenedRow:
    """A stored evidence row as one run screened it.

    Rows are shared between runs: a source served from the evidence cache keeps the run
    that first fetched it, and the ``screening_verdict`` in its ``extra`` is whichever run
    wrote last. Every field reads through to the row except ``extra``, which carries this
    run's own verdict.
    """

    def __init__(self, row: EvidenceRow, screening_verdict: Mapping[str, Any]) -> None:
        self._row = row
        self.extra: dict[str, Any] = {
            **(row.extra or {}),
            "screening_verdict": dict(screening_verdict),
        }

    def __getattr__(self, name: str) -> Any:
        return getattr(self._row, name)


class EvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, evidence: Evidence) -> None:
        row = _to_row(evidence)
        stmt = pg_insert(EvidenceRow).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["evidence_id"],
            set_={k: v for k, v in row.items() if k != "evidence_id"},
        )
        await self._session.execute(stmt)

    async def bulk_upsert(self, evidences: list[Evidence]) -> None:
        """Upsert in statements that stay under the driver's bind-parameter limit.

        One multi-row INSERT for a large run (1,185 kept + 1,149 dropped items × 25
        columns) exceeded asyncpg's 32,767 parameters, so the report's screened-verdict
        write failed for every large run. A repeated evidence id keeps its last copy: ON
        CONFLICT DO UPDATE cannot touch the same row twice in one statement.
        """
        if not evidences:
            return
        rows = list({e.evidence_id: _to_row(e) for e in evidences}.values())
        per_stmt = max(1, MAX_BIND_PARAMS // len(rows[0]))
        for start in range(0, len(rows), per_stmt):
            chunk = rows[start : start + per_stmt]
            stmt = pg_insert(EvidenceRow).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["evidence_id"],
                set_={k: getattr(stmt.excluded, k) for k in chunk[0] if k != "evidence_id"},
            )
            await self._session.execute(stmt)

    async def find_by_target(
        self,
        gene: str,
        disease: str,
        direction: str | None,
        evidence_type: str | None = None,
    ) -> list[EvidenceRow]:
        """Return all evidence rows across runs for this (gene, disease, direction).

        ``direction=None`` matches every direction, for evidence that does not depend on
        it (human genetics, expression). Optionally filter to a single evidence_type
        string (e.g. "article"). Used by acquisition nodes to detect prior evidence and
        skip external API calls.
        """
        conditions = [EvidenceRow.gene == gene, EvidenceRow.disease == disease]
        if direction is not None:
            conditions.append(EvidenceRow.direction == direction)
        if evidence_type is not None:
            conditions.append(EvidenceRow.evidence_type == evidence_type)
        result = await self._session.execute(select(EvidenceRow).where(and_(*conditions)))
        return list(result.scalars().all())

    async def get_by_run(self, run_id: uuid.UUID) -> list[EvidenceRow]:
        result = await self._session.execute(
            select(EvidenceRow).where(EvidenceRow.run_id == run_id)
        )
        return list(result.scalars().all())

    async def get_by_ids(self, evidence_ids: Iterable[uuid.UUID]) -> list[EvidenceRow]:
        ids = list(dict.fromkeys(evidence_ids))
        rows: list[EvidenceRow] = []
        for start in range(0, len(ids), MAX_BIND_PARAMS):
            result = await self._session.execute(
                select(EvidenceRow).where(
                    EvidenceRow.evidence_id.in_(ids[start : start + MAX_BIND_PARAMS])
                )
            )
            rows.extend(result.scalars().all())
        return rows

    async def get_for_run(
        self, run_id: uuid.UUID, verdicts: Mapping[str, Mapping[str, Any]]
    ) -> list[EvidenceRow | ScreenedRow]:
        """Every row a run used: those it screened, each with the run's verdict
        (``verdicts``: evidence id → screening verdict) whichever run stored them, and
        the rows it stored without screening them.

        `get_by_run` alone misses every source the run took from the evidence cache: a
        cached row keeps the run that first fetched it, so a run's full report listed no
        patents, trials, druggability, genetics or constraint rows whenever those came
        from the cache.
        """
        screened = await self.get_by_ids(uuid.UUID(str(k)) for k in verdicts)
        out: list[EvidenceRow | ScreenedRow] = [
            ScreenedRow(r, verdicts[str(r.evidence_id)]) for r in screened
        ]
        seen = {str(r.evidence_id) for r in screened}
        out += [r for r in await self.get_by_run(run_id) if str(r.evidence_id) not in seen]
        return out

    async def update_embedding(self, evidence_id: uuid.UUID, embedding: list[float]) -> None:
        row = await self._session.get(EvidenceRow, evidence_id)
        if row is not None:
            row.embedding = embedding

    async def similarity_search(
        self,
        embedding: list[float],
        run_id: uuid.UUID,
        k: int = 10,
    ) -> list[EvidenceRow]:
        # pgvector cosine distance operator: <=>
        result = await self._session.execute(
            select(EvidenceRow)
            .where(EvidenceRow.run_id == run_id)
            .where(EvidenceRow.embedding.is_not(None))
            .order_by(EvidenceRow.embedding.cosine_distance(embedding))
            .limit(k)
        )
        return list(result.scalars().all())
