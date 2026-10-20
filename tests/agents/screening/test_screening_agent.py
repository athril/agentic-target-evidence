# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for ScreeningAgent."""

from __future__ import annotations

import json
import math
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.screening.screening.agent import (
    _MISSING_RATIONALE,
    ScreeningAgent,
    _apply_verdict,
    _evidence_to_xml,
    _fulltext_max_chars,
    _parse_verdicts,
    inherit_faers_verdicts,
    is_reusable_cached_verdict,
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


async def test_screening_agent_keeps_target_drug_trial_without_llm(run_id, trace_id, screening_ctx):
    """A phased trial whose record names the target is kept even if the LLM would drop it."""
    ctx, provider = screening_ctx
    article = make_evidence(run_id, trace_id, source="PMID:11111")
    trial = make_evidence(
        run_id,
        trace_id,
        source="NCT07220083",
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        extra={
            "phase": "PHASE3",
            "interventions": ["DRUG-123", "Placebo"],
            "participation_criteria": {
                "eligibility_criteria": "Inclusion: " + "x" * 700 + " carriers of BRCA1 variants"
            },
        },
    )
    provider.complete = AsyncMock(
        return_value=_make_completion([{"id": "PMID:11111", "verdict": "drop", "rationale": "x"}])
    )
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=[article, trial],
    )
    result = await ScreeningAgent().run(msg, ctx)

    provider.complete.assert_awaited_once()  # only the article went to the LLM
    sent = provider.complete.await_args.args[0].messages[0]["content"]
    assert "NCT07220083" not in sent
    by_source = {e.source: e.extra["screening_verdict"] for e in result.payload}
    assert by_source["NCT07220083"]["verdict"] == "keep"
    assert "names the target gene" in by_source["NCT07220083"]["rationale"]
    assert by_source["PMID:11111"]["verdict"] == "drop"


def _fda(run_id, trace_id, kind: str, drug: str, **extra):
    return make_evidence(
        run_id,
        trace_id,
        source=f"fda:{kind}:{drug}",
        evidence_type=EvidenceType.REGULATORY,
        extra={"abstract": "", "title": "", "drug_name": drug, **extra},
    )


async def test_screening_agent_resolves_openfda_rows(run_id, trace_id, screening_ctx):
    """openFDA labels/FAERS used to be pre-dropped as text-less, starving the regulatory lens.

    On-target label → kept without the LLM; off-target label → LLM sees its text; each
    FAERS bundle inherits its label's verdict; an orphan FAERS bundle is dropped.
    """
    ctx, provider = screening_ctx
    on_label = _fda(
        run_id,
        trace_id,
        "label",
        "OLAPARIB",
        mechanism_of_action="PARP inhibitor; synthetic lethality in BRCA1-mutated cells.",
        text="Drug: OLAPARIB. MoA: PARP inhibitor.",
    )
    off_label = _fda(
        run_id,
        trace_id,
        "label",
        "PRIMIDONE",
        indications_and_usage="Control of focal epileptic seizures.",
        text="Drug: PRIMIDONE. Indication: focal epileptic seizures.",
    )
    on_faers = _fda(run_id, trace_id, "faers", "OLAPARIB", text="OLAPARIB: 900 reports.")
    off_faers = _fda(run_id, trace_id, "faers", "PRIMIDONE", text="PRIMIDONE: 10126 reports.")
    orphan_faers = _fda(run_id, trace_id, "faers", "OTHER", text="OTHER: 5 reports.")
    provider.complete = AsyncMock(
        return_value=_make_completion(
            [{"id": "fda:label:PRIMIDONE", "verdict": "drop", "rationale": "Epilepsy drug"}]
        )
    )
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=[on_label, off_label, on_faers, off_faers, orphan_faers],
    )
    result = await ScreeningAgent().run(msg, ctx)

    provider.complete.assert_awaited_once()
    sent = provider.complete.await_args.args[0].messages[0]["content"]
    assert "Drug: PRIMIDONE. Indication: focal epileptic seizures." in sent
    assert "OLAPARIB" not in sent
    assert "fda:faers:" not in sent

    by_source = {e.source: e.extra["screening_verdict"]["verdict"] for e in result.payload}
    assert by_source == {
        "fda:label:OLAPARIB": "keep",
        "fda:label:PRIMIDONE": "drop",
        "fda:faers:OLAPARIB": "keep",
        "fda:faers:PRIMIDONE": "drop",
        "fda:faers:OTHER": "drop",
    }


def test_inherit_faers_verdicts_finds_label_across_merged_lists(run_id, trace_id):
    """The workflow merges cached label verdicts with a freshly screened FAERS bundle."""
    cached_label = _apply_verdict(
        _fda(run_id, trace_id, "label", "SPARSENTAN"), {"verdict": "keep", "rationale": "LLM"}
    )
    orphaned_faers = _apply_verdict(
        _fda(run_id, trace_id, "faers", "SPARSENTAN"),
        {"verdict": "drop", "rationale": "No screened FDA label for this drug"},
    )
    out = inherit_faers_verdicts([cached_label, orphaned_faers])
    assert [e.extra["screening_verdict"]["verdict"] for e in out] == ["keep", "keep"]
    assert out[1].extra["screening_verdict"]["deterministic"] is True


def test_rule_verdicts_are_marked_and_never_reused():
    assert not is_reusable_cached_verdict(
        {"verdict": "keep", "rationale": "x", "deterministic": True}
    )
    # Legacy cache rows (pre-flag) that stranded every openFDA row.
    assert not is_reusable_cached_verdict(
        {"verdict": "drop", "rationale": "No screenable text available"}
    )
    assert is_reusable_cached_verdict({"verdict": "keep", "rationale": "Relevant"})


def test_llm_non_answers_are_never_reused():
    """A missing or unparseable LLM verdict judged nothing, so a later run re-screens."""
    for rationale in ("verdict missing from LLM response", "LLM response could not be parsed"):
        assert not is_reusable_cached_verdict({"verdict": "uncertain", "rationale": rationale})


async def test_screening_agent_keeps_druggability_records_without_llm(
    run_id, trace_id, screening_ctx
):
    """UniProt/ChEMBL/DGIdb rows keep their content in extra["text"], not an abstract;
    they used to be dropped as having no screenable text."""
    ctx, provider = screening_ctx
    provider.complete = AsyncMock()
    rows = [
        make_evidence(
            run_id,
            trace_id,
            source=src,
            evidence_type=EvidenceType.DRUGGABILITY,
            extra={"abstract": "", "title": "", "text": text},
        )
        for src, text in [
            ("uniprot:P38398", "UniProt P38398: BRCA1. Localization: Nucleus."),
            ("chembl:CHEMBL5990", "ChEMBL CHEMBL5990: Max clinical phase=0.0."),
        ]
    ]
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=rows,
    )
    result = await ScreeningAgent().run(msg, ctx)

    provider.complete.assert_not_awaited()
    assert [e.extra["screening_verdict"]["verdict"] for e in result.payload] == ["keep", "keep"]
    assert all(e.extra["screening_verdict"]["deterministic"] for e in result.payload)


async def test_screening_agent_keeps_on_target_patent_without_llm(run_id, trace_id, screening_ctx):
    """A patent naming the target is IP on the target; only disease-only patents go to the LLM."""
    ctx, provider = screening_ctx
    on_target = make_evidence(
        run_id,
        trace_id,
        source="17602803",
        evidence_type=EvidenceType.PATENT,
        extra={"title": "INHIBITORS OF BRCA1", "abstract": "Compounds that inhibit BRCA1."},
    )
    disease_only = make_evidence(
        run_id,
        trace_id,
        source="17658494",
        evidence_type=EvidenceType.PATENT,
        extra={"title": "METHOD OF TREATING BREAST CANCER", "abstract": "A compound of Formula I."},
    )
    provider.complete = AsyncMock(
        return_value=_make_completion([{"id": "17658494", "verdict": "drop", "rationale": "x"}])
    )
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=[on_target, disease_only],
    )
    result = await ScreeningAgent().run(msg, ctx)

    provider.complete.assert_awaited_once()
    sent = provider.complete.await_args.args[0].messages[0]["content"]
    assert "17602803" not in sent
    by_source = {e.source: e.extra["screening_verdict"] for e in result.payload}
    assert by_source["17602803"]["verdict"] == "keep"
    assert by_source["17658494"]["verdict"] == "drop"


async def test_screening_agent_retries_items_the_llm_left_without_verdict(
    run_id, trace_id, screening_ctx
):
    """A partial JSON array used to strand the omitted items as 'uncertain' for good."""
    ctx, provider = screening_ctx
    evidences = [make_evidence(run_id, trace_id, source=f"PMID:{i}") for i in range(1, 4)]
    provider.complete = AsyncMock(
        side_effect=[
            # First call answers only one of three ids.
            _make_completion([{"id": "PMID:1", "verdict": "keep", "rationale": "Relevant"}]),
            # The retry covers the two it left out.
            _make_completion(
                [
                    {"id": "PMID:2", "verdict": "drop", "rationale": "Off-topic"},
                    {"id": "PMID:3", "verdict": "keep", "rationale": "Relevant"},
                ]
            ),
        ]
    )
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=evidences,
    )
    result = await ScreeningAgent().run(msg, ctx)

    assert provider.complete.await_count == 2
    retried = provider.complete.await_args_list[1].args[0].messages[0]["content"]
    assert "PMID:1" not in retried and "PMID:2" in retried and "PMID:3" in retried
    by_source = {e.source: e.extra["screening_verdict"]["verdict"] for e in result.payload}
    assert by_source == {"PMID:1": "keep", "PMID:2": "drop", "PMID:3": "keep"}


def test_parse_verdicts_keeps_the_complete_entries_of_a_truncated_reply(run_id, trace_id):
    """A reply cut off by its token cap used to lose the whole batch."""
    batch = [make_evidence(run_id, trace_id, source=f"PMID:{i}") for i in range(1, 4)]
    full = json.dumps(
        [
            {"id": "PMID:1", "verdict": "keep", "rationale": "Relevant"},
            {"id": "PMID:2", "verdict": "drop", "rationale": "Off-topic"},
            {"id": "PMID:3", "verdict": "keep", "rationale": "Relevant"},
        ]
    )
    cut = full[: full.index('"PMID:3"') + 12]
    result = _parse_verdicts(cut, batch)
    assert [r["verdict"] for r in result[:2]] == ["keep", "drop"]
    assert result[2]["rationale"] == _MISSING_RATIONALE


async def test_screening_agent_retries_only_the_items_cut_from_a_truncated_reply(
    run_id, trace_id, screening_ctx
):
    ctx, provider = screening_ctx
    evidences = [make_evidence(run_id, trace_id, source=f"PMID:{i}") for i in range(1, 4)]
    truncated = CompletionResult(
        content='[{"id": "PMID:1", "verdict": "keep", "rationale": "Relevant"}, '
        '{"id": "PMID:2", "verdict": "drop", "rationale": "Off"}, {"id": "PMID:3", "verd',
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1.0,
    )
    provider.complete = AsyncMock(
        side_effect=[
            truncated,
            _make_completion([{"id": "PMID:3", "verdict": "keep", "rationale": "Relevant"}]),
        ]
    )
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=evidences,
    )
    result = await ScreeningAgent().run(msg, ctx)

    assert provider.complete.await_count == 2
    retried = provider.complete.await_args_list[1].args[0].messages[0]["content"]
    assert "PMID:1" not in retried and "PMID:2" not in retried and "PMID:3" in retried
    by_source = {e.source: e.extra["screening_verdict"]["verdict"] for e in result.payload}
    assert by_source == {"PMID:1": "keep", "PMID:2": "drop", "PMID:3": "keep"}


@pytest.mark.parametrize("n", [1, 4, 25])
async def test_screening_reply_cap_is_80_tokens_per_item_plus_100(
    run_id, trace_id, screening_ctx, n
):
    ctx, provider = screening_ctx
    evidences = [make_evidence(run_id, trace_id, source=f"PMID:{i}") for i in range(n)]
    provider.complete = AsyncMock(
        return_value=_make_completion(
            [{"id": f"PMID:{i}", "verdict": "keep", "rationale": "ok"} for i in range(n)]
        )
    )
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=evidences,
    )
    await ScreeningAgent().run(msg, ctx)
    assert provider.complete.await_args.args[0].max_tokens == 80 * n + 100


@pytest.mark.parametrize("num_ctx", [8192, 16384])
async def test_screening_batches_never_exceed_the_context_window(
    run_id, trace_id, screening_ctx, num_ctx
):
    """Prompt (system + documents) plus the reply cap stays inside the window, counting
    tokens at 3 characters each, for evidence of very different lengths."""
    ctx, provider = screening_ctx
    provider.context_window = MagicMock(return_value=num_ctx)
    evidences = [
        make_evidence(
            run_id,
            trace_id,
            source=f"PMID:{i}",
            extra={"abstract": "word " * (200 + 900 * (i % 4))},
        )
        for i in range(60)
    ]

    async def _answer(req):
        ids = re.findall(r'<document id="([^"]+)"', req.messages[0]["content"])
        return _make_completion([{"id": i, "verdict": "keep", "rationale": "ok"} for i in ids])

    provider.complete = AsyncMock(side_effect=_answer)
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=evidences,
    )
    await ScreeningAgent().run(msg, ctx)
    for call in provider.complete.await_args_list:
        req = call.args[0]
        prompt_chars = len(req.system) + len(req.messages[0]["content"])
        assert math.ceil(prompt_chars / 3.0) + req.max_tokens <= num_ctx


async def test_screening_agent_keeps_uncertain_when_retry_also_fails(
    run_id, trace_id, screening_ctx
):
    ctx, provider = screening_ctx
    evidences = [make_evidence(run_id, trace_id, source=f"PMID:{i}") for i in range(1, 3)]
    bad = CompletionResult(
        content="not json", model_used="m", input_tokens=1, output_tokens=1, latency_ms=1.0
    )
    provider.complete = AsyncMock(return_value=bad)
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=evidences,
    )
    result = await ScreeningAgent().run(msg, ctx)

    assert provider.complete.await_count == 2  # one batch + one retry, no storm
    assert all(e.extra["screening_verdict"]["verdict"] == "uncertain" for e in result.payload)


async def test_screening_agent_marks_only_rule_verdicts_deterministic(
    run_id, trace_id, screening_ctx
):
    ctx, provider = screening_ctx
    article = make_evidence(run_id, trace_id, source="PMID:1")
    empty = make_evidence(run_id, trace_id, source="PMID:2", extra={"abstract": ""})
    provider.complete = AsyncMock(
        return_value=_make_completion([{"id": "PMID:1", "verdict": "keep", "rationale": "ok"}])
    )
    msg = make_task_msg(
        "screening",
        {"target_gene": "BRCA1", "disease": "breast cancer", "pass_type": "first"},
        run_id,
        trace_id,
        payload=[article, empty],
    )
    result = await ScreeningAgent().run(msg, ctx)
    flags = {e.source: e.extra["screening_verdict"].get("deterministic") for e in result.payload}
    assert flags == {"PMID:1": None, "PMID:2": True}


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
