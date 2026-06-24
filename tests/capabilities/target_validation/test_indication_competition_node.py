# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the indication_competition acquisition node wired into the
compiled pipeline graph.

The node is a closure inside build_graph(), so it's exercised the same way the
graph itself invokes it: via the compiled graph's node runnable
(`compiled_graph.nodes["indication_competition"].ainvoke(state)`), with the
module-level cache lookup / fetch_indication_competition / persistence
boundaries patched. Mirrors test_gbd_node.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capabilities.target_validation.workflow import build_graph
from core.routing.router import Router
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType, Provenance

_RUN_ID = uuid.uuid4()


@pytest.fixture(scope="module")
def competition_node():
    router = MagicMock(spec=Router)
    graph = build_graph(router, checkpointer=None)
    return graph.nodes["indication_competition"]


def _state(**overrides) -> dict:
    base = {
        "target_gene": "PCSK9",
        "disease": "type 2 diabetes",
        "run_id": _RUN_ID,
        "force_refresh": False,
        "gene_id": "",
        "disease_id": "",
        "direction": "unspecified",
    }
    base.update(overrides)
    return base


def _evidence_row() -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene="PCSK9",
        disease="type 2 diabetes",
        evidence_type=EvidenceType.COMPETITION,
        scope="abstract",
        source="competition:indication:type_2_diabetes",
        source_link="https://api.fda.gov",
        classification=DataClass.NON_SENSITIVE,
        direction=Direction.UNSPECIFIED,
        provenance=Provenance(
            agent_name="indication_competition",
            tool_name="openfda.count_indication_drugs+clinicaltrials.count_condition_trials",
            timestamp=datetime.now(UTC),
            trace_id="t",
        ),
        extra={
            "approved_drug_count": 1,
            "active_trial_count": 120,
            "phase3_trial_count": 30,
            "total_trial_count": 500,
            "text": "1 approved drug; 500 trials, 120 active, 30 in Phase 3.",
        },
    )


async def test_competition_node_cache_hit_skips_fetch(competition_node):
    cached = [_evidence_row()]
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup",
            AsyncMock(return_value=cached),
        ),
        patch(
            "capabilities.target_validation.workflow.fetch_indication_competition", AsyncMock()
        ) as mock_fetch,
    ):
        result = await competition_node.ainvoke(_state())

    assert result == {"competition_evidence": cached}
    mock_fetch.assert_not_awaited()


async def test_competition_node_cache_lookup_uses_competition_type(competition_node):
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup",
            AsyncMock(return_value=[]),
        ) as mock_cache,
        patch(
            "capabilities.target_validation.workflow.fetch_indication_competition",
            AsyncMock(return_value=[]),
        ),
        patch("capabilities.target_validation.workflow._persist_evidence", AsyncMock()),
    ):
        await competition_node.ainvoke(_state())

    mock_cache.assert_awaited_once_with("PCSK9", "type 2 diabetes", "unspecified", "competition")


async def test_competition_node_cache_miss_calls_fetch(competition_node):
    fresh = [_evidence_row()]
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup",
            AsyncMock(return_value=[]),
        ),
        patch(
            "capabilities.target_validation.workflow.fetch_indication_competition",
            AsyncMock(return_value=fresh),
        ),
        patch("capabilities.target_validation.workflow._persist_evidence", AsyncMock()),
    ):
        result = await competition_node.ainvoke(_state())

    assert result == {"competition_evidence": fresh}


async def test_competition_node_force_refresh_bypasses_cache(competition_node):
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup", AsyncMock()
        ) as mock_cache,
        patch(
            "capabilities.target_validation.workflow.fetch_indication_competition",
            AsyncMock(return_value=[]),
        ),
        patch("capabilities.target_validation.workflow._persist_evidence", AsyncMock()),
    ):
        await competition_node.ainvoke(_state(force_refresh=True))

    mock_cache.assert_not_awaited()


async def test_competition_node_no_mapping_returns_empty_without_failure(competition_node):
    # Both sources unmapped degrades to [] via fetch_indication_competition itself —
    # the node must not record this as a failed source.
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup",
            AsyncMock(return_value=[]),
        ),
        patch(
            "capabilities.target_validation.workflow.fetch_indication_competition",
            AsyncMock(return_value=[]),
        ),
        patch("capabilities.target_validation.workflow._persist_evidence", AsyncMock()),
    ):
        result = await competition_node.ainvoke(_state())

    assert result == {"competition_evidence": []}
    assert "failed_sources" not in result


async def test_competition_node_exception_is_caught_and_recorded(competition_node):
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup",
            AsyncMock(return_value=[]),
        ),
        patch(
            "capabilities.target_validation.workflow.fetch_indication_competition",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await competition_node.ainvoke(_state())

    assert result == {
        "competition_evidence": [],
        "failed_sources": ["indication_competition"],
    }
