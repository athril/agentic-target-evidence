# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for _lens_citations.py — the per-axis literature citation step."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from agents.interpretation._lens_citations import (
    CANDIDATES,
    MAX_PICKS,
    _parse_picks,
    add_literature_support,
)
from core.routing.providers.base import CompletionResult
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance
from schemas.verdicts import AxisVerdict, LensVerdict

_RUN = uuid.uuid4()


def _claim(text: str, et: EvidenceType = EvidenceType.ARTICLE) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=uuid.uuid4(),
        run_id=_RUN,
        gene="TRPC6",
        disease="FSGS",
        evidence_type=et,
        claim_text=text,
        provenance=Provenance(
            agent_name="test", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        classification=DataClass.NON_SENSITIVE,
    )


def _verdict(axes: list[AxisVerdict]) -> LensVerdict:
    return LensVerdict(
        run_id=_RUN,
        trace_id="t",
        lens="biology",
        target_gene="TRPC6",
        disease="FSGS",
        overall_verdict="support",
        confidence=0.9,
        axes=axes,
    )


def _ctx(embed_vectors, reply: str):
    """A RunContext whose embedder maps texts to ``embed_vectors(text)`` and whose
    model always returns ``reply``."""
    embedder = MagicMock()
    embedder.embed = AsyncMock(side_effect=lambda texts: [embed_vectors(t) for t in texts])
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=CompletionResult(
            content=reply, model_used="m", input_tokens=1, output_tokens=1, latency_ms=1.0
        )
    )
    router = MagicMock()
    router.get_provider.return_value = embedder
    router.select.return_value = (provider, "mock-model")
    return RunContext(run_id=_RUN, trace_id="t", router=router), provider


def _vec(text: str) -> list[float]:
    # Two-dimensional toy embedding: "inhibitor" texts point one way, the rest the other.
    return [1.0, 0.0] if "inhibitor" in text else [0.0, 1.0]


async def test_picks_are_added_and_recorded():
    papers = [_claim("Z3571, a TRPC6 inhibitor, works in FSGS models."), _claim("Unrelated.")]
    axis = AxisVerdict(axis="druggability", verdict=True, rationale="Known inhibitors exist.")
    ctx, provider = _ctx(_vec, json.dumps({"supports": [{"n": 1, "reason": "inhibitor"}]}))

    out = await add_literature_support(
        _verdict([axis]), papers, ctx, DataClass.NON_SENSITIVE, "biology_lens"
    )

    assert out.axes[0].supporting_claim_ids == [str(papers[0].evidence_id)]
    assert out.axes[0].added_claim_ids == [str(papers[0].evidence_id)]
    sent = provider.complete.await_args.args[0].messages[0]["content"]
    assert sent.index("Z3571") < sent.index("Unrelated")  # most similar candidate first


async def test_only_axes_with_a_verdict_and_only_literature_are_considered():
    paper = _claim("Some finding.")
    record = _claim("UniProt record", EvidenceType.DRUGGABILITY)
    uncertain = AxisVerdict(axis="developability", verdict=None)
    ctx, provider = _ctx(_vec, json.dumps({"supports": [{"n": 1}]}))

    out = await add_literature_support(
        _verdict([uncertain]), [paper, record], ctx, DataClass.NON_SENSITIVE, "biology_lens"
    )

    provider.complete.assert_not_awaited()
    assert out.axes[0].supporting_claim_ids == []


async def test_existing_citations_are_kept_and_not_duplicated():
    paper = _claim("TRPC6 inhibitor paper")
    axis = AxisVerdict(
        axis="druggability", verdict=True, supporting_claim_ids=[str(paper.evidence_id)]
    )
    ctx, _ = _ctx(_vec, json.dumps({"supports": [{"n": 1}]}))
    out = await add_literature_support(
        _verdict([axis]), [paper], ctx, DataClass.NON_SENSITIVE, "biology_lens"
    )
    assert out.axes[0].supporting_claim_ids == [str(paper.evidence_id)]
    assert out.axes[0].added_claim_ids == []


async def test_degrades_to_the_lens_citations_without_an_embedder():
    paper = _claim("TRPC6 inhibitor paper")
    axis = AxisVerdict(axis="druggability", verdict=True)
    router = MagicMock()
    router.get_provider.side_effect = KeyError("ollama")
    ctx = RunContext(run_id=_RUN, trace_id="t", router=router)
    verdict = _verdict([axis])
    out = await add_literature_support(verdict, [paper], ctx, DataClass.NON_SENSITIVE, "x")
    assert out == verdict


async def test_unparseable_reply_adds_nothing():
    paper = _claim("TRPC6 inhibitor paper")
    axis = AxisVerdict(axis="druggability", verdict=True)
    ctx, _ = _ctx(_vec, "I think claim one is good")
    out = await add_literature_support(
        _verdict([axis]), [paper], ctx, DataClass.NON_SENSITIVE, "biology_lens"
    )
    assert out.axes[0].supporting_claim_ids == []


def test_parse_picks_bounds_dedupes_and_caps():
    raw = json.dumps({"supports": [{"n": 2}, {"n": 2}, {"n": 0}, {"n": 99}, "3", {"n": "x"}]})
    assert _parse_picks(raw, 5) == [1, 2]
    many = json.dumps({"supports": [{"n": i} for i in range(1, CANDIDATES + 1)]})
    assert len(_parse_picks(many, CANDIDATES)) == MAX_PICKS


async def test_input_is_independent_of_the_lens_wording():
    """The lens model rephrases between runs; the citation step's input must not move."""
    papers = [_claim("Z3571, a TRPC6 inhibitor, works in FSGS models."), _claim("Unrelated.")]
    sent = []
    for wording in ("Known inhibitors exist.", "Tool compounds such as Z3571 bind TRPC6."):
        axis = AxisVerdict(axis="druggability", verdict=True, rationale=wording)
        ctx, provider = _ctx(_vec, json.dumps({"supports": [{"n": 1}]}))
        await add_literature_support(
            _verdict([axis]), papers, ctx, DataClass.NON_SENSITIVE, "biology_lens"
        )
        sent.append(provider.complete.await_args.args[0].messages[0]["content"])
    assert sent[0] == sent[1]
    assert "structurally amenable" in sent[0]  # the fixed config description


async def test_reviewed_axis_records_picks_even_when_already_cited():
    paper = _claim("TRPC6 inhibitor paper")
    axis = AxisVerdict(
        axis="druggability", verdict=True, supporting_claim_ids=[str(paper.evidence_id)]
    )
    ctx, _ = _ctx(_vec, json.dumps({"supports": [{"n": 1}]}))
    out = await add_literature_support(
        _verdict([axis]), [paper], ctx, DataClass.NON_SENSITIVE, "biology_lens"
    )
    assert out.axes[0].literature_reviewed is True
    assert out.axes[0].literature_picks == [str(paper.evidence_id)]
    assert out.axes[0].added_claim_ids == []  # not "added": the model cited it
