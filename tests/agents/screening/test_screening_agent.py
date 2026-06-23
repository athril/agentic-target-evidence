# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ScreeningAgent."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.screening.screening.agent import (
    ScreeningAgent,
    _apply_verdict,
    _evidence_to_xml,
    _fulltext_max_chars,
    _parse_verdicts,
)
from core.routing.providers.base import CompletionResult
from schemas.evidence import EvidenceType
from tests.agents.conftest import make_evidence, make_task_msg


def _make_completion(verdicts: list[dict]) -> CompletionResult:
    return CompletionResult(
        content=json.dumps(verdicts),
        model_used="test-model",
        input_tokens=10,
        output_tokens=5,
        latency_ms=100.0,
    )


@pytest.fixture()
def screening_ctx(run_id, trace_id):
    provider = MagicMock()
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")

    from harness.context import RunContext

    ctx = RunContext(run_id=run_id, trace_id=trace_id, router=router)
    return ctx, provider


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_parse_verdicts_positional_fallback(run_id, trace_id):
    ev = make_evidence(run_id, trace_id, source="PMID:11111")
    raw = json.dumps([{"verdict": "keep", "rationale": "Relevant"}])
    result = _parse_verdicts(raw, [ev])
    assert result[0]["verdict"] == "keep"


def test_parse_verdicts_id_based_matching(run_id, trace_id):
    ev1 = make_evidence(run_id, trace_id, source="PMID:11111")
    ev2 = make_evidence(run_id, trace_id, source="PMID:22222")
    # LLM returns in reverse order — should still map correctly
    raw = json.dumps(
        [
            {"id": "PMID:22222", "verdict": "drop", "rationale": "Off-topic"},
            {"id": "PMID:11111", "verdict": "keep", "rationale": "Relevant"},
        ]
    )
    result = _parse_verdicts(raw, [ev1, ev2])
    assert result[0]["verdict"] == "keep"
    assert result[1]["verdict"] == "drop"


def test_parse_verdicts_fallback_on_bad_json(run_id, trace_id):
    batch = [make_evidence(run_id, trace_id) for _ in range(2)]
    result = _parse_verdicts("not json", batch)
    assert len(result) == 2
    assert all(r["verdict"] == "uncertain" for r in result)


def test_apply_verdict_attaches_to_extra(run_id, trace_id):
    ev = make_evidence(run_id, trace_id)
    updated = _apply_verdict(ev, {"verdict": "keep", "rationale": "OK"})
    assert updated.extra["screening_verdict"]["verdict"] == "keep"
    # Original is unchanged (frozen model)
    assert "screening_verdict" not in ev.extra


# ---------------------------------------------------------------------------
# ScreeningAgent.act()
# ---------------------------------------------------------------------------


async def test_screening_agent_first_pass_classifies_all(run_id, trace_id, screening_ctx):
    ctx, provider = screening_ctx
    evidences = [make_evidence(run_id, trace_id) for _ in range(3)]
    verdicts = [
        {"verdict": "keep", "rationale": "Relevant"},
        {"verdict": "drop", "rationale": "Off-topic"},
        {"verdict": "uncertain", "rationale": "Needs full text"},
    ]
    provider.complete = AsyncMock(return_value=_make_completion(verdicts))

    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=evidences,
    )
    result = await ScreeningAgent().run(msg, ctx)

    assert result.intent == "result"
    assert len(result.payload) == 3
    verdicts_out = [e.extra["screening_verdict"]["verdict"] for e in result.payload]
    assert verdicts_out == ["keep", "drop", "uncertain"]


async def test_screening_agent_empty_payload_returns_empty(run_id, trace_id, screening_ctx):
    ctx, _ = screening_ctx
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=[],
    )
    result = await ScreeningAgent().run(msg, ctx)
    assert result.payload == []


async def test_screening_agent_drops_missing_abstract_without_llm(run_id, trace_id, screening_ctx):
    ctx, provider = screening_ctx
    ev_with_abstract = make_evidence(run_id, trace_id, source="PMID:11111")
    ev_no_abstract = make_evidence(run_id, trace_id, source="PMID:22222", extra={"abstract": ""})
    provider.complete = AsyncMock(
        return_value=_make_completion([{"verdict": "keep", "rationale": "Relevant"}])
    )

    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=[ev_with_abstract, ev_no_abstract],
    )
    result = await ScreeningAgent().run(msg, ctx=ctx)

    # LLM called only once (for the one evidence that has an abstract)
    provider.complete.assert_awaited_once()
    assert len(result.payload) == 2
    by_source = {e.source: e.extra["screening_verdict"]["verdict"] for e in result.payload}
    assert by_source["PMID:22222"] == "drop"


async def test_screening_agent_second_pass_only_rescreens_uncertain_full_text(
    run_id, trace_id, screening_ctx
):
    ctx, provider = screening_ctx

    keep_ev = make_evidence(run_id, trace_id, extra={"screening_verdict": {"verdict": "keep"}})
    uncertain_abstract = make_evidence(
        run_id, trace_id, extra={"screening_verdict": {"verdict": "uncertain"}}, scope="abstract"
    )
    uncertain_full = make_evidence(
        run_id, trace_id, extra={"screening_verdict": {"verdict": "uncertain"}}, scope="full_text"
    )

    provider.complete = AsyncMock(
        return_value=_make_completion([{"verdict": "keep", "rationale": "Confirmed"}])
    )

    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "second"},
        run_id,
        trace_id,
        payload=[keep_ev, uncertain_abstract, uncertain_full],
    )
    result = await ScreeningAgent().run(msg, ctx)

    # Only uncertain_full was re-screened; provider called exactly once
    provider.complete.assert_awaited_once()
    # Result has all 3 items (keep + abstract uncertain + re-screened full text)
    assert len(result.payload) == 3


async def test_screening_agent_second_pass_no_uncertain_full_text_is_noop(
    run_id, trace_id, screening_ctx
):
    ctx, provider = screening_ctx
    evidence = make_evidence(run_id, trace_id, extra={"screening_verdict": {"verdict": "keep"}})

    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "second"},
        run_id,
        trace_id,
        payload=[evidence],
    )
    result = await ScreeningAgent().run(msg, ctx)

    provider.complete.assert_not_called()
    assert len(result.payload) == 1


# ---------------------------------------------------------------------------
# _evidence_to_xml full-text rendering
# ---------------------------------------------------------------------------


def test_evidence_to_xml_omits_full_text_block_for_abstract_scope(run_id, trace_id):
    ev = make_evidence(run_id, trace_id, scope="abstract", extra={"full_text": "Body prose."})
    xml = _evidence_to_xml(ev)
    assert "<full_text>" not in xml


def test_evidence_to_xml_includes_full_text_block_for_full_text_scope(run_id, trace_id):
    ev = make_evidence(
        run_id, trace_id, scope="full_text", extra={"full_text": "Detailed body prose."}
    )
    xml = _evidence_to_xml(ev)
    assert "<full_text>Detailed body prose.</full_text>" in xml


def test_evidence_to_xml_omits_full_text_block_when_body_missing(run_id, trace_id):
    """scope upgraded to full_text but no body fetched (e.g. non-OA article)."""
    ev = make_evidence(run_id, trace_id, scope="full_text", extra={})
    xml = _evidence_to_xml(ev)
    assert "<full_text>" not in xml


def test_evidence_to_xml_truncates_full_text_at_configured_cap(run_id, trace_id, monkeypatch):
    monkeypatch.setenv("SCREEN_FULLTEXT_MAX_CHARS", "20")
    body = "x" * 100
    ev = make_evidence(run_id, trace_id, scope="full_text", extra={"full_text": body})
    xml = _evidence_to_xml(ev)
    assert "x" * 20 in xml
    assert "…[truncated]" in xml
    assert "x" * 21 not in xml


def test_evidence_to_xml_disables_full_text_when_cap_is_zero(run_id, trace_id, monkeypatch):
    monkeypatch.setenv("SCREEN_FULLTEXT_MAX_CHARS", "0")
    ev = make_evidence(run_id, trace_id, scope="full_text", extra={"full_text": "Body prose."})
    xml = _evidence_to_xml(ev)
    assert "<full_text>" not in xml


def test_fulltext_max_chars_defaults_and_reads_env(monkeypatch):
    monkeypatch.delenv("SCREEN_FULLTEXT_MAX_CHARS", raising=False)
    assert _fulltext_max_chars() == 8000
    monkeypatch.setenv("SCREEN_FULLTEXT_MAX_CHARS", "500")
    assert _fulltext_max_chars() == 500
    monkeypatch.setenv("SCREEN_FULLTEXT_MAX_CHARS", "not-an-int")
    assert _fulltext_max_chars() == 8000


async def test_screening_agent_clinical_trial_gene_in_eligibility_reaches_llm(
    run_id, trace_id, screening_ctx
):
    """Regression: gene mentioned only in eligibility_criteria must appear in the
    <abstract> sent to the LLM, not be silently dropped because brief_summary
    never names the gene."""
    ctx, provider = screening_ctx

    captured_user_messages: list[str] = []

    async def _capture_complete(req):
        captured_user_messages.append(req.messages[-1]["content"])
        return _make_completion(
            [{"id": "NCT05213624", "verdict": "keep", "rationale": "TRPC6 mutation trial"}]
        )

    provider.complete = _capture_complete

    trial_ev = make_evidence(
        run_id,
        trace_id,
        source="NCT05213624",
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        extra={
            "title": "BI 764198 in Primary FSGS",
            "brief_summary": "A study of BI 764198 in FSGS patients.",
            "interventions": ["BI 764198"],
            "participation_criteria": {
                "eligibility_criteria": "Inclusion: documented (TRPC6) gene mutation causing FSGS."
            },
        },
    )

    msg = make_task_msg(
        "screening",
        {"target_gene": "TRPC6", "disease": "FSGS", "pass_type": "first"},
        run_id,
        trace_id,
        payload=[trial_ev],
    )
    result = await ScreeningAgent().run(msg, ctx)

    assert len(captured_user_messages) == 1
    llm_input = captured_user_messages[0]
    assert "TRPC6" in llm_input, "Gene name must reach the LLM via eligibility text"
    assert "BI 764198" in llm_input, "Intervention name must reach the LLM"

    assert len(result.payload) == 1
    assert result.payload[0].extra["screening_verdict"]["verdict"] == "keep"
