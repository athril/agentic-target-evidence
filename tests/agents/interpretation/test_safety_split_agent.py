# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The safety lens run as typed questions (SPLIT_LENSES=safety).

A fake provider routes on the system prompt: each question's skill, the narrative skill and
the single-call safety skill open with a distinct heading, so the test controls what every
call returns and can tell which path produced the verdict.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.interpretation._lens_base import lens_input_digest, split_enabled
from agents.interpretation.safety_lens.agent import SafetyLensAgent
from agents.interpretation.safety_lens.split import split_digest
from capabilities.target_validation.workflow import _lens_cache_key
from core.routing.providers.base import CompletionResult
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, Direction, EvidenceType, Provenance
from schemas.verdicts import LensVerdict
from tests.agents.conftest import make_task_msg

_LIABILITIES_HEAD = "# Safety question: have human data settled"
_HARM_HEAD = "# Safety question: is harm reported in people"
_TISSUE_HEAD = "# Safety question: is the target expressed"
_NARRATIVE_HEAD = "# Safety narrative"
_LEGACY_HEAD = "# Safety Lens Skill"


def _completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content, model_used="m", input_tokens=1, output_tokens=1, latency_ms=1.0
    )


def _claim(
    run_id: uuid.UUID, text: str, etype: EvidenceType = EvidenceType.REGULATORY
) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="GENE-A",
        disease="disease one",
        evidence_type=etype,
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="t", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        claim_text=text,
        direction=Direction.UNSPECIFIED,
        confidence=0.9,
    )


class _Router:
    """Fake model: answers by the heading of the system prompt, and records the calls."""

    def __init__(self, answers: dict[str, list[str]]) -> None:
        self.answers = answers
        self.calls: list[str] = []
        self.requests: list[Any] = []

    async def complete(self, req: Any) -> CompletionResult:
        system = req.system or ""
        self.requests.append(req)
        for head in (
            _LIABILITIES_HEAD,
            _HARM_HEAD,
            _TISSUE_HEAD,
            _NARRATIVE_HEAD,
            _LEGACY_HEAD,
        ):
            if system.startswith(head):
                self.calls.append(head)
                queue = self.answers.get(head) or ["{}"]
                return _completion(queue.pop(0) if len(queue) > 1 else queue[0])
        return _completion("{}")  # citation / repair steps


def _ctx(run_id, trace_id, answers):
    fake = _Router(answers)
    provider = MagicMock()
    provider.complete = fake.complete
    provider.context_window = None
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    return RunContext(run_id=run_id, trace_id=trace_id, router=router), fake


def _spec(claims: list[CoreClaim], **extra: Any) -> dict[str, Any]:
    return {
        "target_gene": "GENE-A",
        "disease": "disease one",
        "direction": "inhibit",
        "gene_id": "",
        "disease_id": "",
        "extracted_claims": [c.model_dump(mode="json") for c in claims],
        "candidate_liabilities": [
            {"system": "organ system a", "severity": "medium", "source": "src", "evidence": ["e1"]},
            {"system": "organ system b", "severity": "medium", "source": "src", "evidence": ["e2"]},
        ],
        "human_exposure": True,
        "drug_precedent": {"status": "validated", "concordant_approved": ["DRUG-A"]},
        "disease_tissue": "Tissue_A",
        "disease_relevant_tissues": ["Tissue_A"],
        "top_tpm_tissues": ["Tissue_B", "Tissue_C"],
        "gtex_tissue_ranks": {"Tissue_A": 2, "Tissue_B": 1, "Tissue_C": 3},
        "bulk_tpm": 40.0,
        **extra,
    }


def _answers(claim_id: str, harm: dict | None = None) -> dict[str, list[str]]:
    return {
        _LIABILITIES_HEAD: [
            json.dumps(
                {
                    "liabilities": [
                        {"index": 1, "status": "resolved_by_human_data", "claim_ids": [claim_id]},
                        {"index": 2, "status": "unresolved"},
                    ]
                }
            )
        ],
        _HARM_HEAD: [json.dumps(harm or {"harm_reported": False, "events": []})],
        _TISSUE_HEAD: [
            json.dumps(
                {
                    "disease_cell_type_expression": "reported",
                    "claim_ids": [claim_id],
                    "off_target_tissues_of_concern": ["Tissue_B", "invented"],
                }
            )
        ],
        _NARRATIVE_HEAD: [
            json.dumps({"rationale": "The profile is acceptable.", "narrative": "Paragraph one."})
        ],
    }


@pytest.fixture()
def split_on(monkeypatch):
    monkeypatch.setenv("SPLIT_LENSES", "safety")


async def _run(ctx, run_id, trace_id, spec):
    msg = make_task_msg("safety_lens", spec, run_id, trace_id)
    result = await SafetyLensAgent().run(msg, ctx)
    return LensVerdict.model_validate(result.payload["lens_verdicts"][0])


async def test_the_verdict_comes_from_the_aggregation_not_the_model(split_on, run_id, trace_id):
    claim = _claim(run_id, "A labelled warning was assessed in treated patients.")
    ctx, fake = _ctx(run_id, trace_id, _answers(str(claim.evidence_id)))

    verdict = await _run(ctx, run_id, trace_id, _spec([claim]))

    assert verdict.overall_verdict == "support"
    assert {a.axis: a.verdict for a in verdict.axes} == {
        "toxicity": True,
        "tissue_specificity": True,
    }
    assert verdict.rationale == "The profile is acceptable."
    assert _LEGACY_HEAD not in fake.calls
    assert fake.calls.count(_NARRATIVE_HEAD) == 1


async def test_the_decision_trace_records_each_question(split_on, run_id, trace_id):
    claim = _claim(run_id, "A labelled warning was assessed in treated patients.")
    cid = str(claim.evidence_id)
    ctx, _ = _ctx(run_id, trace_id, _answers(cid))

    verdict = await _run(ctx, run_id, trace_id, _spec([claim]))

    trace = {r["question_id"]: r for r in verdict.question_results}
    assert set(trace) == {"liability_resolution", "human_harm", "tissue"}
    assert "1. organ system a" in trace["liability_resolution"]["facts"]
    assert trace["liability_resolution"]["answer"]["liabilities"][0]["claim_ids"] == [cid]
    assert trace["liability_resolution"]["shown_claim_ids"] == [cid]
    # the tissue not named in the facts was removed and said so
    assert trace["tissue"]["answer"]["off_target_tissues_of_concern"] == ["Tissue_B"]
    assert trace["tissue"]["notes"]


async def test_a_citation_the_model_was_not_shown_is_dropped_and_not_scored(
    split_on, run_id, trace_id
):
    claim = _claim(run_id, "A labelled warning was assessed in treated patients.")
    invented = str(uuid.uuid4())
    answers = _answers(str(claim.evidence_id))
    answers[_LIABILITIES_HEAD] = [
        json.dumps(
            {
                "liabilities": [
                    {"index": 1, "status": "resolved_by_human_data", "claim_ids": [invented]},
                    {"index": 2, "status": "unresolved"},
                ]
            }
        )
    ]
    ctx, _ = _ctx(run_id, trace_id, answers)

    verdict = await _run(ctx, run_id, trace_id, _spec([claim]))

    trace = {r["question_id"]: r for r in verdict.question_results}
    assert trace["liability_resolution"]["dropped_claim_ids"] == [invented]
    # no valid citation: the liability stays unresolved
    assert trace["liability_resolution"]["answer"]["liabilities"][0]["status"] == "unresolved"
    assert all(invented not in a.supporting_claim_ids for a in verdict.axes)


async def test_harm_in_people_opposes_unless_the_class_is_approved(split_on, run_id, trace_id):
    claim = _claim(run_id, "Cognitive worsening was reported in treated patients.")
    cid = str(claim.evidence_id)
    harm = {
        "harm_reported": True,
        "events": [
            {
                "description": "cognitive worsening",
                "claim_ids": [cid],
                "in_requested_direction": True,
            }
        ],
    }
    ctx, _ = _ctx(run_id, trace_id, _answers(cid, harm))
    spec = _spec([claim], drug_precedent={"status": "failed"}, human_exposure=False)

    verdict = await _run(ctx, run_id, trace_id, spec)

    assert verdict.overall_verdict == "oppose"
    assert verdict.axes[0].verdict is False

    # the same harm next to an approved same-direction drug is a labelled, managed risk
    ctx, _ = _ctx(run_id, trace_id, _answers(cid, harm))
    approved = await _run(ctx, run_id, trace_id, _spec([claim]))
    assert approved.axes[0].verdict is True


async def test_the_guards_still_run_on_the_split_verdict(split_on, run_id, trace_id):
    claim = _claim(run_id, "A labelled warning was assessed in treated patients.")
    ctx, _ = _ctx(run_id, trace_id, _answers(str(claim.evidence_id)))

    verdict = await _run(ctx, run_id, trace_id, _spec([claim]))

    families = {c.family for c in verdict.guard_checks}
    assert {"constraint", "tissue_relevance", "quantity"} <= families


async def test_the_guards_annotate_prose_that_inverts_the_facts(split_on, run_id, trace_id):
    """The narrative call may not mis-band the pre-computed constraint reading: the same
    guard that checks the single-call lens flags it."""
    from services.evidence.constraint_interpret import interpret_constraint

    claim = _claim(run_id, "A labelled warning was assessed in treated patients.")
    answers = _answers(str(claim.evidence_id))
    answers[_NARRATIVE_HEAD] = [
        json.dumps(
            {
                "rationale": "The profile is acceptable.",
                "narrative": "GENE-A is haploinsufficient, so inhibition is risky.",
            }
        )
    ]
    reading = interpret_constraint("GENE-A", loeuf=0.759, pli=0.0, mis_z=1.70).model_dump()
    ctx, _ = _ctx(run_id, trace_id, answers)

    verdict = await _run(ctx, run_id, trace_id, _spec([claim], constraint_reading=reading))

    assert "CONSTRAINT GUARD" in verdict.narrative
    assert any(f.rule_id == "constraint_interpretation_guard" for f in verdict.validation_flags)
    assert next(c for c in verdict.guard_checks if c.family == "constraint").status == "flagged"


async def test_an_unanswerable_question_falls_back_to_the_single_call_lens(
    split_on, run_id, trace_id
):
    claim = _claim(run_id, "A labelled warning was assessed in treated patients.")
    answers = _answers(str(claim.evidence_id))
    answers[_HARM_HEAD] = ["not json", "still not json"]
    answers[_LEGACY_HEAD] = [
        json.dumps(
            {
                "overall_verdict": "neutral",
                "confidence": 0.4,
                "rationale": "Legacy rationale.",
                "axes": [{"axis": "toxicity", "verdict": None, "confidence": 0.4}],
            }
        )
    ]
    ctx, fake = _ctx(run_id, trace_id, answers)

    verdict = await _run(ctx, run_id, trace_id, _spec([claim]))

    assert _LEGACY_HEAD in fake.calls
    assert verdict.rationale.startswith("Legacy rationale")
    assert verdict.question_results == []
    flag = next(f for f in verdict.validation_flags if f.rule_id == "split_lens.fallback")
    assert flag.severity == "medium" and "human_harm" in flag.message


async def test_an_unusable_narrative_does_not_lose_the_verdict(split_on, run_id, trace_id):
    claim = _claim(run_id, "A labelled warning was assessed in treated patients.")
    answers = _answers(str(claim.evidence_id))
    answers[_NARRATIVE_HEAD] = ["prose, not json", "more prose"]
    ctx, fake = _ctx(run_id, trace_id, answers)

    verdict = await _run(ctx, run_id, trace_id, _spec([claim]))

    assert verdict.overall_verdict == "support"
    assert verdict.rationale and "organ system a" in verdict.narrative
    assert fake.calls.count(_NARRATIVE_HEAD) == 2


async def test_without_the_flag_the_single_call_path_runs(monkeypatch, run_id, trace_id):
    monkeypatch.delenv("SPLIT_LENSES", raising=False)
    claim = _claim(run_id, "A labelled warning was assessed in treated patients.")
    answers = {_LEGACY_HEAD: [json.dumps({"overall_verdict": "neutral", "axes": []})]}
    ctx, fake = _ctx(run_id, trace_id, answers)

    verdict = await _run(ctx, run_id, trace_id, _spec([claim]))

    assert fake.calls == [_LEGACY_HEAD]
    assert verdict.question_results == []


def test_the_flag_is_read_in_one_place(monkeypatch):
    monkeypatch.setenv("SPLIT_LENSES", " Safety , genetics ")
    assert split_enabled("safety") and split_enabled("genetics")
    assert not split_enabled("clinical")
    monkeypatch.delenv("SPLIT_LENSES")
    assert not split_enabled("safety")


def test_split_and_single_call_verdicts_never_share_a_cache_entry():
    spec = {"target_gene": "GENE-A", "extracted_claims": [], "source_quality": {}}
    plain = _lens_cache_key("GENE-A", "disease one", "inhibit", "safety", spec)
    split = _lens_cache_key(
        "GENE-A", "disease one", "inhibit", "safety", spec, extra=split_digest()
    )
    assert plain != split


def test_the_cache_key_changes_when_a_question_skill_changes(monkeypatch):
    import agents.interpretation._lens_questions as module

    before = split_digest()
    real = module.load_skill
    monkeypatch.setattr(
        module, "load_skill", lambda name: "edited" if name == "safety_q_tissue" else real(name)
    )
    assert split_digest() != before


def test_the_digest_covers_the_narrative_skill(monkeypatch):
    import agents.interpretation._lens_questions as module

    before = split_digest()
    real = module.load_skill
    monkeypatch.setattr(
        module, "load_skill", lambda name: "edited" if name == "safety_narrative" else real(name)
    )
    assert split_digest() != before


def test_lens_input_digest_includes_the_extra_text():
    spec = {"extracted_claims": [], "source_quality": {}}
    assert lens_input_digest("safety", spec, "skill") != lens_input_digest(
        "safety", spec, "skill", "extra"
    )
    assert lens_input_digest("safety", spec, "skill") == lens_input_digest(
        "safety", spec, "skill", ""
    )


# ------------------------------------------------------------------ direction harms


@pytest.mark.parametrize(
    ("reading", "expected"),
    [
        ({"verdict": "contradicts", "against": 0.87}, True),
        ({"verdict": "contradicts", "against": 0.4}, False),
        ({"verdict": "conflicting", "against": 0.9}, False),
        ({"verdict": "supports", "against": 0.0}, False),
    ],
)
def test_a_direction_harm_needs_a_clear_contradiction_not_just_a_liability(reading, expected):
    """One label warning creates the 'worsens the disease' liability even when approved
    drugs of the same direction outweigh it; the reading's verdict decides."""
    from agents.interpretation.safety_lens.split import _direction_harms
    from services.evidence.liabilities import DIRECTION_HARM_SYSTEM

    liabilities = [{"system": DIRECTION_HARM_SYSTEM, "severity": "high"}]
    assert _direction_harms({"direction_reading": reading}, liabilities) is expected


def test_without_a_reading_the_liability_decides():
    from agents.interpretation.safety_lens.split import _direction_harms
    from services.evidence.liabilities import DIRECTION_HARM_SYSTEM

    assert _direction_harms({}, [{"system": DIRECTION_HARM_SYSTEM}]) is True
    assert _direction_harms({}, [{"system": "organ system a"}]) is False


async def test_an_approved_class_is_not_opposed_for_a_confirmed_labelled_risk(
    split_on, run_id, trace_id
):
    claim = _claim(run_id, "A labelled skin reaction occurs in treated patients.")
    cid = str(claim.evidence_id)
    answers = _answers(cid)
    answers[_LIABILITIES_HEAD] = [
        json.dumps(
            {
                "liabilities": [
                    {"index": 1, "status": "confirmed_in_humans", "claim_ids": [cid]},
                    {"index": 2, "status": "unresolved"},
                ]
            }
        )
    ]
    spec = _spec([claim])
    spec["candidate_liabilities"][0]["severity"] = "high"
    ctx, _ = _ctx(run_id, trace_id, answers)

    approved = await _run(ctx, run_id, trace_id, spec)
    spec["drug_precedent"] = {"status": "in_clinic"}
    ctx, _ = _ctx(run_id, trace_id, _answers_like(answers))
    unapproved = await _run(ctx, run_id, trace_id, spec)

    assert approved.overall_verdict == "support"
    assert unapproved.overall_verdict == "oppose"


def _answers_like(answers: dict[str, list[str]]) -> dict[str, list[str]]:
    return {k: list(v) for k, v in answers.items()}


async def test_the_narrative_is_written_after_the_verdict_rules(split_on, run_id, trace_id):
    """A common-essential cancer target the questions call acceptable is set to neutral by
    rule; the narrative call must be told the verdict as published, not "support"."""
    claim = _claim(run_id, "A labelled warning was assessed in treated patients.")
    ctx, fake = _ctx(run_id, trace_id, _answers(str(claim.evidence_id)))
    spec = _spec(
        [claim],
        drug_precedent={"status": "stalled"},
        dependency_profile={"kind": "common_essential", "text": "Common essential."},
        disease_classes=["oncology"],
    )
    verdict = await _run(ctx, run_id, trace_id, spec)
    assert verdict.overall_verdict == "neutral"
    narrative_req = next(r for r in fake.requests if (r.system or "").startswith(_NARRATIVE_HEAD))
    prompt = narrative_req.messages[0]["content"]
    assert "Computed overall verdict (fixed): neutral" in prompt
    flags = [f.rule_id for f in verdict.validation_flags]
    assert flags.count("dependency.common_essential") == 1


async def test_an_approved_class_settles_its_liabilities_from_its_labels(
    split_on, run_id, trace_id
):
    """One high-severity knockout liability left an approved class neutral; its label
    shows what use in these patients found."""
    label_id = uuid.uuid4()
    claim = _claim(run_id, "DRUG-A label carries no boxed warning").model_copy(
        update={"source_evidence_id": label_id}
    )
    answers = _answers(str(claim.evidence_id))
    answers[_LIABILITIES_HEAD] = [
        json.dumps({"liabilities": [{"index": 1, "status": "unresolved"}]})
    ]
    spec = _spec(
        [claim],
        candidate_liabilities=[
            {"system": "heart", "severity": "high", "source": "mouse knockout", "evidence": ["e"]}
        ],
        approved_label_warnings=[
            {
                "drug": "DRUG-A",
                "text": "5.1 Infections: serious infections.",
                "evidence_id": str(label_id),
            }
        ],
    )
    ctx, _ = _ctx(run_id, trace_id, answers)
    verdict = await _run(ctx, run_id, trace_id, spec)
    toxicity = next(a for a in verdict.axes if a.axis == "toxicity")
    assert toxicity.verdict is True and verdict.overall_verdict == "support"
    trace = next(r for r in verdict.question_results if r.get("answer", {}).get("liabilities"))
    assert trace["answer"]["liabilities"][0]["status"] == "resolved_by_human_data"


def test_the_lens_rules_version_is_part_of_every_lens_digest(monkeypatch):
    """Prompt-building code and post-LLM rules are not in the skill or the spec, so a change
    to them alone must still miss the cache."""
    import agents.interpretation._lens_base as base

    spec = _spec([])
    before = lens_input_digest("safety", spec, "skill")
    monkeypatch.setattr(base, "LENS_RULES_VERSION", "test-bump")
    assert lens_input_digest("safety", spec, "skill") != before


def test_a_narrative_cut_at_its_cap_keeps_its_rationale_and_whole_sentences():
    from agents.interpretation.safety_lens.split import _parse_prose

    cut = '{"rationale": "No liability is open.", "narrative": "Para one ends. Para two is cu'
    assert _parse_prose(cut) == ("No liability is open.", "Para one ends.")
    # A complete reply without a narrative is still refused (and re-asked).
    assert _parse_prose('{"rationale": "r"}') is None
