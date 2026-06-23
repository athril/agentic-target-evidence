# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ClinicalTrialAgent."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from agents.retrieval.clinical_trial.agent import ClinicalTrialAgent
from mcp_servers.clinicaltrials.tools import TrialRecord
from schemas.evidence import EvidenceType
from tests.agents.conftest import make_task_msg


def _make_records(n: int = 2, scope: str = "abstract") -> list[TrialRecord]:
    return [
        TrialRecord(
            nct_id=f"NCT{1000 + i}",
            title=f"Trial {i}",
            status="COMPLETED",
            phase="PHASE2",
            scope=scope,
        )
        for i in range(n)
    ]


async def test_clinical_trial_agent_returns_evidence_list(run_id, trace_id, ctx):
    msg = make_task_msg(
        "clinical_trial", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with patch(
        "services.retrieval.clinical_trial.search_trials", AsyncMock(return_value=_make_records(4))
    ):
        result = await ClinicalTrialAgent().run(msg, ctx)

    assert result.intent == "result"
    assert len(result.payload) == 4
    assert all(e.evidence_type == EvidenceType.CLINICAL_TRIAL for e in result.payload)


async def test_clinical_trial_agent_preserves_scope(run_id, trace_id, ctx):
    msg = make_task_msg(
        "clinical_trial", {"target_gene": "BRCA1", "disease": "breast cancer"}, run_id, trace_id
    )

    with patch(
        "services.retrieval.clinical_trial.search_trials",
        AsyncMock(return_value=_make_records(1, scope="full_text")),
    ):
        result = await ClinicalTrialAgent().run(msg, ctx)

    assert result.payload[0].scope == "full_text"


async def test_clinical_trial_agent_passes_population(run_id, trace_id, ctx):
    msg = make_task_msg(
        "clinical_trial",
        {"target_gene": "BRCA1", "disease": "breast cancer", "population": "adult"},
        run_id,
        trace_id,
    )
    captured: list[tuple] = []

    async def mock_search(gene, disease, population=None):
        captured.append((gene, disease, population))
        return []

    with patch("services.retrieval.clinical_trial.search_trials", mock_search):
        await ClinicalTrialAgent().run(msg, ctx)

    assert captured[0] == ("BRCA1", "breast cancer", "adult")
