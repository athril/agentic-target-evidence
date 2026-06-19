# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(32))  # pending|running|hitl_wait|done|error
    target_gene: Mapped[str] = mapped_column(String(64))
    disease: Mapped[str] = mapped_column(String(256))
    direction: Mapped[str] = mapped_column(String(16), default="unspecified")
    population: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tissue: Mapped[str | None] = mapped_column(String(256), nullable=True)
    user_request: Mapped[str] = mapped_column(Text)
    step_budget_total: Mapped[int] = mapped_column(Integer, default=200)
    step_budget_consumed: Mapped[int] = mapped_column(Integer, default=0)
    rerun_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    model_fingerprint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    force_refresh: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    evidence: Mapped[list[EvidenceRow]] = relationship(back_populates="run", lazy="select")


class EvidenceRow(Base):
    __tablename__ = "evidence"

    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE")
    )
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    gene: Mapped[str] = mapped_column(String(64))
    gene_id: Mapped[str] = mapped_column(String(128), nullable=True, default="")
    disease: Mapped[str] = mapped_column(String(256))
    disease_id: Mapped[str] = mapped_column(String(128), nullable=True, default="")
    direction: Mapped[str] = mapped_column(String(16), default="unspecified")
    availability_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    population: Mapped[str | None] = mapped_column(String(256), nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(256))
    source_link: Mapped[str] = mapped_column(Text)
    query_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    classification: Mapped[str] = mapped_column(String(16))
    # Provenance — flattened into the row to avoid a join on every read
    prov_agent_name: Mapped[str] = mapped_column(String(64))
    prov_tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prov_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prov_model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prov_trace_id: Mapped[str] = mapped_column(String(128))
    # pgvector embedding (768-dim, nomic-embed-text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[Run] = relationship(back_populates="evidence")


class Hypothesis(Base):
    __tablename__ = "hypotheses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(64))  # druggability | toxicity | solubility | ...
    verdict: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE")
    )
    target: Mapped[str] = mapped_column(String(64))
    score: Mapped[int] = mapped_column(Integer)  # 0–100 suitability score
    rationale: Mapped[str] = mapped_column(Text)
    supporting_evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Critique(Base):
    __tablename__ = "critiques"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE")
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.evidence_id", ondelete="CASCADE")
    )
    impact_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    sjr_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    novelty_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_challenge: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE")
    )
    stage: Mapped[str] = mapped_column(String(64))
    missing_aspects: Mapped[list[str]] = mapped_column(JSON, default=list)
    completeness_score: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE")
    )
    artifact_uri: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmCache(Base):
    """Cache for LLM-generated decisions; keyed on (cache_key, model_used).

    No FK to runs or evidence — this is a standalone lookup cache. A cache miss
    is always safe (triggers fresh inference); a hit is only served when model_used
    exactly matches the active model fingerprint, ensuring model changes invalidate
    all prior decisions automatically.
    """

    __tablename__ = "llm_cache"

    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_used: Mapped[str] = mapped_column(String(128), primary_key=True)
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# NOTE: LangGraph checkpoint tables (checkpoints, checkpoint_writes,
# checkpoint_migrations) are intentionally absent here. They are created and
# owned by AsyncPostgresSaver.setup() and must not be redefined in our ORM —
# doing so would create a naming conflict and break LangGraph's own migrations.
