# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Cached literature gets the papers of role queries added since it was cached."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.retrieval.literature.agent import ROLE_QUERY_VERSION
from capabilities.target_validation.workflow import build_graph
from core.routing.router import Router
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance

_WF = "capabilities.target_validation.workflow"


@pytest.fixture(scope="module")
def literature_node():
    return build_graph(MagicMock(spec=Router), checkpointer=None).nodes["literature"]


def _row(pmid: str, **extra) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENE-A",
        disease="disease one",
        evidence_type=EvidenceType.ARTICLE,
        scope="abstract",
        source=f"PMID:{pmid}",
        source_link="https://example.org",
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(agent_name="t", timestamp=datetime.now(UTC), trace_id="t"),
        extra={"pmid": pmid, "publication_types": [], "registry_ids": [], **extra},
    )


def _state() -> dict:
    return {
        "target_gene": "GENE-A",
        "disease": "disease one",
        "direction": "inhibit",
        "run_id": uuid.uuid4(),
        "gene_id": "",
        "disease_id": "",
    }


@pytest.mark.parametrize(
    ("cached_extra", "asked"), [({}, True), ({"role_query_version": ROLE_QUERY_VERSION}, False)]
)
async def test_cached_literature_runs_only_the_role_queries_it_lacks(
    literature_node, cached_extra, asked
):
    cached = [_row("1", **cached_extra)]
    added = [_row("2", retrieval_role="target_perturbation_models")]
    roles = AsyncMock(return_value=added)
    with (
        patch(f"{_WF}._evidence_cache_lookup", AsyncMock(return_value=cached)),
        patch(f"{_WF}.role_query_evidence", roles),
        patch(f"{_WF}._model_phenotypes", AsyncMock(return_value=["Phenotype Two"])),
        patch(f"{_WF}._disease_mesh", AsyncMock(return_value="Disease One")),
        patch(f"{_WF}._persist_evidence", AsyncMock()),
    ):
        out = await literature_node.ainvoke(_state())

    if asked:
        assert roles.await_args.kwargs["seen"] == {"1"}
        assert roles.await_args.kwargs["phenotypes"] == ["Phenotype Two"]
        assert [e.source for e in out["literature_evidence"]] == ["PMID:1", "PMID:2"]
    else:
        roles.assert_not_awaited()
        assert out["literature_evidence"] == cached


async def test_no_model_phenotype_is_cached_and_a_failed_lookup_is_not(monkeypatch):
    import capabilities.target_validation.workflow as wf
    import services.evidence.model_phenotypes as mp

    store: dict[str, object] = {}

    async def get(key, _model):
        return store.get(key)

    async def put(key, _model, _kind, payload):
        store[key] = payload

    monkeypatch.setattr(wf, "_llm_cache_get", get)
    monkeypatch.setattr(wf, "_llm_cache_set", put)
    monkeypatch.setattr(wf, "_MODEL_PHENOTYPES", {})
    lookup = AsyncMock(side_effect=[None, [], AssertionError("cached")])
    monkeypatch.setattr(mp, "find_model_phenotypes", lookup)
    monkeypatch.setattr(wf, "_disease_mesh", AsyncMock(return_value="Disease One"))

    assert await wf._model_phenotypes("disease one") == []  # failed: not stored
    assert store == {}
    assert await wf._model_phenotypes("disease one") == []  # none: stored
    assert await wf._model_phenotypes("disease one") == []  # from memory, no lookup
    assert lookup.await_count == 2 and len(store) == 1
