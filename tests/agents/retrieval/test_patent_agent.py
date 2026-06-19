# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for PatentAgent (MP-31)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from agents.retrieval.patent.agent import PatentAgent
from mcp_servers.uspto.tools import PatentRecord
from schemas.evidence import DataClass, EvidenceType
from tests.agents.conftest import make_task_msg


def _make_records(n: int = 2) -> list[PatentRecord]:
    return [
        PatentRecord(
            patent_id=f"US{1000 + i}",
            app_number=f"1600000{i}",
            title=f"Patent {i}",
            abstract="A method for treating breast cancer using BRCA1.",
            assignee=f"Corp{i}",
            filing_date="2021-01-01",
        )
        for i in range(n)
    ]


async def test_patent_agent_returns_evidence_list(run_id, trace_id, ctx):
    msg = make_task_msg(
        "patent", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with patch(
        "services.retrieval.patent.search_patents", AsyncMock(return_value=_make_records(3))
    ):
        result = await PatentAgent().run(msg, ctx)

    assert result.intent == "result"
    assert len(result.payload) == 3
    assert all(e.evidence_type == EvidenceType.PATENT for e in result.payload)


async def test_patent_agent_classification_is_non_sensitive(run_id, trace_id, ctx):
    msg = make_task_msg(
        "patent", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with patch(
        "services.retrieval.patent.search_patents", AsyncMock(return_value=_make_records(1))
    ):
        result = await PatentAgent().run(msg, ctx)

    assert all(e.classification == DataClass.NON_SENSITIVE for e in result.payload)


async def test_patent_agent_empty_results(run_id, trace_id, ctx):
    msg = make_task_msg(
        "patent", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with patch("services.retrieval.patent.search_patents", AsyncMock(return_value=[])):
        result = await PatentAgent().run(msg, ctx)

    assert result.payload == []
