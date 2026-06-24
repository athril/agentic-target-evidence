# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.persistence.models import EvidenceRow
from schemas.evidence import Evidence


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
        if not evidences:
            return
        rows = [_to_row(e) for e in evidences]
        stmt = pg_insert(EvidenceRow).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["evidence_id"],
            set_={k: getattr(stmt.excluded, k) for k in rows[0] if k != "evidence_id"},
        )
        await self._session.execute(stmt)

    async def find_by_target(
        self,
        gene: str,
        disease: str,
        direction: str,
        evidence_type: str | None = None,
    ) -> list[EvidenceRow]:
        """Return all evidence rows across runs for this (gene, disease, direction).

        Optionally filter to a single evidence_type string (e.g. "article").
        Used by acquisition nodes to detect prior evidence and skip external API calls.
        """
        conditions = [
            EvidenceRow.gene == gene,
            EvidenceRow.disease == disease,
            EvidenceRow.direction == direction,
        ]
        if evidence_type is not None:
            conditions.append(EvidenceRow.evidence_type == evidence_type)
        result = await self._session.execute(select(EvidenceRow).where(and_(*conditions)))
        return list(result.scalars().all())

    async def get_by_run(self, run_id: uuid.UUID) -> list[EvidenceRow]:
        result = await self._session.execute(
            select(EvidenceRow).where(EvidenceRow.run_id == run_id)
        )
        return list(result.scalars().all())

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
