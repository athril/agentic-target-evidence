# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for all test modules."""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import UTC, datetime

import pytest

from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance
from schemas.messages import AgentMessage

# Must run before any test module imports core.persistence.artifact_store (and the
# report agents), since they read RESULTS_DIR into a module-level constant at import
# time. Without this, agent/service tests that exercise the real archive_raw()/
# export_summary_csv() path (mocking only the external API client, not the writer)
# silently write fixture data into the real results/ tree instead of a sandbox.
os.environ.setdefault("RESULTS_DIR", tempfile.mkdtemp(prefix="gtv-test-results-"))


@pytest.fixture
def run_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def trace_id() -> str:
    return "test-trace-" + uuid.uuid4().hex[:8]


@pytest.fixture
def sample_provenance(trace_id: str) -> Provenance:
    return Provenance(
        agent_name="test_agent",
        tool_name="test_tool",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        model_used="test-model",
        trace_id=trace_id,
    )


@pytest.fixture
def sample_evidence(run_id: uuid.UUID, sample_provenance: Provenance) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="BRCA1",
        gene_id="ENSG00000012048",
        disease="breast cancer",
        disease_id="EFO_0000305",
        evidence_type=EvidenceType.ARTICLE,
        scope="abstract",
        source="PMID:12345678",
        source_link="https://pubmed.ncbi.nlm.nih.gov/12345678/",
        provenance=sample_provenance,
        classification=DataClass.NON_SENSITIVE,
    )


@pytest.fixture
def sensitive_evidence(run_id: uuid.UUID, sample_provenance: Provenance) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="BRCA1",
        gene_id="ENSG00000012048",
        disease="breast cancer",
        disease_id="EFO_0000305",
        evidence_type=EvidenceType.OMICS,
        scope="full_text",
        source="internal-dataset-001",
        source_link="file:///data/internal/brca1_omics.csv",
        provenance=sample_provenance,
        classification=DataClass.SENSITIVE,
    )


@pytest.fixture
def sample_message(run_id: uuid.UUID, trace_id: str, sample_evidence: Evidence) -> AgentMessage:
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="planner",
        to_agent="literature",
        intent="task",
        task_spec={"target_gene": "BRCA1", "disease": "breast cancer"},
        payload=[sample_evidence],
        trace_id=trace_id,
    )
