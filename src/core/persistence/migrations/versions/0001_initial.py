# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Initial schema: all tables + pgvector extension

Revision ID: 0001
Revises:
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector extension must exist before the Vector column can be created
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "runs",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("target_gene", sa.String(64), nullable=False),
        sa.Column("disease", sa.String(256), nullable=False),
        sa.Column("population", sa.String(256), nullable=True),
        sa.Column("tissue", sa.String(256), nullable=True),
        sa.Column("user_request", sa.Text, nullable=False),
        sa.Column("step_budget_total", sa.Integer, nullable=False, server_default="200"),
        sa.Column("step_budget_consumed", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("direction", sa.String(16), nullable=False, server_default="unspecified"),
        sa.Column("model_fingerprint", sa.String(256), nullable=True),
        sa.Column("force_refresh", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("rerun_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_runs_target", "runs", ["target_gene", "disease", "direction"])

    op.create_table(
        "evidence",
        sa.Column("evidence_id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="1.0"),
        sa.Column("gene", sa.String(64), nullable=False),
        sa.Column("disease", sa.String(256), nullable=False),
        sa.Column("population", sa.String(256), nullable=True),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("source", sa.String(256), nullable=False),
        sa.Column("source_link", sa.Text, nullable=False),
        sa.Column("query_used", sa.Text, nullable=True),
        sa.Column("artifact_uri", sa.Text, nullable=True),
        sa.Column("extra", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("classification", sa.String(16), nullable=False),
        sa.Column("prov_agent_name", sa.String(64), nullable=False),
        sa.Column("prov_tool_name", sa.String(255), nullable=True),
        sa.Column("prov_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prov_model_used", sa.String(128), nullable=True),
        sa.Column("prov_trace_id", sa.String(128), nullable=False),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("gene_id", sa.String(128), nullable=True, server_default=""),
        sa.Column("disease_id", sa.String(128), nullable=True, server_default=""),
        sa.Column("direction", sa.String(16), nullable=False, server_default="unspecified"),
        sa.Column("availability_date", sa.Date(), nullable=True),
    )
    op.create_index("ix_evidence_run_id", "evidence", ["run_id"])
    op.create_index("ix_evidence_target", "evidence", ["gene", "disease", "direction"])

    op.create_table(
        "hypotheses",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("verdict", sa.Boolean, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "experiments",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("supporting_evidence_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "critiques",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("evidence.evidence_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("impact_factor", sa.Float, nullable=True),
        sa.Column("sjr_score", sa.Float, nullable=True),
        sa.Column("novelty_flag", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("quality_challenge", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "reviews",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("missing_aspects", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("completeness_score", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_uri", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "functional_screens",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("gene_symbol", sa.String(64), nullable=False),
        sa.Column("screen_id", sa.String(128), nullable=False),
        sa.Column("cell_line", sa.String(128), nullable=True),
        sa.Column("cancer_type", sa.String(128), nullable=True),
        # Chronos/CERES gene effect score; <= -1 indicates strong dependency
        sa.Column("gene_effect", sa.Float, nullable=True),
        sa.Column("is_essential", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("dataset_version", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_functional_screens_gene", "functional_screens", ["gene_symbol"])
    op.create_index("ix_functional_screens_screen", "functional_screens", ["screen_id"])

    op.create_table(
        "expression_data",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gene_symbol", sa.String(64), nullable=False),
        sa.Column("tissue", sa.String(256), nullable=False),
        sa.Column("tpm_median", sa.Float, nullable=True),
        sa.Column("tpm_q1", sa.Float, nullable=True),
        sa.Column("tpm_q3", sa.Float, nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("differential_expression_padj", sa.Float, nullable=True),
        sa.Column("log2_fold_change", sa.Float, nullable=True),
    )
    op.create_index("ix_expression_data_gene_symbol", "expression_data", ["gene_symbol"])

    op.create_table(
        "gwas_hits",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gene_symbol", sa.String(64), nullable=False),
        sa.Column("trait", sa.String(256), nullable=False),
        sa.Column("pvalue", sa.Float, nullable=True),
        sa.Column("beta", sa.Float, nullable=True),
        sa.Column("odds_ratio", sa.Float, nullable=True),
        sa.Column("study_id", sa.String(128), nullable=True),
        sa.Column("variant_id", sa.String(128), nullable=True),
        sa.Column("lof_score", sa.Float, nullable=True),
        sa.Column("is_lof_intolerant", sa.Boolean, nullable=True),
    )
    op.create_index("ix_gwas_hits_gene_symbol", "gwas_hits", ["gene_symbol"])

    op.create_table(
        "llm_cache",
        sa.Column("cache_key", sa.String(128), nullable=False),
        sa.Column("model_used", sa.String(128), nullable=False),
        sa.Column("decision_type", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("cache_key", "model_used"),
    )


def downgrade() -> None:
    op.drop_table("llm_cache")
    op.drop_index("ix_gwas_hits_gene_symbol", table_name="gwas_hits")
    op.drop_table("gwas_hits")
    op.drop_index("ix_expression_data_gene_symbol", table_name="expression_data")
    op.drop_table("expression_data")
    op.drop_index("ix_functional_screens_screen", table_name="functional_screens")
    op.drop_index("ix_functional_screens_gene", table_name="functional_screens")
    op.drop_table("functional_screens")
    op.drop_table("reports")
    op.drop_table("reviews")
    op.drop_table("critiques")
    op.drop_table("experiments")
    op.drop_table("hypotheses")
    op.drop_index("ix_evidence_target", table_name="evidence")
    op.drop_index("ix_evidence_run_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_runs_target", table_name="runs")
    op.drop_table("runs")
    op.execute("DROP EXTENSION IF EXISTS vector")
