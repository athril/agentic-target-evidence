# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for _lens_repair.py — one bounded, re-checked rewrite of flagged prose."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.interpretation._lens_repair import repair_enabled, repair_prose
from core.routing.providers.base import CompletionResult
from harness.context import RunContext
from schemas.evidence import DataClass
from schemas.verdicts import AxisVerdict, LensVerdict
from services.evidence.guard_core import Violation

_RUN = uuid.uuid4()
_BAD = "There are strong data indicating dependency across cancer cell lines."
_GOOD = "TRPC6 is not a cell-line dependency (2/1178 lines, 0.2%)."


def _find(text: str) -> list[Violation]:
    """Stand-in for the lens checks: flags the false dependency sentence."""
    if "dependency across cancer cell lines" in text:
        return [
            Violation(
                rule_id="quantity.cell_line_dependency",
                kind="overstatement",
                excerpt="dependency across cancer cell lines",
                note="[⚠ QUANTITY GUARD: the data show 2/1178 lines (0.2%).]",
            )
        ]
    if "everywhere" in text:  # a different, new problem a bad rewrite could introduce
        return [
            Violation(rule_id="other.rule", kind="contradiction", excerpt="everywhere", note="x")
        ]
    return []


def _verdict(rationale: str, narrative: str = "Para one.\n\nPara two.") -> LensVerdict:
    return LensVerdict(
        run_id=_RUN,
        trace_id="t",
        lens="biology",
        target_gene="TRPC6",
        disease="FSGS",
        overall_verdict="support",
        confidence=0.9,
        rationale=rationale,
        narrative=narrative,
        axes=[AxisVerdict(axis="druggability", verdict=True, rationale="Tractable.")],
    )


def _ctx(reply: str):
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=CompletionResult(
            content=reply, model_used="m", input_tokens=1, output_tokens=1, latency_ms=1.0
        )
    )
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    return RunContext(run_id=_RUN, trace_id="t", router=router), provider


async def _repair(verdict: LensVerdict, reply: str):
    ctx, provider = _ctx(reply)
    out = await repair_prose(
        verdict,
        find=_find,
        pool=[],
        context="",
        ctx=ctx,
        classification=DataClass.NON_SENSITIVE,
        task="biology_lens",
    )
    return out, provider


async def test_flagged_sentence_is_rewritten_and_original_recorded():
    rationale = f"TRPC6 is a strong target. {_BAD} Expression fits."
    out, provider = await _repair(_verdict(rationale), json.dumps({"S1": _GOOD}))

    assert out.rationale == f"TRPC6 is a strong target. {_GOOD} Expression fits."
    flag = next(f for f in out.validation_flags if f.rule_id == "prose_repaired")
    assert flag.severity == "low"
    assert flag.claim_excerpt == _BAD
    assert _GOOD in flag.message
    sent = provider.complete.await_args.args[0].messages[0]["content"]
    assert _BAD in sent and "2/1178" in sent and "TRPC6 is a strong target" not in sent


async def test_nothing_flagged_means_no_model_call():
    out, provider = await _repair(_verdict("All good."), "{}")
    provider.complete.assert_not_awaited()
    assert out == _verdict("All good.")


async def test_rewrite_that_still_fails_the_check_is_rejected():
    out, _ = await _repair(_verdict(_BAD), json.dumps({"S1": f"Still: {_BAD}"}))
    assert out.rationale == _BAD
    assert not any(f.rule_id == "prose_repaired" for f in out.validation_flags)


async def test_rewrite_that_introduces_a_new_problem_is_rejected():
    out, _ = await _repair(_verdict(_BAD), json.dumps({"S1": "Dependency is seen everywhere."}))
    assert out.rationale == _BAD


async def test_rewrite_that_brings_in_a_missing_value_placeholder_is_rejected():
    """Copied from an empty data field, "n/a" replaced the figures it was asked to fix."""
    reply = json.dumps({"S1": "Cancer cell lines show dependency in n/a of 1178 lines."})
    out, _ = await _repair(_verdict(_BAD), reply)
    assert out.rationale == _BAD
    assert not any(f.rule_id == "prose_repaired" for f in out.validation_flags)


async def test_unparseable_reply_leaves_prose_unchanged():
    out, _ = await _repair(_verdict(_BAD), "I fixed it.")
    assert out.rationale == _BAD


async def test_paragraph_breaks_and_other_sentences_are_preserved():
    narrative = f"First paragraph.\n\n{_BAD}\n\nThird paragraph."
    out, _ = await _repair(_verdict("Fine.", narrative), json.dumps({"S1": _GOOD}))
    assert out.narrative == f"First paragraph.\n\n{_GOOD}\n\nThird paragraph."


async def test_terminology_only_findings_are_not_repaired():
    def find(text: str) -> list[Violation]:
        return [Violation(rule_id="t", kind="terminology", excerpt="Fine", note="n")]

    ctx, provider = _ctx("{}")
    await repair_prose(
        _verdict("Fine."),
        find=find,
        pool=[],
        context="",
        ctx=ctx,
        classification=DataClass.NON_SENSITIVE,
        task="biology_lens",
    )
    provider.complete.assert_not_awaited()


@pytest.mark.parametrize(
    ("value", "enabled"), [(None, True), ("1", True), ("0", False), ("false", False)]
)
def test_switch(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv("LENS_PROSE_REPAIR", raising=False)
    else:
        monkeypatch.setenv("LENS_PROSE_REPAIR", value)
    assert repair_enabled() is enabled


async def test_off_topic_rewrite_is_rejected():
    """TRPC6 regulatory lens: a flagged boxed-warning sentence came back as an
    unrelated indication sentence and replaced the axis rationale."""

    def find(text: str) -> list[Violation]:
        if "relevant to the target biology" in text:
            return [
                Violation(
                    rule_id="fact.off_target_label_safety",
                    kind="contradiction",
                    excerpt="relevant to the target biology",
                    note="[⚠ FACT CHECK: a drug effect, not a target effect.]",
                )
            ]
        return []

    bad = "SPARSENTAN carries a black-box warning for hepatotoxicity, which is relevant to the target biology."
    off_topic = "SPARSENTAN is FDA-approved to reduce proteinuria in adults with FSGS."
    ctx, _ = _ctx(json.dumps({"S1": off_topic}))
    out = await repair_prose(
        _verdict(bad),
        find=find,
        pool=[],
        context="",
        ctx=ctx,
        classification=DataClass.NON_SENSITIVE,
        task="regulatory_lens",
    )
    assert out.rationale == bad
    assert not out.validation_flags


def test_parse_merges_one_object_per_line():
    """Small models answer {"S1": …}\\n{"S2": …} instead of one object; both rewrites count."""
    from agents.interpretation._lens_repair import _parse

    raw = '{"S1": "First rewrite."}\n{"S2": "Second rewrite."}'
    assert _parse(raw) == {"S1": "First rewrite.", "S2": "Second rewrite."}


async def test_a_rewrite_that_adds_a_fact_no_check_can_see_is_rejected():
    """Rewrites that passed every check still invented a boxed warning for a named drug
    and swapped in another lineage's figures from a data line."""
    invented_name = json.dumps({"S1": "OTHERDRUG has dependency in some cell lines."})
    invented_number = json.dumps({"S1": "Cancer cell lines show dependency in 13/1178 lines."})

    for reply in (invented_name, invented_number):
        out, _ = await _repair(_verdict(_BAD), reply)
        assert out.rationale == _BAD
        assert not any(f.rule_id == "prose_repaired" for f in out.validation_flags)


async def test_a_long_correction_is_recorded_whole_not_cut_mid_word():
    """CFTR clinical: the correction flag cut the rewrite at 300 characters, so the report
    showed "… Ivacaftor 150 mg q" as the published sentence."""
    long_good = _GOOD[:-1] + ", " + ", ".join(["cancer cell lines are not dependent"] * 7) + "."
    assert 300 < len(long_good) <= 2 * len(_BAD) + 200
    out, _ = await _repair(_verdict(f"Intro. {_BAD}"), json.dumps({"S1": long_good}))
    flag = next(f for f in out.validation_flags if f.rule_id == "prose_repaired")
    assert flag.message.endswith(long_good)
