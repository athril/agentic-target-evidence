# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The safety lens: a verdict computed from the findings, then one call for the prose.

A fake provider routes on the system prompt, so the test controls what the narrative call
returns and can tell that no other call decides the verdict.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.interpretation._lens_base import lens_input_digest
from agents.interpretation.safety_lens.agent import SafetyLensAgent
from agents.interpretation.safety_lens.verdict import (
    NARRATIVE_SKILL,
    _parse_prose,
    verdict_digest,
)
from capabilities.target_validation.workflow import _lens_cache_key
from core.routing.providers.base import CompletionResult
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, Direction, EvidenceType, Provenance
from schemas.verdicts import LensVerdict
from services.evidence.liabilities import DIRECTION_HARM_SYSTEM
from tests.agents.conftest import make_task_msg

_NARRATIVE_HEAD = "# Safety narrative"
_PROSE = json.dumps({"rationale": "The profile is acceptable.", "narrative": "Paragraph one."})


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
    """Fake model: the narrative call gets the queued replies; every other call (citation
    and repair steps) gets an empty object. Records the calls."""

    def __init__(self, narrative: list[str]) -> None:
        self.narrative = narrative
        self.calls: list[str] = []
        self.requests: list[Any] = []

    async def complete(self, req: Any) -> CompletionResult:
        self.requests.append(req)
        if (req.system or "").startswith(_NARRATIVE_HEAD):
            self.calls.append(_NARRATIVE_HEAD)
            q = self.narrative
            return _completion(q.pop(0) if len(q) > 1 else q[0])
        self.calls.append("other")
        return _completion("{}")


def _ctx(run_id, trace_id, narrative: list[str] | None = None):
    fake = _Router(narrative or [_PROSE])
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
        "liability_assessed": True,
        "drug_precedent": {"status": "in_clinic"},
        "disease_tissue": "Tissue_A",
        "disease_relevant_tissues": ["Tissue_A"],
        "top_tpm_tissues": ["Tissue_B", "Tissue_C"],
        "gtex_tissue_ranks": {"Tissue_A": 2, "Tissue_B": 1, "Tissue_C": 3},
        "bulk_tpm": 40.0,
        **extra,
    }


async def _run(ctx, run_id, trace_id, spec):
    msg = make_task_msg("safety_lens", spec, run_id, trace_id)
    result = await SafetyLensAgent().run(msg, ctx)
    return LensVerdict.model_validate(result.payload["lens_verdicts"][0])


async def test_the_verdict_comes_from_the_findings_and_one_call_writes_the_prose(run_id, trace_id):
    ctx, fake = _ctx(run_id, trace_id)

    verdict = await _run(ctx, run_id, trace_id, _spec([]))

    assert verdict.overall_verdict == "support"
    assert {a.axis: a.verdict for a in verdict.axes} == {
        "toxicity": True,
        "tissue_specificity": True,
    }
    assert verdict.rationale == "The profile is acceptable."
    assert fake.calls.count(_NARRATIVE_HEAD) == 1
    assert verdict.question_results == []
    assert [(r["system"], r["status"]) for r in verdict.liability_statuses] == [
        ("organ system a", "unresolved"),
        ("organ system b", "unresolved"),
    ]


async def test_the_narrative_is_told_the_verdict_as_computed(run_id, trace_id):
    """A common-essential cancer target is neutral by rule; the narrative call must be told
    the verdict as published."""
    ctx, fake = _ctx(run_id, trace_id)
    spec = _spec(
        [],
        dependency_profile={"kind": "common_essential", "text": "Common essential."},
        disease_classes=["oncology"],
    )

    verdict = await _run(ctx, run_id, trace_id, spec)

    assert verdict.overall_verdict == "neutral"
    assert next(a for a in verdict.axes if a.axis == "toxicity").verdict is False
    req = next(r for r in fake.requests if (r.system or "").startswith(_NARRATIVE_HEAD))
    assert "Computed overall verdict (fixed): neutral" in req.messages[0]["content"]


async def test_a_label_warning_that_the_drug_worsens_the_disease_opposes(run_id, trace_id):
    worsens = {
        "system": DIRECTION_HARM_SYSTEM,
        "severity": "high",
        "source": "drug-a",
        "evidence": ['drug-a label warns: "cases of the disease were reported"'],
    }
    ctx, _ = _ctx(run_id, trace_id)
    verdict = await _run(ctx, run_id, trace_id, _spec([], candidate_liabilities=[worsens]))
    assert verdict.overall_verdict == "oppose"


async def test_an_approved_class_settles_its_findings_from_its_labels(run_id, trace_id):
    """One high-severity knockout finding left an approved class neutral; its label shows
    what use in these patients found."""
    label_id = uuid.uuid4()
    claim = _claim(run_id, "DRUG-A label carries no boxed warning").model_copy(
        update={"source_evidence_id": label_id}
    )
    spec = _spec(
        [claim],
        drug_precedent={"status": "validated", "concordant_approved": ["DRUG-A"]},
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
    ctx, _ = _ctx(run_id, trace_id)

    verdict = await _run(ctx, run_id, trace_id, spec)

    assert verdict.overall_verdict == "support"
    assert verdict.liability_statuses[0]["status"] == "resolved_by_human_data"
    assert verdict.liability_statuses[0]["claim_ids"] == [str(claim.evidence_id)]
    # the same finding with the drug approved only elsewhere stays open
    ctx, _ = _ctx(run_id, trace_id)
    elsewhere = await _run(
        ctx, run_id, trace_id, {**spec, "drug_precedent": {"status": "in_clinic"}}
    )
    assert elsewhere.overall_verdict == "neutral"


async def test_the_guards_annotate_prose_that_inverts_the_facts(run_id, trace_id):
    from services.evidence.constraint_interpret import interpret_constraint

    prose = json.dumps(
        {
            "rationale": "The profile is acceptable.",
            "narrative": "GENE-A is haploinsufficient, so inhibition is risky.",
        }
    )
    reading = interpret_constraint("GENE-A", loeuf=0.759, pli=0.0, mis_z=1.70).model_dump()
    ctx, _ = _ctx(run_id, trace_id, [prose])

    verdict = await _run(ctx, run_id, trace_id, _spec([], constraint_reading=reading))

    assert "CONSTRAINT GUARD" in verdict.narrative
    assert any(f.rule_id == "constraint_interpretation_guard" for f in verdict.validation_flags)
    families = {c.family for c in verdict.guard_checks}
    assert {"constraint", "tissue_relevance", "quantity"} <= families


_LONG = (
    "The safety profile of this target was assessed from the structured findings. "
    "Each candidate liability is listed with the status human data gave it, and the "
    "expression facts are quoted as given. "
) * 4


@pytest.mark.parametrize(
    ("counted", "flagged"), [(["abnormal lung morphology"], True), ([], False)]
)
async def test_the_omission_guard_asks_only_for_counted_mouse_systems(
    run_id, trace_id, counted, flagged
):
    """On an activator run the knockout's phenotypes are not liabilities of the requested
    change, and the prose need not name them."""
    prose = json.dumps({"rationale": "Acceptable.", "narrative": _LONG})
    ctx, _ = _ctx(run_id, trace_id, [prose])
    spec = _spec(
        [],
        ot_mouse_phenotype_labels=["abnormal lung morphology"],
        liability_mouse_phenotypes=counted,
    )

    verdict = await _run(ctx, run_id, trace_id, spec)

    assert ("PHENOTYPE OMISSION GUARD" in verdict.narrative) is flagged


async def test_an_unusable_narrative_does_not_lose_the_verdict(run_id, trace_id):
    ctx, fake = _ctx(run_id, trace_id, ["prose, not json", "more prose"])
    spec = _spec([], safety_stops_elsewhere=["drug-x in disease two: NCT1 (phase 2, terminated)"])

    verdict = await _run(ctx, run_id, trace_id, spec)

    assert verdict.overall_verdict == "support"
    assert "organ system a" in verdict.narrative and "drug-x in disease two" in verdict.narrative
    assert fake.calls.count(_NARRATIVE_HEAD) == 2


def test_the_cache_key_carries_the_narrative_skill_and_the_aggregation_version(monkeypatch):
    import agents.interpretation.safety_lens.verdict as verdict_module
    import capabilities.target_validation.workflow as wf

    spec = {"target_gene": "GENE-A", "extracted_claims": [], "source_quality": {}}

    def key() -> str:
        return _lens_cache_key(
            "GENE-A",
            "disease one",
            "inhibit",
            "safety",
            spec,
            extra=verdict_module.verdict_digest(),
            skill=NARRATIVE_SKILL,
        )

    before = key()
    monkeypatch.setattr(verdict_module, "AGGREGATION_VERSION", "test-bump")
    assert key() != before
    monkeypatch.undo()
    real = wf.load_skill
    monkeypatch.setattr(
        wf, "load_skill", lambda name: "edited" if name == NARRATIVE_SKILL else real(name)
    )
    assert key() != before
    assert verdict_digest()


def test_lens_input_digest_includes_the_extra_text():
    spec = {"extracted_claims": [], "source_quality": {}}
    assert lens_input_digest("safety", spec, "skill") != lens_input_digest(
        "safety", spec, "skill", "extra"
    )
    assert lens_input_digest("safety", spec, "skill") == lens_input_digest(
        "safety", spec, "skill", ""
    )


def test_the_lens_rules_version_is_part_of_every_lens_digest(monkeypatch):
    """Prompt-building code and post-LLM rules are not in the skill or the spec, so a change
    to them alone must still miss the cache."""
    import agents.interpretation._lens_base as base

    spec = _spec([])
    before = lens_input_digest("safety", spec, "skill")
    monkeypatch.setattr(base, "LENS_RULES_VERSION", "test-bump")
    assert lens_input_digest("safety", spec, "skill") != before


def test_a_narrative_cut_at_its_cap_keeps_its_rationale_and_whole_sentences():
    cut = '{"rationale": "No liability is open.", "narrative": "Para one ends. Para two is cu'
    assert _parse_prose(cut) == ("No liability is open.", "Para one ends.")
    # A complete reply without a narrative is still refused (and re-asked).
    assert _parse_prose('{"rationale": "r"}') is None
