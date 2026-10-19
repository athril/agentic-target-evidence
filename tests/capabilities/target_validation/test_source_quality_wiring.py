# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for source_quality node wiring in the compiled pipeline graph."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from capabilities.target_validation.workflow import (
    _REQUIRED_UPSTREAM,
    CLEAR_FROM_NODE,
    build_graph,
)
from core.routing.router import Router


@pytest.fixture(scope="module")
def compiled_graph():
    router = MagicMock(spec=Router)
    return build_graph(router, checkpointer=None)


def test_source_quality_node_present(compiled_graph):
    assert "source_quality" in compiled_graph.get_graph().nodes


def test_claim_extraction_routes_through_source_quality_and_allele_effects_to_hitl_gate(
    compiled_graph,
):
    edges = {(e.source, e.target) for e in compiled_graph.get_graph().edges}
    assert ("claim_extraction", "source_quality") in edges
    assert ("source_quality", "allele_effects") in edges
    assert ("allele_effects", "hitl_gate") in edges
    assert ("claim_extraction", "hitl_gate") not in edges


def test_restarting_from_claims_or_allele_effects_clears_the_allele_effects():
    for node in ("claim_extraction", "allele_effects", "screening_first"):
        assert CLEAR_FROM_NODE[node]["allele_effects"] == {}
    assert "allele_effects" not in CLEAR_FROM_NODE["hitl_gate"]


def test_screening_first_restart_clears_source_quality():
    assert CLEAR_FROM_NODE["screening_first"]["source_quality"] == {}


def test_hitl_gate_restart_does_not_clear_source_quality():
    assert "source_quality" not in CLEAR_FROM_NODE["hitl_gate"]


def test_hitl_gate_requires_source_quality_upstream():
    assert "source_quality" in _REQUIRED_UPSTREAM["hitl_gate"]


def _kept(n: int) -> list:
    import uuid
    from datetime import UTC, datetime

    from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance

    return [
        Evidence(
            evidence_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            gene="GENE-A",
            disease="DISEASE-A",
            evidence_type=EvidenceType.ARTICLE,
            scope="abstract",
            source=f"PMID:{i}",
            source_link="https://example.org",
            classification=DataClass.NON_SENSITIVE,
            provenance=Provenance(
                agent_name="t", tool_name="t", timestamp=datetime.now(UTC), trace_id="t"
            ),
            extra={"screening_verdict": {"verdict": "keep"}},
        )
        for i in range(n)
    ]


async def test_resumed_run_reuses_carried_source_quality(compiled_graph, monkeypatch):
    """A resume keeps the scores of the run it continues; rescoring the same sources would
    repeat every journal lookup against external quotas (even under force_refresh)."""
    from unittest.mock import AsyncMock

    import capabilities.target_validation.workflow as wf

    kept = _kept(3)
    carried = {str(e.evidence_id): {"sjr_score": 0.6} for e in kept}
    run = AsyncMock(side_effect=AssertionError("must not rescore"))
    monkeypatch.setattr(wf.SourceQualityAgent, "run", run)
    state = {
        "target_gene": "GENE-A",
        "disease": "DISEASE-A",
        "direction": "inhibit",
        "force_refresh": True,
        "screened_evidence": kept,
        "source_quality": carried,
    }
    out = await compiled_graph.nodes["source_quality"].ainvoke(state)
    assert out["source_quality"] == carried
    run.assert_not_called()


def _wire_cache(monkeypatch, wf, cached):
    """Route the node's LLM-cache calls to an in-memory store seeded with ``cached``."""
    store: dict[str, object] = {"value": cached}

    async def get(_key, _model):
        return store["value"]

    async def put(_key, _model, _kind, payload):
        store["value"] = payload

    monkeypatch.setattr(wf, "_llm_cache_get", get)
    monkeypatch.setattr(wf, "_llm_cache_set", put)
    return store


async def test_repeat_run_cache_hit_rekeys_scores_onto_this_runs_documents(
    compiled_graph, monkeypatch
):
    """A cache hit used to return the earlier run's UUID-keyed map, which matched none of
    this run's documents; it must give each current document its cached score."""
    from unittest.mock import AsyncMock

    import capabilities.target_validation.workflow as wf
    from services.evidence.quality_cache import to_cache

    earlier = _kept(2)
    cached = to_cache({str(e.evidence_id): {"sjr_score": 0.85} for e in earlier}, earlier)
    _wire_cache(monkeypatch, wf, cached)
    run = AsyncMock(side_effect=AssertionError("must not rescore"))
    monkeypatch.setattr(wf.SourceQualityAgent, "run", run)

    repeat = _kept(2)  # same PMIDs, new UUIDs
    state = {
        "target_gene": "GENE-A",
        "disease": "DISEASE-A",
        "direction": "inhibit",
        "screened_evidence": repeat,
        "model_fingerprint": "fp",
    }
    out = await compiled_graph.nodes["source_quality"].ainvoke(state)

    assert out["source_quality"] == {str(e.evidence_id): {"sjr_score": 0.85} for e in repeat}
    run.assert_not_called()


async def test_partial_cache_hit_scores_only_the_uncached_documents(compiled_graph, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    import capabilities.target_validation.workflow as wf
    from services.evidence.quality_cache import to_cache

    earlier = _kept(1)
    store = _wire_cache(
        monkeypatch, wf, to_cache({str(earlier[0].evidence_id): {"sjr_score": 0.85}}, earlier)
    )
    repeat = _kept(2)  # PMID:0 cached, PMID:1 new
    fresh = {str(repeat[1].evidence_id): {"sjr_score": 0.4}}
    run = AsyncMock(return_value=MagicMock(payload={"source_quality": fresh}))
    monkeypatch.setattr(wf.SourceQualityAgent, "run", run)
    state = {
        "target_gene": "GENE-A",
        "disease": "DISEASE-A",
        "direction": "inhibit",
        "screened_evidence": repeat,
        "model_fingerprint": "fp",
        "run_id": repeat[0].run_id,
    }
    out = await compiled_graph.nodes["source_quality"].ainvoke(state)

    scored = run.call_args.args[0].payload
    assert [ev.source for ev in scored] == ["PMID:1"]
    assert out["source_quality"] == {
        str(repeat[0].evidence_id): {"sjr_score": 0.85},
        str(repeat[1].evidence_id): {"sjr_score": 0.4},
    }
    assert set(store["value"]["by_source"]) == {"PMID:0", "PMID:1"}


async def test_new_sources_are_scored(compiled_graph, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    import capabilities.target_validation.workflow as wf

    kept = _kept(2)
    fresh = {str(e.evidence_id): {"sjr_score": 0.4} for e in kept}
    run = AsyncMock(return_value=MagicMock(payload={"source_quality": fresh}))
    monkeypatch.setattr(wf.SourceQualityAgent, "run", run)
    state = {
        "target_gene": "GENE-A",
        "disease": "DISEASE-A",
        "direction": "inhibit",
        "force_refresh": True,
        "screened_evidence": kept,
        "source_quality": {str(kept[0].evidence_id): {"sjr_score": 0.6}},  # one missing
        "run_id": kept[0].run_id,
    }
    out = await compiled_graph.nodes["source_quality"].ainvoke(state)
    assert out["source_quality"] == fresh
    run.assert_called_once()


async def test_allele_effects_are_asked_once_per_claim_text_and_reused(compiled_graph, monkeypatch):
    """Typed allele effects are cached per claim text: a repeat run asks only for new claims."""
    import uuid
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    import agents.interpretation.genetics_lens.allele_effects as ae
    import capabilities.target_validation.workflow as wf
    from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance

    def claim(text: str) -> CoreClaim:
        return CoreClaim(
            evidence_id=uuid.uuid4(),
            source_evidence_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            gene="GENE-A",
            disease="disease a",
            evidence_type=EvidenceType.ARTICLE,
            claim_text=text,
            topics=["genetics"],
            provenance=Provenance(
                agent_name="t", tool_name="t", timestamp=datetime.now(UTC), trace_id="t"
            ),
            classification=DataClass.NON_SENSITIVE,
        )

    _wire_cache(monkeypatch, wf, None)
    store: dict[str, object] = {}

    async def get(key, _model):
        return store.get(key)

    async def put(key, _model, _kind, payload):
        store[key] = payload

    monkeypatch.setattr(wf, "_llm_cache_get", get)
    monkeypatch.setattr(wf, "_llm_cache_set", put)
    known = claim("Loss-of-function GENE-A carriers had lower disease a risk.")
    unrelated = claim("GENE-A is expressed in the liver.")  # no genetic change: not asked

    async def answer(claims, **_kw):
        return {str(c.evidence_id): {"gene_change": "loss", "risk": "lower"} for c in claims}

    ask = AsyncMock(side_effect=answer)
    monkeypatch.setattr(ae, "ask_allele_effects", ask)
    state = {
        "extracted_claims": [known, unrelated],
        "model_fingerprint": "fp",
        "run_id": known.run_id,
        "target_gene": "GENE-A",
        "disease": "disease a",
    }

    first = await compiled_graph.nodes["allele_effects"].ainvoke(state)
    repeat = claim(known.claim_text)  # same text, new run ids
    second = await compiled_graph.nodes["allele_effects"].ainvoke(
        {**state, "extracted_claims": [repeat]}
    )

    assert list(first["allele_effects"]) == [str(known.evidence_id)]
    assert ask.await_count == 1  # the repeat came from the cache
    assert second["allele_effects"] == {
        str(repeat.evidence_id): {"gene_change": "loss", "risk": "lower"}
    }


async def test_the_allele_question_reads_at_most_three_batches_indication_first(
    compiled_graph, monkeypatch
):
    import uuid
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    import agents.interpretation.genetics_lens.allele_effects as ae
    import capabilities.target_validation.workflow as wf
    from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance

    def claim(text: str) -> CoreClaim:
        return CoreClaim(
            evidence_id=uuid.uuid4(),
            source_evidence_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            gene="GENE-A",
            disease="disease a",
            evidence_type=EvidenceType.ARTICLE,
            claim_text=text,
            topics=["genetics"],
            provenance=Provenance(agent_name="t", timestamp=datetime.now(UTC), trace_id="t"),
            classification=DataClass.NON_SENSITIVE,
        )

    elsewhere = [claim(f"GENE-A variant {i} raised risk of another trait.") for i in range(40)]
    here = [claim(f"GENE-A variant {i} raised risk of disease a.") for i in range(10)]
    _wire_cache(monkeypatch, wf, None)
    ask = AsyncMock(return_value={})
    monkeypatch.setattr(ae, "ask_allele_effects", ask)

    await compiled_graph.nodes["allele_effects"].ainvoke(
        {
            "extracted_claims": [*elsewhere, *here],
            "model_fingerprint": "",
            "run_id": here[0].run_id,
            "target_gene": "GENE-A",
            "disease": "disease a",
        }
    )

    asked = ask.call_args.args[0]
    assert len(asked) == ae.ALLELE_CLAIMS_MAX == 45
    assert asked[:10] == here  # the indication's claims first
