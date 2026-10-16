# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The genetics acquisition node's evidence cache.

Regression lock for a panel in which every genetics lens ran without gnomAD, ClinVar,
GWAS or the gene-disease curations: the cached Open Targets association (also of type
`genetics`) counted as a cache hit, so the genetics agent never ran, and the agent's own
rows were stored without the run's direction, so a direction-keyed lookup never found them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capabilities.target_validation.workflow import (
    GENETICS_CACHE,
    _complete_producer_cache,
    build_graph,
)
from core.routing.router import Router
from schemas.evidence import DataClass, Direction, Evidence, EvidenceType, Provenance

_RUN_ID = uuid.uuid4()
_T0 = datetime(2026, 10, 14, tzinfo=UTC)


def _ev(
    source: str,
    *,
    agent: str = "genetics",
    etype: EvidenceType = EvidenceType.CONSTRAINT,
    when: datetime = _T0,
    extra: dict | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene="GENE1",
        disease="disease one",
        evidence_type=etype,
        scope="abstract",
        source=source,
        source_link="https://example.org",
        classification=DataClass.NON_SENSITIVE,
        direction=Direction.UNSPECIFIED,
        extra=extra or {},
        provenance=Provenance(agent_name=agent, timestamp=when, trace_id="t"),
    )


_OT_ROW = _ev(
    "opentargets:ENSG1:MONDO_1",
    agent="services/retrieval/opentargets",
    etype=EvidenceType.GENETICS,
)


def test_another_producers_row_of_the_same_type_is_not_a_hit():
    assert _complete_producer_cache([_OT_ROW], GENETICS_CACHE) == []


def test_a_retrieval_without_its_core_source_is_not_a_hit():
    partial = [_ev("gwas_catalog:GCST1", etype=EvidenceType.GENETICS)]
    assert _complete_producer_cache(partial, GENETICS_CACHE) == []


def test_complete_retrieval_is_a_hit_keeping_only_its_own_rows():
    rows = [_OT_ROW, _ev("gnomad:ENSG1"), _ev("gnomad_clinvar:ENSG1")]
    hit = _complete_producer_cache(rows, GENETICS_CACHE)
    assert sorted(e.source for e in hit) == ["gnomad:ENSG1", "gnomad_clinvar:ENSG1"]


def test_repeated_retrievals_keep_the_newest_row_per_source():
    old = _ev("gnomad:ENSG1", extra={"loeuf": 0.9})
    new = _ev("gnomad:ENSG1", when=_T0 + timedelta(days=1), extra={"loeuf": 1.1})
    hit = _complete_producer_cache([old, new], GENETICS_CACHE)
    assert [e.extra["loeuf"] for e in hit] == [1.1]


def test_rows_from_an_older_retrieval_version_are_not_reused():
    spec = GENETICS_CACHE.__class__(
        producer="genetics", required_sources=("gnomad:",), retrieval_version=2
    )
    assert _complete_producer_cache([_ev("gnomad:ENSG1")], spec) == []
    stamped = _ev("gnomad:ENSG1", extra={"retrieval_version": 2})
    assert _complete_producer_cache([stamped], spec) == [stamped]


@pytest.fixture(scope="module")
def genetics_node():
    graph = build_graph(MagicMock(spec=Router), checkpointer=None)
    return graph.nodes["genetics"]


def _state(direction: str = "inhibit") -> dict:
    return {
        "target_gene": "GENE1",
        "disease": "disease one",
        "run_id": _RUN_ID,
        "force_refresh": False,
        "gene_id": "",
        "disease_id": "",
        "direction": direction,
    }


async def test_node_looks_up_genetics_for_any_direction(genetics_node):
    cached = [_ev("gnomad:ENSG1")]
    lookup = AsyncMock(return_value=cached)
    with patch("capabilities.target_validation.workflow._evidence_cache_lookup", lookup):
        result = await genetics_node.ainvoke(_state("activate"))
    assert lookup.await_args.args[2] is None  # direction-agnostic
    assert result["genetics_evidence"] == cached


async def test_node_runs_the_agent_when_only_open_targets_is_cached(genetics_node):
    fresh = _ev("gnomad:ENSG1")
    agent_reply = MagicMock(payload=[fresh])
    persist = AsyncMock()
    with (
        patch(
            "capabilities.target_validation.workflow._evidence_cache_lookup",
            AsyncMock(return_value=[_OT_ROW]),
        ),
        patch(
            "capabilities.target_validation.workflow.GeneticsAgent.run",
            AsyncMock(return_value=agent_reply),
        ) as run,
        patch("capabilities.target_validation.workflow._persist_evidence", persist),
    ):
        result = await genetics_node.ainvoke(_state())
    run.assert_awaited_once()
    [persisted] = persist.await_args.args[0]
    assert persisted.extra["retrieval_version"] == GENETICS_CACHE.retrieval_version
    assert [e.source for e in result["genetics_evidence"]] == ["gnomad:ENSG1"]
