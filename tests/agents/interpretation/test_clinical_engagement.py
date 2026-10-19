# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Target engagement of drugs that failed for efficacy
(agents/interpretation/clinical_lens/engagement.py and the workflow helper that uses it).

A fake provider answers by the question's skill heading. Drug names are placeholders.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.interpretation.clinical_lens.engagement import (
    ask_engagement,
    engagement_digest,
    inputs_digest,
    normalise_engagement,
    programme_claims,
    programme_facts,
    programmes_to_ask,
)
from capabilities.target_validation import workflow
from core.routing.providers.base import CompletionResult
from harness.context import RunContext
from schemas.evidence import (
    CoreClaim,
    DataClass,
    Direction,
    Evidence,
    EvidenceType,
    Provenance,
)
from services.evidence.programme_outcomes import (
    Programme,
    ProgrammeReading,
    apply_engagement,
    load_programme_config,
)

_HEAD = "# Clinical question: did the drug engage its target?"


def _completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content, model_used="m", input_tokens=1, output_tokens=1, latency_ms=1.0
    )


def _claim(run_id: uuid.UUID, text: str, etype: EvidenceType = EvidenceType.ABSTRACT) -> CoreClaim:
    from schemas.evidence import LensTopic

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
        topics=[LensTopic.CLINICAL],
    )


def _programme(
    drug: str = "DRUG-A", outcome: str = "failed_efficacy", failure_stage: float = 3.0
) -> Programme:
    return Programme(
        drug=drug,
        effect="decrease",
        stage=failure_stage,
        outcome=outcome,  # type: ignore[arg-type]
        trials=["nct1"],
        reasons=["futility"],
        failure_stage=failure_stage,
    )


@pytest.fixture(autouse=True)
def _no_publication_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The publication question reads the literature index (PubMed lookups); these tests
    are about engagement."""

    async def passthrough(_state, reading, _cfg, _ctx):
        return reading

    monkeypatch.setattr(workflow, "_publication_outcomes", passthrough)


@pytest.fixture()
def harness(run_id, trace_id):
    provider = MagicMock()
    provider.context_window = None
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    return RunContext(run_id=run_id, trace_id=trace_id, router=router), provider


# --------------------------------------------------------------------- selection


def test_only_efficacy_failures_at_phase_2_or_later_are_asked():
    reading = ProgrammeReading(
        requested="decrease",
        programmes=[
            _programme("A", "failed_efficacy", 3.0),
            _programme("B", "failed_efficacy", 2.0),
            _programme("C", "failed_efficacy", 1.0),
            _programme("D", "failed_safety", 3.0),
            _programme("E", "stalled_unexplained", 3.0),
            _programme("F", "ongoing", 0.0),
        ],
    )
    assert [p.drug for p in programmes_to_ask(reading)] == ["A", "B"]


def test_claims_are_those_that_name_the_drug_in_clinical_or_biology_text(run_id):
    named = _claim(run_id, "DRUG-A lowered the circulating marker in treated patients.")
    other = _claim(run_id, "DRUG-B lowered the circulating marker.")
    partial = _claim(run_id, "DRUG-AB is an unrelated compound.")
    assert programme_claims(_programme(), [named, other, partial]) == [named]


def test_the_facts_list_the_trials_with_posted_outcome_measures():
    facts = programme_facts(
        _programme(),
        [
            {
                "nct_id": "NCT1",
                "phase": "PHASE3",
                "status": "TERMINATED",
                "title": "A study of DRUG-A",
                "arm_results": [{"measure": "Change in score", "arm": "a", "p_value": 0.4}],
            }
        ],
        "disease one",
    )
    assert (
        "Drug: drug-a — best stage in disease one: phase 3; it failed for lack of efficacy."
        in facts
    )
    assert (
        "Trial nct1: PHASE3, terminated — A study of DRUG-A; primary outcome measures: Change in score"
        in facts
    )


def test_the_facts_survive_a_trial_the_registry_facts_lack():
    facts = programme_facts(_programme(), [], "disease one")
    assert "Trials in this indication: nct1" in facts


# ----------------------------------------------------------------------- the question


async def test_a_cited_answer_is_returned(harness, run_id):
    ctx, provider = harness
    claim = _claim(run_id, "DRUG-A lowered the circulating marker in treated patients.")
    cid = str(claim.evidence_id)
    sent: list[Any] = []

    async def complete(req):
        sent.append(req)
        return _completion(json.dumps({"engagement": "shown", "claim_ids": [cid]}))

    provider.complete = complete

    answer = await ask_engagement(
        _programme(),
        facts="Drug: drug-a",
        claims=[claim],
        quality_map={},
        disease_classes=frozenset(),
        ctx=ctx,
        classification=DataClass.NON_SENSITIVE,
    )

    assert answer == ("shown", [cid])
    assert sent[0].system.startswith(_HEAD)
    assert sent[0].task == "clinical_lens"


async def test_a_claim_the_model_was_not_shown_cannot_establish_engagement(harness, run_id):
    ctx, provider = harness
    claim = _claim(run_id, "DRUG-A lowered the circulating marker.")
    provider.complete = AsyncMock(
        return_value=_completion(
            json.dumps({"engagement": "shown", "claim_ids": [str(uuid.uuid4())]})
        )
    )

    answer = await ask_engagement(
        _programme(),
        facts="f",
        claims=[claim],
        quality_map={},
        disease_classes=frozenset(),
        ctx=ctx,
        classification=DataClass.NON_SENSITIVE,
    )

    assert answer == ("not_reported", [])


async def test_no_claim_naming_the_drug_means_not_reported_without_a_call(harness):
    ctx, provider = harness
    provider.complete = AsyncMock()

    answer = await ask_engagement(
        _programme(),
        facts="f",
        claims=[],
        quality_map={},
        disease_classes=frozenset(),
        ctx=ctx,
        classification=DataClass.NON_SENSITIVE,
    )

    assert answer == ("not_reported", [])
    provider.complete.assert_not_awaited()


async def test_an_unparseable_answer_is_not_reported(harness, run_id):
    ctx, provider = harness
    provider.complete = AsyncMock(return_value=_completion("no idea"))
    answer = await ask_engagement(
        _programme(),
        facts="f",
        claims=[_claim(run_id, "DRUG-A text")],
        quality_map={},
        disease_classes=frozenset(),
        ctx=ctx,
        classification=DataClass.NON_SENSITIVE,
    )
    assert answer == ("not_reported", [])


@pytest.mark.parametrize("status", ["shown", "not_shown"])
def test_a_status_without_a_citation_is_not_reported(status):
    assert normalise_engagement({"engagement": status, "claim_ids": []}) == ("not_reported", [])


def test_the_digest_changes_with_the_skill_and_the_inputs(monkeypatch, run_id):
    import agents.interpretation._lens_questions as module

    before = engagement_digest()
    monkeypatch.setattr(module, "load_skill", lambda name: "edited")
    assert engagement_digest() != before

    a, b = _claim(run_id, "one"), _claim(run_id, "two")
    assert inputs_digest("f", [a]) != inputs_digest("f", [a, b])
    assert inputs_digest("f", [a, b]) == inputs_digest("f", [b, a])


# --------------------------------------------------------------- the strength it moves


def test_engagement_scales_the_strength_it_applies_to():
    cfg = load_programme_config()
    reading = ProgrammeReading(
        requested="decrease",
        programmes=[_programme("A"), _programme("B"), _programme("C", "failed_safety")],
    )
    base = apply_engagement(reading, {}, cfg, "disease one")
    shown = apply_engagement(
        reading, {"A": ("shown", ["c1"]), "B": ("shown", ["c2"])}, cfg, "disease one"
    )
    not_shown = apply_engagement(
        reading, {"A": ("not_shown", ["c1"]), "B": ("not_shown", ["c2"])}, cfg, "disease one"
    )
    assert not_shown.falsification < base.falsification < shown.falsification
    assert [p.engagement for p in shown.programmes] == ["shown", "shown", "not_assessed"]
    assert shown.programmes[0].engagement_claim_ids == ["c1"]


def test_engagement_never_changes_a_safety_failure():
    cfg = load_programme_config()
    reading = ProgrammeReading(requested="decrease", programmes=[_programme("C", "failed_safety")])
    out = apply_engagement(reading, {"C": ("shown", ["c1"])}, cfg, "disease one")
    assert out.falsification == pytest.approx(0.35 * 0.6)
    assert out.programmes[0].engagement == "not_assessed"


def test_a_class_with_shown_engagement_reaches_the_strong_gate():
    """Two phase 3 efficacy failures: unassessed they bound the score at 59; with their drugs
    shown to engage the target, the mechanism failed and the cap is 44."""
    cfg = load_programme_config()
    reading = ProgrammeReading(
        requested="decrease",
        programmes=[_programme(n) for n in ("A", "B")],
    )
    unassessed = apply_engagement(reading, {}, cfg, "disease one")
    shown = apply_engagement(reading, {n: ("shown", ["c"]) for n in "AB"}, cfg, "disease one")
    assert unassessed.gate is not None and unassessed.gate.max == 59
    assert shown.gate is not None and shown.gate.max == 44


# ---------------------------------------------------------------- the workflow helper


def _ot_evidence(run_id: uuid.UUID, rows: list[dict[str, Any]]) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="GENE-A",
        disease="disease one",
        evidence_type=EvidenceType.GENETICS,
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="opentargets", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        claim_text="ot",
        direction=Direction.UNSPECIFIED,
        confidence=1.0,
        scope="abstract",
        source="opentargets:x",
        source_link="http://x",
        extra={"known_drugs": rows},
    )


def _row(drug: str, trial: str) -> dict[str, Any]:
    return {
        "drug_name": drug,
        "action_type": "INHIBITOR",
        "max_phase": 3.0,
        "disease_id": "",
        "disease_name": "disease one",
        "approved_in_indication": False,
        "is_approved": False,
        "trial_status": "TERMINATED",
        "stopped_for_cause": [],
        "stop_reports": [
            {
                "trial_id": trial,
                "stage": 3.0,
                "status": "TERMINATED",
                "categories": ["Negative"],
                "why": "futility",
            }
        ],
    }


async def test_the_clinical_node_helper_asks_once_per_failed_drug_and_scales_the_strength(
    harness, run_id, monkeypatch
):
    ctx, provider = harness
    claims = [_claim(run_id, "DRUG-A lowered the circulating marker in treated patients.")]
    cid = str(claims[0].evidence_id)
    asked: list[str] = []

    async def complete(req):
        asked.append(req.messages[0]["content"])
        return _completion(json.dumps({"engagement": "shown", "claim_ids": [cid]}))

    provider.complete = complete
    stored: dict[str, dict] = {}

    async def cache_get(key, model):
        return stored.get(key)

    async def cache_set(key, model, kind, payload):
        stored[key] = payload

    monkeypatch.setattr(workflow, "_llm_cache_get", cache_get)
    monkeypatch.setattr(workflow, "_llm_cache_set", cache_set)
    state: Any = {
        "target_gene": "GENE-A",
        "disease": "disease one",
        "direction": "inhibit",
        "model_fingerprint": "fp",
        "extracted_claims": claims,
        "opentargets_evidence": [_ot_evidence(run_id, [_row("DRUG-A", "nct1")])],
    }

    unassessed = await workflow._programme_reading(state)
    first = await workflow._programme_reading(state, ctx)
    second = await workflow._programme_reading(state, ctx)

    assert unassessed["programmes"][0]["engagement"] == "not_assessed"
    assert first["programmes"][0]["engagement"] == "shown"
    assert first["programmes"][0]["engagement_claim_ids"] == [cid]
    assert first["falsification"] > unassessed["falsification"]
    assert "target engagement: shown" in first["text"]
    assert len(asked) == 1  # the second call is served from the cache
    assert second["falsification"] == first["falsification"]
    assert len(stored) == 1


async def test_a_failed_engagement_question_leaves_the_reading_intact(harness, run_id, monkeypatch):
    ctx, provider = harness
    provider.complete = AsyncMock(side_effect=RuntimeError("model down"))
    monkeypatch.setattr(workflow, "_llm_cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(workflow, "_llm_cache_set", AsyncMock())
    claims = [_claim(run_id, "DRUG-A lowered the circulating marker.")]
    state: Any = {
        "target_gene": "GENE-A",
        "disease": "disease one",
        "direction": "inhibit",
        "model_fingerprint": "fp",
        "extracted_claims": claims,
        "opentargets_evidence": [_ot_evidence(run_id, [_row("DRUG-A", "nct1")])],
    }

    reading = await workflow._programme_reading(state, ctx)

    assert reading["programmes"][0]["engagement"] == "not_assessed"
    assert reading["falsification"] == pytest.approx(0.3)
