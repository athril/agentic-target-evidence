# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the five interpretation lens agents."""

from __future__ import annotations

import json
import uuid
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.interpretation.biology_lens.agent import BiologyLensAgent
from agents.interpretation.clinical_lens.agent import ClinicalLensAgent
from agents.interpretation.commercial_lens.agent import CommercialLensAgent
from agents.interpretation.genetics_lens.agent import GeneticsLensAgent
from agents.interpretation.safety_lens.agent import SafetyLensAgent
from core.routing.providers.base import CompletionResult
from schemas.evidence import CoreClaim, Direction, EvidenceType
from schemas.verdicts import LensVerdict
from tests.agents.conftest import make_task_msg


def _make_completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content,
        model_used="test-model",
        input_tokens=20,
        output_tokens=60,
        latency_ms=100.0,
    )


def _valid_verdict_json(lens: str) -> str:
    return json.dumps(
        {
            "overall_verdict": "support",
            "confidence": 0.82,
            "rationale": f"Test rationale for {lens}.",
            "axes": [
                {
                    "axis": "test_axis",
                    "verdict": True,
                    "confidence": 0.82,
                    "rationale": "Evidence supports.",
                    "supporting_claim_ids": [],
                }
            ],
        }
    )


def _make_claim(run_id, trace_id, evidence_type: EvidenceType, topics=None) -> dict:
    from datetime import datetime

    from schemas.evidence import DataClass, Provenance

    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=run_id,
        gene="BRCA1",
        disease="breast cancer",
        evidence_type=evidence_type,
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="test",
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            trace_id=trace_id,
        ),
        claim_text="BRCA1 shows strong causal association with breast cancer.",
        direction=Direction.INHIBIT,
        confidence=0.9,
        topics=topics or [],
    ).model_dump(mode="json")


@pytest.fixture()
def lens_ctx(run_id, trace_id):
    provider = MagicMock()
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    from harness.context import RunContext

    ctx = RunContext(run_id=run_id, trace_id=trace_id, router=router)
    return ctx, provider


# ---------------------------------------------------------------------------
# GeneticsLensAgent
# ---------------------------------------------------------------------------


async def test_genetics_lens_returns_verdict(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_valid_verdict_json("genetics")))

    claims = [_make_claim(run_id, trace_id, EvidenceType.GENETICS)]
    msg = make_task_msg(
        "genetics_lens",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "inhibit",
            "gene_id": "ENSG001",
            "disease_id": "EFO:0001234",
            "extracted_claims": claims,
        },
        run_id,
        trace_id,
    )

    result = await GeneticsLensAgent().run(msg, ctx)

    assert result.intent == "result"
    assert isinstance(result.payload, dict)
    verdicts = result.payload.get("lens_verdicts", [])
    assert len(verdicts) == 1
    lv = LensVerdict.model_validate(verdicts[0])
    assert lv.lens == "genetics"
    assert lv.overall_verdict == "support"
    assert lv.confidence == pytest.approx(0.82)


async def test_genetics_lens_filters_non_genetics_claims(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict_json("genetics"))

    provider.complete = mock_complete

    # Only PATENT claim — should be filtered out, LLM sees empty list
    patent_claim = _make_claim(run_id, trace_id, EvidenceType.PATENT)
    genetics_claim = _make_claim(run_id, trace_id, EvidenceType.GENETICS)
    msg = make_task_msg(
        "genetics_lens",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [patent_claim, genetics_claim],
        },
        run_id,
        trace_id,
    )

    await GeneticsLensAgent().run(msg, ctx)

    # Only 1 genetics claim should appear in the prompt
    assert (
        "1)" in captured[0]
        or "(1)" in captured[0]
        or "1\n" in captured[0]
        or "Relevant claims (1)" in captured[0]
    )


async def test_genetics_lens_fallback_on_bad_llm_response(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion("not json at all"))

    msg = make_task_msg(
        "genetics_lens",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "unspecified",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
        },
        run_id,
        trace_id,
    )

    result = await GeneticsLensAgent().run(msg, ctx)

    verdicts = result.payload.get("lens_verdicts", [])
    assert len(verdicts) == 1
    lv = LensVerdict.model_validate(verdicts[0])
    assert lv.overall_verdict == "insufficient_evidence"


# ---------------------------------------------------------------------------
# BiologyLensAgent
# ---------------------------------------------------------------------------


async def test_biology_lens_returns_verdict(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_valid_verdict_json("biology")))

    claims = [_make_claim(run_id, trace_id, EvidenceType.ARTICLE, topics=["biology"])]
    msg = make_task_msg(
        "biology_lens",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": claims,
        },
        run_id,
        trace_id,
    )

    result = await BiologyLensAgent().run(msg, ctx)

    verdicts = result.payload.get("lens_verdicts", [])
    lv = LensVerdict.model_validate(verdicts[0])
    assert lv.lens == "biology"
    assert lv.overall_verdict == "support"


async def test_biology_lens_parses_narrative_with_raw_newlines(run_id, trace_id, lens_ctx):
    """Regression: local models emit multi-paragraph `narrative` fields with literal
    newlines between paragraphs instead of `\\n` escapes. Strict json.loads rejects
    raw control characters in strings, which silently discarded an otherwise valid
    verdict as 'LLM response could not be parsed'. _parse_verdict uses strict=False.
    """
    ctx, provider = lens_ctx
    # Note: a real newline inside the JSON string value, not an escaped \n.
    raw = (
        '{"overall_verdict": "support", "confidence": 0.85, '
        '"rationale": "Strong support.", '
        '"narrative": "Para one about mechanism.\n\nPara two about druggability.", '
        '"axes": []}'
    )
    provider.complete = AsyncMock(return_value=_make_completion(raw))

    claims = [_make_claim(run_id, trace_id, EvidenceType.ARTICLE, topics=["biology"])]
    msg = make_task_msg(
        "biology_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": claims,
        },
        run_id,
        trace_id,
    )

    result = await BiologyLensAgent().run(msg, ctx)

    lv = LensVerdict.model_validate(result.payload["lens_verdicts"][0])
    assert lv.overall_verdict == "support"
    assert lv.confidence == 0.85
    assert "Para one" in lv.narrative and "Para two" in lv.narrative


async def test_biology_lens_recovers_premature_root_close(run_id, trace_id, lens_ctx):
    """Regression: local models sometimes close the root object early and emit the
    remaining keys (e.g. `axes`) as siblings, e.g. `{...,"narrative":"..."},\\n
    "axes":[...]}`. Strict json.loads raises 'Extra data' on the leftover and the
    whole verdict was discarded as 'LLM response could not be parsed'. loads_recovering
    splices out the stray brace so the (otherwise valid) verdict and its axes survive.
    """
    ctx, provider = lens_ctx
    # Stray `}` after `narrative`, with a real newline in the narrative for good measure.
    raw = (
        '{"overall_verdict": "support", "confidence": 0.9, '
        '"rationale": "Strong support.", '
        '"narrative": "Para one.\n\nPara two."'
        "},\n"
        '  "axes": [{"axis": "druggability", "verdict": true, "confidence": 0.9, '
        '"rationale": "Tractable.", "supporting_claim_ids": ["abc"]}]'
        "}"
    )
    provider.complete = AsyncMock(return_value=_make_completion(raw))

    claims = [_make_claim(run_id, trace_id, EvidenceType.ARTICLE, topics=["biology"])]
    msg = make_task_msg(
        "biology_lens",
        {
            "target_gene": "PNPLA3",
            "disease": "MASH",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": claims,
        },
        run_id,
        trace_id,
    )

    result = await BiologyLensAgent().run(msg, ctx)

    lv = LensVerdict.model_validate(result.payload["lens_verdicts"][0])
    assert lv.overall_verdict == "support"
    assert lv.confidence == 0.9
    assert "Para one" in lv.narrative and "Para two" in lv.narrative
    assert [ax.axis for ax in lv.axes] == ["druggability"]


# ---------------------------------------------------------------------------
# BiologyLensAgent — DepMap relevance caveat
# ---------------------------------------------------------------------------


_DEPMAP_NON_ONCOLOGY_SPEC = {
    "target_gene": "TRPC6",
    "disease": "FSGS",
    "direction": "inhibit",
    "gene_id": "",
    "disease_id": "",
    "extracted_claims": [],
    "depmap_text": "low dependency across cell lines",
    "depmap_mean_chronos": -0.1,
    "depmap_dependency_fraction": 0.02,
    "depmap_is_common_essential": False,
    "is_oncology_indication": False,
}


async def test_depmap_caveat_injected_for_non_oncology(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict_json("biology"))

    provider.complete = mock_complete

    msg = make_task_msg("biology_lens", _DEPMAP_NON_ONCOLOGY_SPEC, run_id, trace_id)

    await BiologyLensAgent().run(msg, ctx)

    assert "non-oncology indication" in captured[0]
    assert "therapeutic window" in captured[0]


async def test_depmap_caveat_absent_for_oncology(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict_json("biology"))

    provider.complete = mock_complete

    spec = {**_DEPMAP_NON_ONCOLOGY_SPEC, "is_oncology_indication": True}
    msg = make_task_msg("biology_lens", spec, run_id, trace_id)

    await BiologyLensAgent().run(msg, ctx)

    assert "non-oncology indication" not in captured[0]


# ---------------------------------------------------------------------------
# SafetyLensAgent
# ---------------------------------------------------------------------------


async def test_safety_lens_returns_verdict(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_valid_verdict_json("safety")))

    claims = [_make_claim(run_id, trace_id, EvidenceType.OMICS)]
    msg = make_task_msg(
        "safety_lens",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": claims,
        },
        run_id,
        trace_id,
    )

    result = await SafetyLensAgent().run(msg, ctx)

    verdicts = result.payload.get("lens_verdicts", [])
    lv = LensVerdict.model_validate(verdicts[0])
    assert lv.lens == "safety"


async def test_safety_lens_includes_structured_text_in_prompt(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict_json("safety"))

    provider.complete = mock_complete

    structured = "Structured expression / constraint / genetics evidence:\nTRPC6 GTEx v8 expression in Lung: 23.2 TPM median.\ngnomAD LOEUF=0.759, pLI=0.00"
    msg = make_task_msg(
        "safety_lens",
        {
            "target_gene": "TRPC6",
            "disease": "focal segmental glomerulosclerosis",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "ot_safety_text": "",
            "ot_mouse_text": "",
            "ot_safety_liability_count": 0,
            "ot_safety_liability_events": [],
            "safety_structured_text": structured,
        },
        run_id,
        trace_id,
    )

    await SafetyLensAgent().run(msg, ctx)

    assert "Lung: 23.2 TPM" in captured[0]
    assert "LOEUF=0.759" in captured[0]


async def test_safety_lens_no_structured_text_still_works(run_id, trace_id, lens_ctx):
    """safety_structured_text absent → no crash, prompt still well-formed."""
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_valid_verdict_json("safety")))

    msg = make_task_msg(
        "safety_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
        },
        run_id,
        trace_id,
    )

    result = await SafetyLensAgent().run(msg, ctx)

    verdicts = result.payload.get("lens_verdicts", [])
    assert len(verdicts) == 1
    assert LensVerdict.model_validate(verdicts[0]).lens == "safety"


# ---------------------------------------------------------------------------
# SafetyLensAgent — WS7: expression breadth + GoF-tolerance framing
# ---------------------------------------------------------------------------


async def test_safety_lens_injects_gof_tolerance_support(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict_json("safety"))

    provider.complete = mock_complete

    msg = make_task_msg(
        "safety_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "constraint_reading": {"is_lof_tolerant": True, "summary_text": ""},
            "mechanism_direction": {"mechanism": "gof"},
        },
        run_id,
        trace_id,
    )

    await SafetyLensAgent().run(msg, ctx)

    assert "SUPPORTS the tolerability" in captured[0]


async def test_safety_lens_no_gof_tolerance_text_when_lof_mechanism(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict_json("safety"))

    provider.complete = mock_complete

    msg = make_task_msg(
        "safety_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "constraint_reading": {"is_lof_tolerant": True, "summary_text": ""},
            "mechanism_direction": {"mechanism": "lof"},
        },
        run_id,
        trace_id,
    )

    await SafetyLensAgent().run(msg, ctx)

    assert "SUPPORTS the tolerability" not in captured[0]


async def test_safety_lens_injects_expression_breadth_caveat(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict_json("safety"))

    provider.complete = mock_complete

    msg = make_task_msg(
        "safety_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "bulk_tpm": 25.0,
            "hpa_specificity": "Low tissue specificity",
            "disease_tissue": "kidney",
        },
        run_id,
        trace_id,
    )

    await SafetyLensAgent().run(msg, ctx)

    assert "breadth or magnitude alone is NOT" in captured[0]


def _verdict_json(*, rationale: str = "ok", narrative: str = "ok", axis_rationale: str = "ok"):
    return json.dumps(
        {
            "overall_verdict": "support",
            "confidence": 0.7,
            "rationale": rationale,
            "narrative": narrative,
            "axes": [
                {
                    "axis": "toxicity",
                    "verdict": True,
                    "confidence": 0.7,
                    "rationale": axis_rationale,
                    "supporting_claim_ids": [],
                }
            ],
        }
    )


async def test_safety_lens_constraint_guard_annotates_haploinsufficiency(run_id, trace_id, lens_ctx):
    """LOEUF=0.759 is LoF-tolerant; a 'haploinsufficient' claim must be annotated + flagged."""
    from services.evidence.constraint_interpret import interpret_constraint

    ctx, provider = lens_ctx
    reading = interpret_constraint("TRPC6", loeuf=0.759, pli=0.0, mis_z=1.70).model_dump()
    bad = _verdict_json(narrative="TRPC6 is haploinsufficient, so inhibition is risky.")
    provider.complete = AsyncMock(return_value=_make_completion(bad))

    msg = make_task_msg(
        "safety_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "constraint_reading": reading,
        },
        run_id,
        trace_id,
    )

    result = await SafetyLensAgent().run(msg, ctx)
    lv = LensVerdict.model_validate(result.payload["lens_verdicts"][0])
    assert "CONSTRAINT GUARD" in lv.narrative
    assert any(f.rule_id == "constraint_interpretation_guard" for f in lv.validation_flags)


async def test_safety_lens_constraint_guard_silent_on_clean_verdict(run_id, trace_id, lens_ctx):
    from services.evidence.constraint_interpret import interpret_constraint

    ctx, provider = lens_ctx
    reading = interpret_constraint("TRPC6", loeuf=0.759, pli=0.0, mis_z=1.70).model_dump()
    clean = _verdict_json(narrative="TRPC6 is LoF-tolerant; reduced dosage is tolerated.")
    provider.complete = AsyncMock(return_value=_make_completion(clean))

    msg = make_task_msg(
        "safety_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "constraint_reading": reading,
        },
        run_id,
        trace_id,
    )

    result = await SafetyLensAgent().run(msg, ctx)
    lv = LensVerdict.model_validate(result.payload["lens_verdicts"][0])
    assert "CONSTRAINT GUARD" not in lv.narrative
    assert lv.validation_flags == []


async def test_safety_lens_tissue_relevance_guard_annotates_bulk_rank_misuse(
    run_id, trace_id, lens_ctx
):
    """Naming a top-bulk-TPM, non-disease tissue as disease-relevant must be annotated + flagged."""
    ctx, provider = lens_ctx
    bad = _verdict_json(
        narrative="Lung is the disease-relevant tissue here given its high expression."
    )
    provider.complete = AsyncMock(return_value=_make_completion(bad))

    msg = make_task_msg(
        "safety_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "top_tpm_tissues": ["Lung", "Esophagus", "Thyroid"],
            "disease_relevant_tissues": ["Kidney_Cortex"],
        },
        run_id,
        trace_id,
    )

    result = await SafetyLensAgent().run(msg, ctx)
    lv = LensVerdict.model_validate(result.payload["lens_verdicts"][0])
    assert "TISSUE RELEVANCE GUARD" in lv.narrative
    assert any(f.rule_id == "tissue_relevance_guard" for f in lv.validation_flags)


async def test_safety_lens_tissue_relevance_guard_silent_when_disease_tissue_named(
    run_id, trace_id, lens_ctx
):
    ctx, provider = lens_ctx
    clean = _verdict_json(
        narrative="Kidney_Cortex is the disease-relevant tissue. Lung shows high bulk TPM."
    )
    provider.complete = AsyncMock(return_value=_make_completion(clean))

    msg = make_task_msg(
        "safety_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "top_tpm_tissues": ["Lung", "Esophagus", "Thyroid"],
            "disease_relevant_tissues": ["Kidney_Cortex"],
        },
        run_id,
        trace_id,
    )

    result = await SafetyLensAgent().run(msg, ctx)
    lv = LensVerdict.model_validate(result.payload["lens_verdicts"][0])
    assert "TISSUE RELEVANCE GUARD" not in lv.narrative
    assert lv.validation_flags == []


async def test_biology_lens_tissue_relevance_guard_annotates_bulk_rank_misuse(
    run_id, trace_id, lens_ctx
):
    """Biology lens: naming a top-bulk-TPM, non-disease tissue as relevant is annotated + flagged."""
    ctx, provider = lens_ctx
    bad = _verdict_json(
        narrative="Lung is the disease-relevant tissue, supporting the mechanism."
    )
    provider.complete = AsyncMock(return_value=_make_completion(bad))

    msg = make_task_msg(
        "biology_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "top_tpm_tissues": ["Lung", "Esophagus", "Thyroid"],
            "disease_relevant_tissues": ["Kidney_Cortex"],
        },
        run_id,
        trace_id,
    )

    result = await BiologyLensAgent().run(msg, ctx)
    lv = LensVerdict.model_validate(result.payload["lens_verdicts"][0])
    assert "TISSUE RELEVANCE GUARD" in lv.narrative
    assert any(f.rule_id == "tissue_relevance_guard" for f in lv.validation_flags)


async def test_biology_lens_tissue_relevance_guard_silent_when_disease_tissue_named(
    run_id, trace_id, lens_ctx
):
    ctx, provider = lens_ctx
    clean = _verdict_json(
        narrative="Kidney_Cortex is the disease-relevant tissue. Lung shows high bulk TPM."
    )
    provider.complete = AsyncMock(return_value=_make_completion(clean))

    msg = make_task_msg(
        "biology_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "top_tpm_tissues": ["Lung", "Esophagus", "Thyroid"],
            "disease_relevant_tissues": ["Kidney_Cortex"],
        },
        run_id,
        trace_id,
    )

    result = await BiologyLensAgent().run(msg, ctx)
    lv = LensVerdict.model_validate(result.payload["lens_verdicts"][0])
    assert "TISSUE RELEVANCE GUARD" not in lv.narrative
    assert lv.validation_flags == []


def test_biology_lens_contract_consumes_tissue_relevance_fields():
    from agents.interpretation.biology_lens.contract import CONTRACT

    assert "top_tpm_tissues" in CONTRACT.consumes
    assert "disease_relevant_tissues" in CONTRACT.consumes


def test_safety_lens_contract_includes_safety_structured_text():
    from agents.interpretation.safety_lens.contract import CONTRACT

    assert "safety_structured_text" in CONTRACT.consumes


def test_safety_lens_contract_validate_inbound_accepts_new_field(run_id, trace_id):
    import uuid

    from agents.interpretation.safety_lens.contract import CONTRACT
    from harness.contract import validate_inbound
    from schemas.messages import AgentMessage

    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="planner",
        to_agent="safety_lens",
        intent="task",
        task_spec={
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "ot_safety_text": "",
            "ot_mouse_text": "",
            "ot_safety_liability_count": 0,
            "ot_safety_liability_events": [],
            "safety_structured_text": "GTEx: Lung=23.2 TPM.",
        },
        trace_id=trace_id,
    )
    validate_inbound(msg, CONTRACT)  # must not raise


def test_safety_lens_contract_validate_inbound_rejects_undeclared_field(run_id, trace_id):
    import uuid

    from agents.interpretation.safety_lens.contract import CONTRACT
    from core.exceptions import ContractViolation
    from harness.contract import validate_inbound
    from schemas.messages import AgentMessage

    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="planner",
        to_agent="safety_lens",
        intent="task",
        task_spec={"target_gene": "TRPC6", "undeclared_field": "boom"},
        trace_id=trace_id,
    )
    with pytest.raises(ContractViolation):
        validate_inbound(msg, CONTRACT)


# ---------------------------------------------------------------------------
# source_quality: contract consumes + validate_inbound (all 6 lenses)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path",
    [
        "agents.interpretation.genetics_lens.contract",
        "agents.interpretation.biology_lens.contract",
        "agents.interpretation.safety_lens.contract",
        "agents.interpretation.clinical_lens.contract",
        "agents.interpretation.commercial_lens.contract",
        "agents.interpretation.regulatory_lens.contract",
    ],
)
def test_lens_contract_declares_source_quality(module_path):
    import importlib

    contract = importlib.import_module(module_path).CONTRACT
    assert "source_quality" in contract.consumes


@pytest.mark.parametrize(
    "module_path",
    [
        "agents.interpretation.genetics_lens.contract",
        "agents.interpretation.biology_lens.contract",
        "agents.interpretation.safety_lens.contract",
        "agents.interpretation.clinical_lens.contract",
        "agents.interpretation.commercial_lens.contract",
        "agents.interpretation.regulatory_lens.contract",
    ],
)
def test_lens_contract_validate_inbound_accepts_source_quality_present(
    module_path, run_id, trace_id
):
    import importlib
    import uuid

    from harness.contract import validate_inbound
    from schemas.messages import AgentMessage

    contract = importlib.import_module(module_path).CONTRACT
    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="planner",
        to_agent=contract.name,
        intent="task",
        task_spec={"source_quality": {"some-evidence-id": {"sjr_quartile": "Q1"}}},
        trace_id=trace_id,
    )
    validate_inbound(msg, contract)  # must not raise


@pytest.mark.parametrize(
    "module_path",
    [
        "agents.interpretation.genetics_lens.contract",
        "agents.interpretation.biology_lens.contract",
        "agents.interpretation.safety_lens.contract",
        "agents.interpretation.clinical_lens.contract",
        "agents.interpretation.commercial_lens.contract",
        "agents.interpretation.regulatory_lens.contract",
    ],
)
def test_lens_contract_validate_inbound_accepts_source_quality_absent(
    module_path, run_id, trace_id
):
    import importlib
    import uuid

    from harness.contract import validate_inbound
    from schemas.messages import AgentMessage

    contract = importlib.import_module(module_path).CONTRACT
    msg = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=run_id,
        from_agent="planner",
        to_agent=contract.name,
        intent="task",
        task_spec={"target_gene": "BRCA1"},
        trace_id=trace_id,
    )
    validate_inbound(msg, contract)  # must not raise


# ---------------------------------------------------------------------------
# _claims_to_json: source-quality enrichment
# ---------------------------------------------------------------------------


def test_claims_to_json_injects_quality_on_hit(run_id, trace_id):
    from agents.interpretation._lens_base import _claims_to_json

    source_id = uuid.uuid4()
    claim = CoreClaim.model_validate(_make_claim(run_id, trace_id, EvidenceType.GENETICS))
    claim = claim.model_copy(update={"source_evidence_id": source_id})
    quality_map = {
        str(source_id): {
            "sjr_score": 0.85,
            "sjr_quartile": "Q1",
            "predatory_flag": False,
            "preprint_flag": False,
        }
    }

    items = json.loads(_claims_to_json([claim], quality_map))

    assert items[0]["quality"] == {
        "score": 0.85,
        "quartile": "Q1",
        "predatory": False,
        "preprint": False,
    }


def test_claims_to_json_degrades_gracefully_on_miss(run_id, trace_id):
    from agents.interpretation._lens_base import _claims_to_json

    claim = CoreClaim.model_validate(_make_claim(run_id, trace_id, EvidenceType.GENETICS))
    # source_evidence_id is None — no quality lookup is possible.
    items = json.loads(_claims_to_json([claim], {"some-other-id": {"sjr_quartile": "Q1"}}))

    assert "quality" not in items[0]


def test_claims_to_json_works_without_quality_map(run_id, trace_id):
    from agents.interpretation._lens_base import _claims_to_json

    claim = CoreClaim.model_validate(_make_claim(run_id, trace_id, EvidenceType.GENETICS))
    items = json.loads(_claims_to_json([claim]))

    assert "quality" not in items[0]


# ---------------------------------------------------------------------------
# ClinicalLensAgent
# ---------------------------------------------------------------------------


async def test_clinical_lens_returns_verdict(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_valid_verdict_json("clinical")))

    claims = [_make_claim(run_id, trace_id, EvidenceType.CLINICAL_TRIAL)]
    msg = make_task_msg(
        "clinical_lens",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": claims,
        },
        run_id,
        trace_id,
    )

    result = await ClinicalLensAgent().run(msg, ctx)

    verdicts = result.payload.get("lens_verdicts", [])
    lv = LensVerdict.model_validate(verdicts[0])
    assert lv.lens == "clinical"


# ---------------------------------------------------------------------------
# CommercialLensAgent
# ---------------------------------------------------------------------------


async def test_commercial_lens_returns_verdict(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_valid_verdict_json("commercial")))

    claims = [_make_claim(run_id, trace_id, EvidenceType.PATENT)]
    msg = make_task_msg(
        "commercial_lens",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": claims,
            "patent_count": 12,
            "trial_count": 3,
        },
        run_id,
        trace_id,
    )

    result = await CommercialLensAgent().run(msg, ctx)

    verdicts = result.payload.get("lens_verdicts", [])
    lv = LensVerdict.model_validate(verdicts[0])
    assert lv.lens == "commercial"


async def test_commercial_lens_includes_counts_in_prompt(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict_json("commercial"))

    provider.complete = mock_complete

    msg = make_task_msg(
        "commercial_lens",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "patent_count": 42,
            "trial_count": 7,
        },
        run_id,
        trace_id,
    )

    await CommercialLensAgent().run(msg, ctx)

    assert "42" in captured[0]
    assert "7" in captured[0]


# ---------------------------------------------------------------------------
# LensVerdict schema round-trip
# ---------------------------------------------------------------------------


def test_lens_verdict_round_trip(run_id):
    from schemas.verdicts import AxisVerdict

    verdict = LensVerdict(
        run_id=run_id,
        trace_id="t1",
        lens="genetics",
        target_gene="BRCA1",
        disease="breast cancer",
        overall_verdict="support",
        confidence=0.9,
        axes=[
            AxisVerdict(
                axis="causality",
                verdict=True,
                confidence=0.9,
                rationale="ok",
                supporting_claim_ids=[],
            )
        ],
        rationale="Supports.",
    )
    dumped = verdict.model_dump(mode="json")
    restored = LensVerdict.from_dict(dumped)
    assert restored == verdict


def test_lens_verdict_invalid_overall_verdict_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LensVerdict(
            run_id=uuid.uuid4(),
            trace_id="t",
            lens="genetics",
            target_gene="G",
            disease="D",
            overall_verdict="maybe",  # not in the Literal
        )


# ---------------------------------------------------------------------------
# LENS_EVIDENCE_TYPES
# ---------------------------------------------------------------------------


def test_lens_evidence_types_safety_includes_regulatory():
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES

    assert EvidenceType.REGULATORY in LENS_EVIDENCE_TYPES["safety"]


def test_lens_evidence_types_commercial_includes_regulatory():
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES

    assert EvidenceType.REGULATORY in LENS_EVIDENCE_TYPES["commercial"]


def test_lens_evidence_types_regulatory_entry_exists():
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES

    assert "regulatory" in LENS_EVIDENCE_TYPES
    assert LENS_EVIDENCE_TYPES["regulatory"] == (EvidenceType.REGULATORY,)


# ---------------------------------------------------------------------------
# Lenses no longer attach validation flags (reverted to clean LLM reasoning)
# ---------------------------------------------------------------------------


async def test_verdicts_without_errors_have_empty_flags(run_id, trace_id, lens_ctx):
    """Lens verdicts carry no validation flags — post-parse validation was removed."""
    ctx, provider = lens_ctx
    good_verdict = json.dumps(
        {
            "overall_verdict": "insufficient_evidence",
            "confidence": 0.2,
            "rationale": "No results passed screening in this retrieval run.",
            "narrative": "No results passed screening. A dedicated review is recommended.",
            "axes": [],
        }
    )
    provider.complete = AsyncMock(return_value=_make_completion(good_verdict))

    msg = make_task_msg(
        "clinical_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
        },
        run_id,
        trace_id,
    )

    result = await ClinicalLensAgent().run(msg, ctx)
    verdicts = result.payload.get("lens_verdicts", [])
    assert verdicts
    lv = LensVerdict.model_validate(verdicts[0])
    assert lv.validation_flags == [], "Correctly worded verdict must have no validation flags"


def test_filter_claims_keeps_regulatory_for_safety(run_id, trace_id):
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    claim = _make_claim(run_id, trace_id, EvidenceType.REGULATORY)
    claims_obj = [
        __import__("schemas.evidence", fromlist=["CoreClaim"]).CoreClaim.model_validate(claim)
    ]
    kept = _filter_claims(claims_obj, LENS_EVIDENCE_TYPES["safety"])
    assert len(kept) == 1


def test_filter_claims_keeps_regulatory_for_commercial(run_id, trace_id):
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    claim = _make_claim(run_id, trace_id, EvidenceType.REGULATORY)
    claims_obj = [
        __import__("schemas.evidence", fromlist=["CoreClaim"]).CoreClaim.model_validate(claim)
    ]
    kept = _filter_claims(claims_obj, LENS_EVIDENCE_TYPES["commercial"])
    assert len(kept) == 1


def test_filter_claims_drops_regulatory_for_genetics(run_id, trace_id):
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    claim = _make_claim(run_id, trace_id, EvidenceType.REGULATORY)
    claims_obj = [
        __import__("schemas.evidence", fromlist=["CoreClaim"]).CoreClaim.model_validate(claim)
    ]
    kept = _filter_claims(claims_obj, LENS_EVIDENCE_TYPES["genetics"])
    assert len(kept) == 0


def test_filter_claims_drops_regulatory_for_biology(run_id, trace_id):
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    claim = _make_claim(run_id, trace_id, EvidenceType.REGULATORY)
    claims_obj = [
        __import__("schemas.evidence", fromlist=["CoreClaim"]).CoreClaim.model_validate(claim)
    ]
    kept = _filter_claims(claims_obj, LENS_EVIDENCE_TYPES["biology"])
    assert len(kept) == 0


def test_filter_claims_drops_regulatory_for_clinical(run_id, trace_id):
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    claim = _make_claim(run_id, trace_id, EvidenceType.REGULATORY)
    claims_obj = [
        __import__("schemas.evidence", fromlist=["CoreClaim"]).CoreClaim.model_validate(claim)
    ]
    kept = _filter_claims(claims_obj, LENS_EVIDENCE_TYPES["clinical"])
    assert len(kept) == 0


# ---------------------------------------------------------------------------
# Literature topic fan-out: ARTICLE claims route by `topics`, not evidence_type
# ---------------------------------------------------------------------------


def _article_claim(run_id, trace_id, topics):
    from schemas.evidence import CoreClaim

    return CoreClaim.model_validate(
        _make_claim(run_id, trace_id, EvidenceType.ARTICLE, topics=topics)
    )


def test_topic_routes_literature_to_tagged_lens(run_id, trace_id):
    """A safety-tagged ARTICLE reaches the safety lens even though ARTICLE is not in
    safety's LENS_EVIDENCE_TYPES."""
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    claims = [_article_claim(run_id, trace_id, ["safety"])]
    kept = _filter_claims(claims, LENS_EVIDENCE_TYPES["safety"], "safety")
    assert len(kept) == 1


def test_topic_fans_one_claim_to_multiple_lenses(run_id, trace_id):
    """One claim tagged [genetics, safety] reaches both lenses."""
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    claims = [_article_claim(run_id, trace_id, ["genetics", "safety"])]
    assert len(_filter_claims(claims, LENS_EVIDENCE_TYPES["genetics"], "genetics")) == 1
    assert len(_filter_claims(claims, LENS_EVIDENCE_TYPES["safety"], "safety")) == 1


def test_biology_is_topic_routed_for_literature(run_id, trace_id):
    """After tightening: biology only sees literature tagged `biology`, not all
    literature. A safety-only-tagged ARTICLE does NOT reach biology; a biology-tagged
    one does."""
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    safety_only = [_article_claim(run_id, trace_id, ["safety"])]
    biology_tagged = [_article_claim(run_id, trace_id, ["biology"])]
    assert len(_filter_claims(safety_only, LENS_EVIDENCE_TYPES["biology"], "biology")) == 0
    assert len(_filter_claims(biology_tagged, LENS_EVIDENCE_TYPES["biology"], "biology")) == 1


def test_untagged_literature_reaches_no_lens_at_routing(run_id, trace_id):
    """An explicitly-untagged literature claim reaches no lens at the routing layer.
    (The biology fallback for untagged literature is applied upstream in
    claim_extraction, not here.)"""
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    claims = [_article_claim(run_id, trace_id, [])]
    for lens in ("biology", "genetics", "safety", "clinical"):
        assert len(_filter_claims(claims, LENS_EVIDENCE_TYPES[lens], lens)) == 0


def test_topic_not_routed_to_commercial(run_id, trace_id):
    """commercial is not a literature-consuming lens; a tagged ARTICLE never reaches it."""
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    claims = [_article_claim(run_id, trace_id, ["safety"])]
    assert len(_filter_claims(claims, LENS_EVIDENCE_TYPES["commercial"], "commercial")) == 0


def test_filter_without_lens_is_pure_type_filter(run_id, trace_id):
    """Backward compat: omitting `lens` gives type-only filtering (no topic fan-out)."""
    from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, _filter_claims

    claims = [_article_claim(run_id, trace_id, ["safety"])]
    assert len(_filter_claims(claims, LENS_EVIDENCE_TYPES["safety"])) == 0


def test_claim_matches_lens_predicate(run_id, trace_id):
    """The shared predicate used by both routing and report citation selection."""
    from agents.interpretation._lens_base import claim_matches_lens
    from schemas.evidence import CoreClaim

    bio_lit = CoreClaim.model_validate(
        _make_claim(run_id, trace_id, EvidenceType.ARTICLE, topics=["biology"])
    )
    genetics_struct = CoreClaim.model_validate(_make_claim(run_id, trace_id, EvidenceType.GENETICS))
    assert claim_matches_lens(bio_lit, "biology") is True
    assert claim_matches_lens(bio_lit, "safety") is False
    assert claim_matches_lens(genetics_struct, "genetics") is True
    assert claim_matches_lens(genetics_struct, "biology") is False


# ---------------------------------------------------------------------------
# LensVerdict schema — schema_version + validation_flags
# ---------------------------------------------------------------------------


def test_lens_verdict_regulatory_round_trip(run_id):
    from schemas.verdicts import AxisVerdict

    verdict = LensVerdict(
        run_id=run_id,
        trace_id="t-reg",
        lens="regulatory",
        target_gene="BRCA1",
        disease="breast cancer",
        overall_verdict="support",
        confidence=0.75,
        axes=[
            AxisVerdict(
                axis="approval_precedent",
                verdict=True,
                confidence=0.8,
                rationale="Approved modulator exists.",
                supporting_claim_ids=[],
            ),
            AxisVerdict(
                axis="label_safety",
                verdict=True,
                confidence=0.7,
                rationale="No black-box warnings.",
                supporting_claim_ids=[],
            ),
            AxisVerdict(
                axis="regulatory_de_risking",
                verdict=True,
                confidence=0.75,
                rationale="Prior approval lowers risk.",
                supporting_claim_ids=[],
            ),
        ],
        rationale="Regulatory landscape is navigable.",
    )
    dumped = verdict.model_dump(mode="json")
    assert dumped["schema_version"] == "1.0"
    assert dumped["lens"] == "regulatory"
    restored = LensVerdict.from_dict(dumped)
    assert restored == verdict


def test_lens_verdict_schema_version_is_1_0(run_id):
    v = LensVerdict(run_id=run_id, trace_id="t", lens="genetics", target_gene="G", disease="D")
    assert v.schema_version == "1.0"


def test_lens_verdict_invalid_lens_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LensVerdict(
            run_id=uuid.uuid4(),
            trace_id="t",
            lens="unknown_lens",
            target_gene="G",
            disease="D",
        )


# ---------------------------------------------------------------------------
# RegulatoryLensAgent
# ---------------------------------------------------------------------------


async def test_regulatory_lens_returns_verdict(run_id, trace_id, lens_ctx):
    from agents.interpretation.regulatory_lens.agent import RegulatoryLensAgent

    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_valid_verdict_json("regulatory")))

    claims = [_make_claim(run_id, trace_id, EvidenceType.REGULATORY)]
    msg = make_task_msg(
        "regulatory_lens",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": claims,
            "fda_label_text": "Drug: testdrug | MoA: inhibits BRCA1.",
        },
        run_id,
        trace_id,
    )

    result = await RegulatoryLensAgent().run(msg, ctx)

    assert result.intent == "result"
    verdicts = result.payload.get("lens_verdicts", [])
    assert len(verdicts) == 1
    lv = LensVerdict.model_validate(verdicts[0])
    assert lv.lens == "regulatory"
    assert lv.overall_verdict == "support"


async def test_regulatory_lens_includes_fda_label_text_in_prompt(run_id, trace_id, lens_ctx):
    from agents.interpretation.regulatory_lens.agent import RegulatoryLensAgent

    ctx, provider = lens_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict_json("regulatory"))

    provider.complete = mock_complete

    fda_text = "FDA-approved drug labels (MoA, indications, label safety flags):\nDrug: imatinib | NDA: NDA21588 | MoA: inhibits BCR-ABL tyrosine kinase."
    msg = make_task_msg(
        "regulatory_lens",
        {
            "target_gene": "ABL1",
            "disease": "CML",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "fda_label_text": fda_text,
        },
        run_id,
        trace_id,
    )

    await RegulatoryLensAgent().run(msg, ctx)

    assert "imatinib" in captured[0]
    assert "NDA21588" in captured[0]


async def test_regulatory_lens_empty_evidence_short_circuits(run_id, trace_id, lens_ctx):
    """With no claims and no fda_label_text, the guard fires and the LLM is never called."""
    from agents.interpretation.regulatory_lens.agent import RegulatoryLensAgent

    ctx, provider = lens_ctx
    provider.complete = AsyncMock(side_effect=AssertionError("LLM must not be called"))

    msg = make_task_msg(
        "regulatory_lens",
        {
            "target_gene": "TRPC6",
            "disease": "FSGS",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
        },
        run_id,
        trace_id,
    )

    result = await RegulatoryLensAgent().run(msg, ctx)

    verdicts = result.payload.get("lens_verdicts", [])
    assert len(verdicts) == 1
    lv = LensVerdict.model_validate(verdicts[0])
    assert lv.overall_verdict == "insufficient_evidence"
    assert lv.confidence == 0.0
    assert lv.axes == []
    provider.complete.assert_not_awaited()


def test_regulatory_lens_contract_consumes_fda_label_text():
    from agents.interpretation.regulatory_lens.contract import CONTRACT

    assert "fda_label_text" in CONTRACT.consumes


def test_safety_lens_contract_consumes_faers_text():
    from agents.interpretation.safety_lens.contract import CONTRACT

    assert "faers_text" in CONTRACT.consumes


def test_commercial_lens_contract_consumes_fda_label_text():
    from agents.interpretation.commercial_lens.contract import CONTRACT

    assert "fda_label_text" in CONTRACT.consumes


def test_commercial_lens_contract_consumes_orphanet_prevalence_text():
    from agents.interpretation.commercial_lens.contract import CONTRACT

    assert "orphanet_prevalence_text" in CONTRACT.consumes


async def test_commercial_lens_includes_orphanet_prevalence_in_prompt(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    captured = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict_json("commercial"))

    provider.complete = mock_complete

    msg = make_task_msg(
        "commercial_lens",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "inhibit",
            "gene_id": "",
            "disease_id": "",
            "extracted_claims": [],
            "patent_count": 0,
            "trial_count": 0,
            "orphanet_prevalence_text": "Orphanet prevalence: Some rare disorder (ORPHA:145): 1-9 / 10 000.",
        },
        run_id,
        trace_id,
    )

    await CommercialLensAgent().run(msg, ctx)

    assert "1-9 / 10 000" in captured[0]
