# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for SourceQualityAgent — the pre-lens source-quality scorer.

SJR resolution is deterministic (bundled scimago lookup table), so most of
these tests exercise the table path with no LLM involved. The LLM is only
consulted for `predatory_flag` on sources the table can't resolve.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.screening.source_quality import agent as source_quality_agent
from agents.screening.source_quality.agent import SourceQualityAgent
from core.routing.providers.base import CompletionResult
from schemas.evidence import EvidenceType
from tests.agents.conftest import make_evidence, make_task_msg


def _make_completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content,
        model_used="test-model",
        input_tokens=40,
        output_tokens=60,
        latency_ms=120.0,
    )


@pytest.fixture()
def source_quality_ctx(run_id, trace_id):
    provider = MagicMock()
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    from harness.context import RunContext

    return RunContext(run_id=run_id, trace_id=trace_id, router=router), provider


@pytest.fixture(autouse=True)
def _disable_openalex(monkeypatch):
    # Keep the OpenAlex fallback from making live HTTP calls in unit tests; the
    # gate short-circuits to unmatched. OpenAlex-specific tests patch the
    # resolver directly to override this.
    monkeypatch.setenv("OPENALEX_ENABLED", "false")


async def test_source_quality_agent_resolves_known_journal_without_llm(
    run_id, trace_id, source_quality_ctx, monkeypatch
):
    # SJR lookup is gated off by default (non-commercial license); enable it so
    # the deterministic path resolves "The Lancet" without falling back to the LLM.
    monkeypatch.setenv("SCIMAGO_SJR_ENABLED", "true")
    ctx, provider = source_quality_ctx
    provider.complete = AsyncMock()
    ev = make_evidence(
        run_id,
        trace_id,
        extra={
            "screening_verdict": {"verdict": "keep"},
            "journal": "The Lancet",
            "issn": "0140-6736",
            "pub_year": date.today().year,
        },
    )

    msg = make_task_msg(
        "source_quality",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[ev],
    )

    result = await SourceQualityAgent().run(msg, ctx)

    provider.complete.assert_not_called()
    entry = result.payload["source_quality"][str(ev.evidence_id)]
    assert entry["sjr_quartile"] == "Q1"
    assert entry["sjr_score"] == pytest.approx(0.85)
    assert entry["predatory_flag"] is False
    assert entry["novelty_flag"] is True


async def test_source_quality_agent_handles_matched_sjr_with_no_score(
    run_id, trace_id, source_quality_ctx, monkeypatch
):
    # Some bundled Scimago rows carry a quartile but a blank `sjr` numeric
    # value — matched=True with sjr=None is a real state, not just a type
    # artifact. Must not crash formatting the quality note.
    from mcp_servers.scimago.tools import SjrRecord

    monkeypatch.setenv("SCIMAGO_SJR_ENABLED", "true")
    ctx, provider = source_quality_ctx
    provider.complete = AsyncMock()
    monkeypatch.setattr(
        source_quality_agent,
        "resolve_sjr",
        lambda **kwargs: SjrRecord(
            matched=True,
            match_type="title",
            matched_title="Some Journal",
            sjr=None,
            sjr_quartile="Q2",
            sjr_score=0.6,
        ),
    )
    ev = make_evidence(
        run_id,
        trace_id,
        extra={
            "screening_verdict": {"verdict": "keep"},
            "journal": "Some Journal",
            "pub_year": date.today().year,
        },
    )
    msg = make_task_msg(
        "source_quality",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[ev],
    )

    result = await SourceQualityAgent().run(msg, ctx)

    provider.complete.assert_not_called()
    entry = result.payload["source_quality"][str(ev.evidence_id)]
    assert entry["sjr_quartile"] == "Q2"
    assert entry["quality_note"] == "SJR Q2 (score n/a) — Some Journal"


async def test_source_quality_agent_falls_back_to_llm_for_unmatched_journal(
    run_id, trace_id, source_quality_ctx
):
    ctx, provider = source_quality_ctx
    ev = make_evidence(
        run_id,
        trace_id,
        extra={
            "screening_verdict": {"verdict": "keep"},
            "journal": "Totally Unranked Made-Up Journal of Nothing",
        },
    )
    provider.complete = AsyncMock(
        return_value=_make_completion(
            json.dumps(
                [
                    {
                        "evidence_id": str(ev.evidence_id),
                        "predatory_flag": False,
                        "quality_challenge": "Not Scopus-indexed; no predatory signals.",
                    }
                ]
            )
        )
    )

    msg = make_task_msg(
        "source_quality",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[ev],
    )

    result = await SourceQualityAgent().run(msg, ctx)

    provider.complete.assert_awaited_once()
    entry = result.payload["source_quality"][str(ev.evidence_id)]
    assert entry["sjr_score"] is None
    assert entry["predatory_flag"] is False
    assert entry["quality_note"] == "Not Scopus-indexed; no predatory signals."


async def test_source_quality_agent_openalex_fallback_when_sjr_unmatched(
    run_id, trace_id, source_quality_ctx, monkeypatch
):
    # SJR off (commercial posture). An established OpenAlex match supplies the
    # quality score and settles predatory_flag deterministically — no LLM call.
    from mcp_servers.openalex.tools import OpenAlexJournal

    ctx, provider = source_quality_ctx
    provider.complete = AsyncMock()
    monkeypatch.setattr(
        source_quality_agent,
        "resolve_journal",
        AsyncMock(
            return_value=OpenAlexJournal(
                matched=True,
                match_type="issn",
                display_name="Journal of Real Science",
                two_yr_mean_citedness=9.3,
                h_index=140,
                is_in_doaj=True,
                quality_score=0.85,
                established=True,
            )
        ),
    )
    ev = make_evidence(
        run_id,
        trace_id,
        extra={
            "screening_verdict": {"verdict": "keep"},
            "journal": "Journal of Real Science",
            "pub_year": date.today().year,
        },
    )
    msg = make_task_msg(
        "source_quality",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[ev],
    )

    result = await SourceQualityAgent().run(msg, ctx)

    provider.complete.assert_not_called()
    entry = result.payload["source_quality"][str(ev.evidence_id)]
    assert entry["sjr_score"] == pytest.approx(0.85)
    assert entry["sjr_quartile"] is None  # OpenAlex has no quartiles
    assert entry["impact_factor"] == pytest.approx(9.3)
    assert entry["predatory_flag"] is False
    assert "OpenAlex" in entry["quality_note"]


async def test_source_quality_agent_openalex_unestablished_still_runs_llm(
    run_id, trace_id, source_quality_ctx, monkeypatch
):
    # OpenAlex matches but the journal is not "established" (no DOAJ, low h-index):
    # keep its quality score, but still ask the LLM for the predatory call.
    from mcp_servers.openalex.tools import OpenAlexJournal

    ctx, provider = source_quality_ctx
    monkeypatch.setattr(
        source_quality_agent,
        "resolve_journal",
        AsyncMock(
            return_value=OpenAlexJournal(
                matched=True,
                match_type="title",
                display_name="Small Niche Journal",
                two_yr_mean_citedness=1.2,
                h_index=4,
                is_in_doaj=False,
                quality_score=0.2,
                established=False,
            )
        ),
    )
    ev = make_evidence(
        run_id,
        trace_id,
        extra={"screening_verdict": {"verdict": "keep"}, "journal": "Small Niche Journal"},
    )
    provider.complete = AsyncMock(
        return_value=_make_completion(
            json.dumps(
                [
                    {
                        "evidence_id": str(ev.evidence_id),
                        "predatory_flag": False,
                        "quality_challenge": "Small but legitimate; no predatory signals.",
                    }
                ]
            )
        )
    )
    msg = make_task_msg(
        "source_quality",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[ev],
    )

    result = await SourceQualityAgent().run(msg, ctx)

    provider.complete.assert_awaited_once()
    entry = result.payload["source_quality"][str(ev.evidence_id)]
    assert entry["sjr_score"] == pytest.approx(0.2)  # OpenAlex score preserved
    assert entry["predatory_flag"] is False  # set by the LLM pass
    assert entry["quality_note"] == "Small but legitimate; no predatory signals."


async def test_source_quality_agent_skips_non_literature_evidence(
    run_id, trace_id, source_quality_ctx
):
    ctx, provider = source_quality_ctx
    provider.complete = AsyncMock()
    patent_ev = make_evidence(
        run_id,
        trace_id,
        evidence_type=EvidenceType.PATENT,
        extra={"screening_verdict": {"verdict": "keep"}},
    )

    msg = make_task_msg(
        "source_quality",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[patent_ev],
    )

    result = await SourceQualityAgent().run(msg, ctx)

    provider.complete.assert_not_called()
    assert result.payload == {"source_quality": {}}


async def test_source_quality_agent_skips_dropped_evidence(run_id, trace_id, source_quality_ctx):
    ctx, provider = source_quality_ctx
    dropped_ev = make_evidence(run_id, trace_id, extra={"screening_verdict": {"verdict": "drop"}})

    msg = make_task_msg(
        "source_quality",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[dropped_ev],
    )

    result = await SourceQualityAgent().run(msg, ctx)

    provider.complete.assert_not_called()
    assert result.payload == {"source_quality": {}}


async def test_source_quality_agent_empty_payload_returns_empty(run_id, trace_id, source_quality_ctx):
    ctx, _ = source_quality_ctx
    msg = make_task_msg(
        "source_quality",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[],
    )

    result = await SourceQualityAgent().run(msg, ctx)

    assert result.payload == {"source_quality": {}}


async def test_source_quality_agent_batches_unmatched_evidence(
    run_id, trace_id, source_quality_ctx, monkeypatch
):
    ctx, provider = source_quality_ctx
    monkeypatch.setattr(source_quality_agent, "_BATCH_SIZE", 2)

    evidences = [
        make_evidence(
            run_id,
            trace_id,
            extra={"screening_verdict": {"verdict": "keep"}, "journal": f"Unranked Journal {i}"},
        )
        for i in range(3)
    ]

    def _batch_response(batch):
        assessments = [
            {"evidence_id": str(e.evidence_id), "predatory_flag": None, "quality_challenge": "ok"}
            for e in batch
        ]
        return CompletionResult(
            content=json.dumps(assessments),
            model_used="test",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100.0,
        )

    provider.complete = AsyncMock(
        side_effect=[
            _batch_response(evidences[:2]),
            _batch_response(evidences[2:]),
        ]
    )

    msg = make_task_msg(
        "source_quality",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=evidences,
    )

    result = await SourceQualityAgent().run(msg, ctx)

    assert provider.complete.await_count == 2
    assert len(result.payload["source_quality"]) == 3


async def test_source_quality_agent_unparseable_response_falls_back(
    run_id, trace_id, source_quality_ctx
):
    ctx, provider = source_quality_ctx
    ev = make_evidence(
        run_id,
        trace_id,
        extra={"screening_verdict": {"verdict": "keep"}, "journal": "Unranked Journal X"},
    )
    provider.complete = AsyncMock(return_value=_make_completion("not json"))

    msg = make_task_msg(
        "source_quality",
        {"target_gene": "BRCA1", "disease": "breast cancer"},
        run_id,
        trace_id,
        payload=[ev],
    )

    result = await SourceQualityAgent().run(msg, ctx)

    quality_map = result.payload["source_quality"]
    entry = quality_map[str(ev.evidence_id)]
    assert entry["sjr_score"] is None
    assert entry["predatory_flag"] is None
    assert "unparseable" in entry["quality_note"]
