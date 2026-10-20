# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The second screening pass and its cache.

Regression lock: pass 2 looked up its verdict under pass 1's key and accepted any hit,
including pass 1's own ``uncertain``, so whenever the LLM cache was on it re-screened
nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capabilities.target_validation.workflow import build_graph
from core.routing.router import Router
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance, source_fingerprint

_RUN_ID = uuid.uuid4()
_WF = "capabilities.target_validation.workflow"


def _ev(source: str, verdict: str, *, full_text: bool) -> Evidence:
    extra: dict = {"title": "t", "abstract": "a", "screening_verdict": {"verdict": verdict}}
    if full_text:
        extra["full_text"] = "Body naming GENE1."
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=_RUN_ID,
        gene="GENE1",
        disease="disease one",
        evidence_type=EvidenceType.ARTICLE,
        scope="full_text" if full_text else "abstract",
        source=source,
        source_link="https://example.org",
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(agent_name="t", timestamp=datetime.now(UTC), trace_id="t"),
        extra=extra,
    )


@pytest.fixture(scope="module")
def node():
    graph = build_graph(MagicMock(spec=Router), checkpointer=None)
    return graph.nodes["screening_second"]


def _state(*evidence: Evidence) -> dict:
    return {
        "target_gene": "GENE1",
        "disease": "disease one",
        "direction": "inhibit",
        "run_id": _RUN_ID,
        "model_fingerprint": "fp",
        "force_refresh": False,
        "screened_evidence": list(evidence),
    }


def _key(ev: Evidence, screening_pass: str = "first") -> str:
    return source_fingerprint(
        "GENE1", "disease one", "inhibit", "article", ev.source, screening_pass=screening_pass
    )


def _rejudge(verdict: str):
    async def run(msg, _ctx):
        return MagicMock(
            payload=[
                e.model_copy(
                    update={
                        "extra": {
                            **e.extra,
                            "screening_verdict": {"verdict": verdict, "rationale": "read it"},
                        }
                    }
                )
                for e in msg.payload
            ]
        )

    return run


async def _invoke(node, state: dict, store: dict, run):
    async def get(key, _model):
        return store.get(key)

    async def put(key, _model, _kind, payload):
        store[key] = payload

    with (
        patch(f"{_WF}._enrich_uncertain_with_full_text", AsyncMock(side_effect=lambda e, **_: e)),
        patch(f"{_WF}._llm_cache_get", get),
        patch(f"{_WF}._llm_cache_set", put),
        patch(f"{_WF}.ScreeningAgent.run", AsyncMock(side_effect=run)) as agent,
    ):
        result = await node.ainvoke(state)
    return result, agent


def test_the_second_pass_key_differs_from_the_first_and_leaves_it_unchanged():
    ev = _ev("PMID:1", "uncertain", full_text=True)
    assert _key(ev, "second") != _key(ev)
    assert _key(ev) == source_fingerprint("GENE1", "disease one", "inhibit", "article", "PMID:1")


async def test_a_cached_first_pass_uncertain_does_not_short_circuit_pass_two(node):
    ev = _ev("PMID:1", "uncertain", full_text=True)
    store = {
        _key(ev): {"verdict": "uncertain", "rationale": "abstract only"},
        _key(ev, "second"): {"verdict": "uncertain", "rationale": "an old non-decision"},
    }
    result, agent = await _invoke(node, _state(ev), store, _rejudge("keep"))

    agent.assert_awaited_once()
    [judged] = result["screened_evidence"]
    assert judged.extra["screening_verdict"]["verdict"] == "keep"
    assert store[_key(ev, "second")]["verdict"] == "keep"
    assert store[_key(ev)]["verdict"] == "uncertain"  # the pass-1 entry is untouched


@pytest.mark.parametrize("verdict", ["keep", "drop"])
async def test_a_cached_decisive_second_pass_verdict_is_reused(node, verdict):
    ev = _ev("PMID:1", "uncertain", full_text=True)
    store = {_key(ev, "second"): {"verdict": verdict, "rationale": "read the results"}}
    result, agent = await _invoke(node, _state(ev), store, _rejudge("keep"))

    agent.assert_not_awaited()
    [judged] = result["screened_evidence"]
    assert judged.extra["screening_verdict"]["verdict"] == verdict


async def test_only_uncertain_full_text_items_are_rejudged_and_only_decisions_cached(node):
    decided = _ev("PMID:1", "keep", full_text=False)
    no_text = _ev("PMID:2", "uncertain", full_text=False)
    with_text = _ev("PMID:3", "uncertain", full_text=True)
    store: dict = {}
    result, agent = await _invoke(
        node, _state(decided, no_text, with_text), store, _rejudge("uncertain")
    )

    [call] = agent.await_args_list
    assert [e.source for e in call.args[0].payload] == ["PMID:3"]
    by_source = {
        e.source: e.extra["screening_verdict"]["verdict"] for e in result["screened_evidence"]
    }
    assert by_source == {"PMID:1": "keep", "PMID:2": "uncertain", "PMID:3": "uncertain"}
    assert store == {}  # a second-pass "uncertain" is never cached
