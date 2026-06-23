# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the gbd acquisition node wired into the compiled pipeline graph.

The node is a closure inside build_graph(), so it's exercised the same way the
graph itself invokes it: via the compiled graph's node runnable
(`compiled_graph.nodes["gbd"].ainvoke(state)`), with the module-level cache
lookup / fetch_gbd / persistence boundaries patched.
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
def gbd_node():
    router = MagicMock(spec=Router)
    graph = build_graph(router, checkpointer=None)
    return graph.nodes["gbd"]


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
        evidence_type=EvidenceType.EPIDEMIOLOGY,
        scope="abstract",
        source="gbd:burden:587",
        source_link="https://ghdx.healthdata.org",
        classification=DataClass.NON_SENSITIVE,
        direction=Direction.UNSPECIFIED,
        provenance=Provenance(
            agent_name="gbd",
            tool_name="gbd.get_disease_burden",
            timestamp=datetime.now(UTC),
            trace_id="t",
        ),
    )


async def test_gbd_node_cache_hit_skips_fetch(gbd_node):
    cached = [_evidence_row()]
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup",
            AsyncMock(return_value=cached),
        ),
        patch("capabilities.target_validation.workflow.fetch_gbd", AsyncMock()) as mock_fetch,
    ):
        result = await gbd_node.ainvoke(_state())

    assert result == {"gbd_evidence": cached}
    mock_fetch.assert_not_awaited()


async def test_gbd_node_cache_miss_calls_fetch(gbd_node):
    fresh = [_evidence_row()]
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup",
            AsyncMock(return_value=[]),
        ),
        patch("capabilities.target_validation.workflow.fetch_gbd", AsyncMock(return_value=fresh)),
        patch("capabilities.target_validation.workflow._persist_evidence", AsyncMock()),
    ):
        result = await gbd_node.ainvoke(_state())

    assert result == {"gbd_evidence": fresh}


async def test_gbd_node_force_refresh_bypasses_cache(gbd_node):
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup", AsyncMock()
        ) as mock_cache,
        patch("capabilities.target_validation.workflow.fetch_gbd", AsyncMock(return_value=[])),
        patch("capabilities.target_validation.workflow._persist_evidence", AsyncMock()),
    ):
        await gbd_node.ainvoke(_state(force_refresh=True))

    mock_cache.assert_not_awaited()


async def test_gbd_node_disabled_source_returns_empty_without_failure(gbd_node):
    # GBD_ENABLED=false (or no mapping) degrades to [] via fetch_gbd itself —
    # the node must not record this as a failed source.
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup",
            AsyncMock(return_value=[]),
        ),
        patch("capabilities.target_validation.workflow.fetch_gbd", AsyncMock(return_value=[])),
        patch("capabilities.target_validation.workflow._persist_evidence", AsyncMock()),
    ):
        result = await gbd_node.ainvoke(_state())

    assert result == {"gbd_evidence": []}
    assert "failed_sources" not in result


async def test_gbd_node_exception_is_caught_and_recorded(gbd_node):
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup",
            AsyncMock(return_value=[]),
        ),
        patch(
            "capabilities.target_validation.workflow.fetch_gbd",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await gbd_node.ainvoke(_state())

    assert result == {"gbd_evidence": [], "failed_sources": ["gbd"]}
