# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for agent tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from harness.context import RunContext
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance
from schemas.messages import AgentMessage


@pytest.fixture()
def run_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def trace_id() -> str:
    return "trace-agent-test"


@pytest.fixture()
def mock_router():
    router = MagicMock()
    provider = MagicMock()
    provider.name = "ollama"
    # Router.select returns a (provider, model_name) tuple; agents unpack both.
    router.select.return_value = (provider, "mock-model")
    return router, provider


@pytest.fixture()
def ctx(run_id, trace_id, mock_router):
    router, _ = mock_router
    return RunContext(run_id=run_id, trace_id=trace_id, router=router)


def make_task_msg(
    to_agent: str,
    task_spec: dict,
    run_id: uuid.UUID,
    trace_id: str,
    payload=None,
) -> AgentMessage:
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="planner",
        to_agent=to_agent,
        intent="task",
        task_spec=task_spec,
        payload=payload,
        trace_id=trace_id,
    )


def make_evidence(
    run_id: uuid.UUID,
    trace_id: str,
    *,
    source: str = "PMID:11111",
    evidence_type: EvidenceType = EvidenceType.ARTICLE,
    classification: DataClass = DataClass.NON_SENSITIVE,
    scope: str = "abstract",
    extra: dict | None = None,
) -> Evidence:
    base_extra = {"abstract": "Test abstract for screening.", "title": "Test title"}
    base_extra.update(extra or {})
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        target_gene="BRCA1",
        disease="breast cancer",
        evidence_type=evidence_type,
        scope=scope,
        source=source,
        source_link="https://pubmed.ncbi.nlm.nih.gov/11111/",
        classification=classification,
        provenance=Provenance(
            agent_name="test",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            trace_id=trace_id,
        ),
        extra=base_extra,
    )
