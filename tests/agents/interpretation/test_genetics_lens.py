# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for GeneticsLensAgent — context injection (source evidence, mechanism direction).

The genetics lens reasons over evidence + skill prompt with deterministic context
injected into the prompt; it no longer post-processes / overrides the LLM verdict.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.interpretation.genetics_lens.agent import GeneticsLensAgent
from agents.interpretation.genetics_lens.contract import CONTRACT
from core.routing.providers.base import CompletionResult
from schemas.evidence import Provenance
from tests.agents.conftest import make_task_msg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prov(trace_id: str = "t") -> Provenance:
    return Provenance(
        agent_name="test",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        trace_id=trace_id,
    )


def _make_completion(content: str) -> CompletionResult:
    return CompletionResult(
        content=content, model_used="test", input_tokens=10, output_tokens=40, latency_ms=50.0
    )


def _valid_verdict(ov: str = "support", confidence: float = 0.8) -> str:
    return json.dumps(
        {
            "overall_verdict": ov,
            "confidence": confidence,
            "rationale": "Test rationale.",
            "narrative": "Test narrative.",
            "axes": [
                {
                    "axis": "causality",
                    "verdict": True,
                    "confidence": confidence,
                    "rationale": "r",
                    "supporting_claim_ids": [],
                },
                {
                    "axis": "genetic_validity",
                    "verdict": True,
                    "confidence": confidence,
                    "rationale": "r",
                    "supporting_claim_ids": [],
                },
            ],
        }
    )


@pytest.fixture()
def lens_ctx(run_id, trace_id):
    provider = MagicMock()
    router = MagicMock()
    router.select.return_value = (provider, "mock-model")
    from harness.context import RunContext

    return RunContext(run_id=run_id, trace_id=trace_id, router=router), provider


def _base_spec(run_id, trace_id, *, ov: str = "support", **overrides) -> dict:
    return {
        "target_gene": "TRPC6",
        "disease": "focal segmental glomerulosclerosis",
        "direction": "inhibit",
        "gene_id": "ENSG00000144935",
        "disease_id": "EFO_0004236",
        "extracted_claims": [],
        "source_evidence_text": "",
        "floor_signals": {},
        **overrides,
    }


# ---------------------------------------------------------------------------
# source_evidence_text injected into prompt
# ---------------------------------------------------------------------------


async def test_genetics_lens_source_evidence_text_in_prompt(run_id, trace_id, lens_ctx):
    """Source evidence text must appear in the LLM prompt even with 0 claims."""
    ctx, provider = lens_ctx
    captured: list[str] = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict())

    provider.complete = mock_complete

    src_text = "Source genetics/constraint evidence:\n[genetics|opentargets] OT genetic_score=0.956"
    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, source_evidence_text=src_text),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        await GeneticsLensAgent().run(msg, ctx)

    assert captured, "LLM must have been called"
    assert "OT genetic_score=0.956" in captured[0]


async def test_genetics_lens_no_source_evidence_text_still_works(run_id, trace_id, lens_ctx):
    """Absence of source_evidence_text must not crash the lens."""
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_valid_verdict()))

    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, source_evidence_text="", floor_signals={}),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        result = await GeneticsLensAgent().run(msg, ctx)

    verdicts = result.payload.get("lens_verdicts", [])
    assert len(verdicts) == 1


async def test_genetics_lens_passes_through_llm_verdict(run_id, trace_id, lens_ctx):
    """The lens returns the LLM's overall_verdict/confidence unmodified — only the
    causality axis + validation_flags are touched, and only when the Mendelian
    floor (WS4) activates."""
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(
        return_value=_make_completion(_valid_verdict(ov="neutral", confidence=0.3))
    )

    # Floor signals below the WS4 Mendelian-grade bar (no gold-star P/LP, no
    # ClinGen/graph corroboration) must NOT escalate the verdict or add flags.
    floor = {"max_genetic_score": 0.956, "plp_count": 64, "high_star_plp": 0}
    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=floor),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        result = await GeneticsLensAgent().run(msg, ctx)

    verdicts = result.payload.get("lens_verdicts", [])
    assert verdicts
    v = verdicts[0]
    assert v.get("overall_verdict") == "neutral", "Verdict must reflect the LLM output verbatim"
    assert "CONFLICT" not in v.get("rationale", ""), "No HITL conflict markers should be added"
    assert v.get("validation_flags", []) == [], "No validation flags below the Mendelian-grade bar"


# ---------------------------------------------------------------------------
# Mechanism-direction injection into the prompt
# ---------------------------------------------------------------------------


async def test_direction_injected_into_prompt(run_id, trace_id, lens_ctx):
    """Mechanism direction from floor_signals must appear in the LLM prompt."""
    ctx, provider = lens_ctx
    captured: list[str] = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict())

    provider.complete = mock_complete

    floor = {
        "max_genetic_score": 0.956,
        "plp_count": 10,
        "high_star_plp": 5,
        "segregation_signal": False,
        "mechanism_direction": {
            "direction": "inhibit",
            "mechanism": "gof",
            "confidence": 0.80,
            "rationale": "Dominant missense P/LP in LoF-tolerant gene.",
            "supporting_variant_ids": [],
        },
        "constraint_reading": {},
    }
    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=floor),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        await GeneticsLensAgent().run(msg, ctx)

    assert captured
    prompt = captured[0]
    assert "inhibit" in prompt.lower() or "INHIBIT" in prompt
    assert "gain-of-function" in prompt.lower() or "gof" in prompt.lower()


# ---------------------------------------------------------------------------
# WS2: SPOKE graph association injected onto the causality axis
# ---------------------------------------------------------------------------


async def test_graph_association_injected_into_prompt(run_id, trace_id, lens_ctx):
    """graph_association from floor_signals must appear in the LLM prompt, routed
    as a causality-axis input (WS2 acceptance)."""
    ctx, provider = lens_ctx
    captured: list[str] = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict())

    provider.complete = mock_complete

    floor = {
        "max_genetic_score": 0.0,
        "plp_count": 0,
        "high_star_plp": 0,
        "graph_association": {
            "disease_name": "focal segmental glomerulosclerosis",
            "edge_sources": ["GWAS", "ClinVar"],
            "gwas_pvalue": 1e-12,
            "diseases_score": 0.9,
            "corroborates_causality": True,
        },
    }
    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=floor),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        await GeneticsLensAgent().run(msg, ctx)

    assert captured
    prompt = captured[0]
    assert "Causality axis input" in prompt
    assert "SPOKE" in prompt
    assert "GWAS" in prompt and "ClinVar" in prompt


# ---------------------------------------------------------------------------
# WS3: HPO breadth band + inheritance mode injected into the prompt
# ---------------------------------------------------------------------------


async def test_hpo_breadth_injected_into_prompt(run_id, trace_id, lens_ctx):
    """inheritance_mode + hpo_phenotype_count/hpo_specificity_band from floor_signals
    must appear in the LLM prompt (WS3 acceptance: HPO breadth band in the prompt)."""
    ctx, provider = lens_ctx
    captured: list[str] = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict())

    provider.complete = mock_complete

    floor = {
        "max_genetic_score": 0.0,
        "plp_count": 0,
        "high_star_plp": 0,
        "inheritance_mode": "Autosomal dominant",
        "hpo_phenotype_count": 4,
        "hpo_specificity_band": "focal",
    }
    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=floor),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        await GeneticsLensAgent().run(msg, ctx)

    assert captured
    prompt = captured[0]
    assert "Ontology constraints" in prompt
    assert "Autosomal dominant" in prompt
    assert "4 phenotype" in prompt
    assert "focal" in prompt


async def test_hpo_breadth_absent_when_no_ontology_signals(run_id, trace_id, lens_ctx):
    """No inheritance_mode/hpo_phenotype_count must not add an ontology block or crash."""
    ctx, provider = lens_ctx
    captured: list[str] = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict())

    provider.complete = mock_complete

    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals={}),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        await GeneticsLensAgent().run(msg, ctx)

    assert captured
    assert "Ontology constraints" not in captured[0]


# ---------------------------------------------------------------------------
# WS4: Mendelian causality floor (post-LLM enforcement)
# ---------------------------------------------------------------------------


_MENDELIAN_FLOOR = {
    "max_genetic_score": 0.0,
    "plp_count": 3,
    "high_star_plp": 2,
    "clingen_classification": "Definitive",
}


async def test_mendelian_floor_clamps_unfavourable_causality_axis(run_id, trace_id, lens_ctx):
    """An LLM verdict marking causality unfavourable must be clamped to
    favourable with floor confidence when the gene-disease pair is Mendelian-grade."""
    ctx, provider = lens_ctx
    bad_verdict = _valid_verdict(ov="oppose", confidence=0.4)
    import json as _json

    data = _json.loads(bad_verdict)
    data["axes"][0]["verdict"] = False
    data["axes"][0]["confidence"] = 0.2
    data["axes"][0]["rationale"] = "No GWAS support undermines causality."
    provider.complete = AsyncMock(return_value=_make_completion(_json.dumps(data)))

    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=_MENDELIAN_FLOOR),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        result = await GeneticsLensAgent().run(msg, ctx)

    v = result.payload["lens_verdicts"][0]
    causality = next(ax for ax in v["axes"] if ax["axis"] == "causality")
    assert causality["verdict"] is True
    assert causality["confidence"] >= 0.60
    assert "MENDELIAN FLOOR GUARD" in causality["rationale"]
    assert v["overall_verdict"] == "oppose", "Top-level LLM verdict is not overridden"


async def test_mendelian_floor_logs_validation_flag(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_valid_verdict()))

    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=_MENDELIAN_FLOOR),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        result = await GeneticsLensAgent().run(msg, ctx)

    v = result.payload["lens_verdicts"][0]
    flags = v.get("validation_flags", [])
    assert len(flags) == 1
    assert flags[0]["rule_id"] == "mendelian_causality_floor"


async def test_mendelian_context_injected_into_prompt(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    captured: list[str] = []

    async def mock_complete(req):
        captured.append(req.messages[0]["content"])
        return _make_completion(_valid_verdict())

    provider.complete = mock_complete

    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=_MENDELIAN_FLOOR),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        await GeneticsLensAgent().run(msg, ctx)

    assert captured
    assert "Mendelian context" in captured[0]
    assert "EXPECTED" in captured[0]


async def test_mendelian_floor_inactive_below_threshold(run_id, trace_id, lens_ctx):
    """No gold-star P/LP, no ClinGen Definitive/Strong, no strong graph association
    → no floor activation, no validation flags, no causality axis injected."""
    ctx, provider = lens_ctx
    provider.complete = AsyncMock(return_value=_make_completion(_valid_verdict()))

    floor = {"max_genetic_score": 0.5, "plp_count": 1, "high_star_plp": 0}
    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=floor),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        result = await GeneticsLensAgent().run(msg, ctx)

    v = result.payload["lens_verdicts"][0]
    assert v.get("validation_flags", []) == []


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_genetics_lens_contract_consumes_source_evidence_text():
    assert "source_evidence_text" in CONTRACT.consumes


def test_genetics_lens_contract_consumes_floor_signals():
    assert "floor_signals" in CONTRACT.consumes


# ---------------------------------------------------------------------------
# Constraint-interpretation guard (post-LLM enforcement)
#
# Regression coverage for the TRPC6xFSGS report bug where the LLM wrote
# "strong missense constraint" / "high mis_z value" for mis_z=1.70 and
# "LOEUF < 0.35 indicating haploinsufficiency is not an issue" for LOEUF=0.759 —
# both directly contradicting the pre-computed `Constraint interpretation` block
# already in the prompt. apply_constraint_guards() existed but was never wired
# into the lens's post-processing; this section locks in the wiring.
# ---------------------------------------------------------------------------


def _trpc6_constraint_reading() -> dict:
    from services.evidence.constraint_interpret import interpret_constraint

    return interpret_constraint(
        "TRPC6", loeuf=0.759, pli=0.00, mis_z=1.70, moeuf=0.928
    ).model_dump()


async def test_constraint_guard_flags_strong_missense_constraint_hallucination(
    run_id, trace_id, lens_ctx
):
    ctx, provider = lens_ctx
    data = json.loads(_valid_verdict())
    data["narrative"] = (
        "Strong missense constraint supports gain-of-function causality; "
        "the high mis_z value of 1.70 suggests missense variants are tolerated."
    )
    provider.complete = AsyncMock(return_value=_make_completion(json.dumps(data)))

    floor = {"max_genetic_score": 0.0, "plp_count": 0, "high_star_plp": 0}
    floor["constraint_reading"] = _trpc6_constraint_reading()
    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=floor),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        result = await GeneticsLensAgent().run(msg, ctx)

    v = result.payload["lens_verdicts"][0]
    assert "CONSTRAINT GUARD" in v["narrative"]
    assert any(f["rule_id"] == "constraint_interpretation_guard" for f in v["validation_flags"])


async def test_constraint_guard_flags_haploinsufficiency_axis_rationale(run_id, trace_id, lens_ctx):
    ctx, provider = lens_ctx
    data = json.loads(_valid_verdict())
    data["axes"][1]["rationale"] = (
        "LOEUF < 0.35 indicating haploinsufficiency is not an issue and mis_z > 1.70 "
        "suggesting missense variants are tolerated."
    )
    provider.complete = AsyncMock(return_value=_make_completion(json.dumps(data)))

    floor = {
        "max_genetic_score": 0.0,
        "plp_count": 0,
        "high_star_plp": 0,
        "constraint_reading": _trpc6_constraint_reading(),
    }
    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=floor),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        result = await GeneticsLensAgent().run(msg, ctx)

    v = result.payload["lens_verdicts"][0]
    genetic_validity = next(ax for ax in v["axes"] if ax["axis"] == "genetic_validity")
    assert "CONSTRAINT GUARD" in genetic_validity["rationale"]


async def test_constraint_guard_inactive_on_correct_text(run_id, trace_id, lens_ctx):
    """Correctly-worded narrative/rationale must not be annotated or flagged."""
    ctx, provider = lens_ctx
    data = json.loads(_valid_verdict())
    data["narrative"] = (
        "TRPC6 is LoF-tolerant (LOEUF=0.759) with no meaningful missense constraint "
        "(mis_z=1.70), consistent with a gain-of-function Mendelian mechanism."
    )
    provider.complete = AsyncMock(return_value=_make_completion(json.dumps(data)))

    floor = {
        "max_genetic_score": 0.0,
        "plp_count": 0,
        "high_star_plp": 0,
        "constraint_reading": _trpc6_constraint_reading(),
    }
    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals=floor),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        result = await GeneticsLensAgent().run(msg, ctx)

    v = result.payload["lens_verdicts"][0]
    assert "CONSTRAINT GUARD" not in v["narrative"]
    assert v.get("validation_flags", []) == []


async def test_constraint_guard_inactive_when_no_constraint_reading(run_id, trace_id, lens_ctx):
    """No constraint_reading in floor_signals (e.g. constraint evidence absent) must
    not crash and must not add flags."""
    ctx, provider = lens_ctx
    data = json.loads(_valid_verdict())
    data["narrative"] = "Strong missense constraint claim with no backing data."
    provider.complete = AsyncMock(return_value=_make_completion(json.dumps(data)))

    msg = make_task_msg(
        "genetics_lens",
        _base_spec(run_id, trace_id, floor_signals={}),
        run_id,
        trace_id,
    )

    with patch(
        "agents.interpretation.genetics_lens.agent.get_disease_descendants",
        new=AsyncMock(return_value=MagicMock(therapeutic_areas=set())),
    ):
        result = await GeneticsLensAgent().run(msg, ctx)

    v = result.payload["lens_verdicts"][0]
    assert v.get("validation_flags", []) == []
