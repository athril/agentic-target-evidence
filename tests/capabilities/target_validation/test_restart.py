# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for restart-from-node functionality (resume_pipeline / restart_router)."""

from __future__ import annotations

import uuid

import pytest

from capabilities.target_validation.workflow import (
    _REQUIRED_UPSTREAM,
    CLEAR_FROM_NODE,
    NODE_TO_JUMP_TARGET,
)

# ---------------------------------------------------------------------------
# Structural consistency checks
# ---------------------------------------------------------------------------


def test_all_jump_targets_have_clear_spec():
    """Every canonical jump target in NODE_TO_JUMP_TARGET must have a CLEAR_FROM_NODE entry."""
    canonical_targets = set(NODE_TO_JUMP_TARGET.values())
    missing = canonical_targets - set(CLEAR_FROM_NODE)
    assert not missing, f"Missing CLEAR_FROM_NODE entries for: {missing}"


def test_all_jump_targets_have_required_upstream():
    """Every canonical jump target must appear in _REQUIRED_UPSTREAM."""
    canonical_targets = set(NODE_TO_JUMP_TARGET.values())
    missing = canonical_targets - set(_REQUIRED_UPSTREAM)
    assert not missing, f"Missing _REQUIRED_UPSTREAM entries for: {missing}"


def test_node_to_jump_target_aliases_map_to_known_targets():
    """All alias values in NODE_TO_JUMP_TARGET must be keys in CLEAR_FROM_NODE."""
    bad = {k: v for k, v in NODE_TO_JUMP_TARGET.items() if v not in CLEAR_FROM_NODE}
    assert not bad, f"Alias(es) point to unknown jump targets: {bad}"


@pytest.mark.parametrize("node", list(NODE_TO_JUMP_TARGET))
def test_clear_from_node_values_are_correct_types(node: str):
    """CLEAR_FROM_NODE values must be the correct zero types (None, [], '', 0, False, {})."""
    target = NODE_TO_JUMP_TARGET[node]
    for field, value in CLEAR_FROM_NODE[target].items():
        assert value in (None, [], "", 0, False, {}), (
            f"CLEAR_FROM_NODE[{target!r}][{field!r}] = {value!r} is not a recognised zero value"
        )


# ---------------------------------------------------------------------------
# CLEAR_FROM_NODE subset ordering
# The later the restart point in the pipeline, the fewer fields should be cleared.
# ---------------------------------------------------------------------------


def test_report_clears_subset_of_gap_detection():
    """Restarting from report clears fewer fields than restarting from gap_detection."""
    report_fields = set(CLEAR_FROM_NODE["report"])
    gap_fields = set(CLEAR_FROM_NODE["gap_detection"])
    assert report_fields < gap_fields, (
        "report CLEAR_FROM_NODE should be a strict subset of gap_detection's"
    )


def test_gap_detection_clears_subset_of_experiment():
    assert set(CLEAR_FROM_NODE["gap_detection"]) < set(CLEAR_FROM_NODE["experiment"])


def test_experiment_clears_subset_of_hitl_gate():
    assert set(CLEAR_FROM_NODE["experiment"]) < set(CLEAR_FROM_NODE["hitl_gate"])


def test_hitl_gate_clears_subset_of_claim_extraction():
    assert set(CLEAR_FROM_NODE["hitl_gate"]) < set(CLEAR_FROM_NODE["claim_extraction"])


def test_claim_extraction_clears_subset_of_screening_first():
    assert set(CLEAR_FROM_NODE["claim_extraction"]) < set(CLEAR_FROM_NODE["screening_first"])


# ---------------------------------------------------------------------------
# resume_pipeline unit test using MemorySaver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_pipeline_report_only(tmp_path, monkeypatch):
    """resume_pipeline with from_node='report' copies lens_verdicts and clears report_uri."""
    from langgraph.checkpoint.memory import MemorySaver

    # Minimal mock state that looks like a completed run
    old_run_id = uuid.uuid4()

    # We'll use a real MemorySaver graph to store the checkpoint, then call resume_pipeline.
    # Build a trivial graph with just a passthrough node for this test.
    from langgraph.graph import END, START, StateGraph

    from schemas.state import PipelineState

    def passthrough(state: PipelineState) -> dict:
        return {}

    builder = StateGraph(PipelineState)
    builder.add_node("passthrough", passthrough)
    builder.add_edge(START, "passthrough")
    builder.add_edge("passthrough", END)

    saver = MemorySaver()
    g = builder.compile(checkpointer=saver)

    old_thread_id = str(uuid.uuid4())
    old_config = {"configurable": {"thread_id": old_thread_id}}

    # Seed a checkpoint that looks like a completed pipeline run
    from schemas.verdicts import LensVerdict

    lv = LensVerdict(
        run_id=old_run_id,
        trace_id=str(old_run_id),
        target_gene="BRCA1",
        disease="breast cancer",
        lens="biology",
        overall_verdict="support",
        confidence=0.9,
        rationale="test",
        narrative="test",
    )
    seed_state = {
        "run_id": old_run_id,
        "target_gene": "BRCA1",
        "disease": "breast cancer",
        "direction": "inhibit",
        "population": None,
        "tissue": None,
        "gene_id": "ENSG00000012048",
        "disease_id": "EFO_0001360",
        "model_fingerprint": "qwen2.5:7b",
        "force_refresh": False,
        "literature_evidence": [],
        "patent_evidence": [],
        "trial_evidence": [],
        "opentargets_evidence": [],
        "genetics_evidence": [],
        "omics_evidence": [],
        "functional_evidence": [],
        "druggability_evidence": [],
        "screened_evidence": [],
        "extracted_claims": [],
        "lens_verdicts": [lv],
        "agreement_map": {"biology": "support"},
        "experiment_results": [{"score": 0.8}],
        "critiques": [{"text": "ok"}],
        "review_gaps": [{"gap": "none"}],
        "report_uri": "file:///results/report/BRCA1_report.md",
        "full_report_uri": None,
        "replan_decision": "proceed",
        "gap_guidance": "",
        "replan_count": 0,
        "step_budget_remaining": 180,
        "loop_counters": {"lens": 1},
        "hitl_approved": True,
        "hitl_overrides": {},
        "failed_lenses": [],
        "failed_sources": [],
        "rerun_count": 0,
        "messages": [],
    }
    await g.aupdate_state(old_config, seed_state, as_node="passthrough")

    # Now verify the checkpoint exists
    snapshot = await g.aget_state(old_config)
    assert snapshot is not None
    assert snapshot.values["report_uri"] == "file:///results/report/BRCA1_report.md"
    assert snapshot.values["lens_verdicts"] == [lv]

    # Call resume_pipeline.  We patch run_pipeline to just capture what initial_state it receives.
    captured: dict = {}

    async def fake_run_pipeline(graph, initial_state, config):
        captured.update(initial_state)

    monkeypatch.setattr(
        "capabilities.target_validation.workflow.run_pipeline",
        fake_run_pipeline,
    )

    from capabilities.target_validation.workflow import resume_pipeline

    new_thread_id = str(uuid.uuid4())
    new_config = {"configurable": {"thread_id": new_thread_id}}
    await resume_pipeline(g, old_thread_id=old_thread_id, from_node="report", config=new_config)

    # report_uri must be cleared
    assert captured["report_uri"] is None
    # lens_verdicts must be preserved (report restart doesn't clear them)
    assert captured["lens_verdicts"] == [lv]
    # experiment_results preserved
    assert captured["experiment_results"] == [{"score": 0.8}]
    # rerun_count incremented
    assert captured["rerun_count"] == 1
    # loop_counters reset
    assert captured["loop_counters"] == {}
    # step_budget reset
    assert captured["step_budget_remaining"] == 200


@pytest.mark.asyncio
async def test_resume_pipeline_invalid_thread_id():
    """resume_pipeline raises ValueError for an unknown thread_id."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    from schemas.state import PipelineState

    def passthrough(state: PipelineState) -> dict:
        return {}

    builder = StateGraph(PipelineState)
    builder.add_node("passthrough", passthrough)
    builder.add_edge(START, "passthrough")
    builder.add_edge("passthrough", END)
    g = builder.compile(checkpointer=MemorySaver())

    from capabilities.target_validation.workflow import resume_pipeline

    with pytest.raises(ValueError, match="No checkpoint found"):
        await resume_pipeline(
            g,
            old_thread_id="00000000-0000-0000-0000-000000000000",
            from_node="report",
            config={"configurable": {"thread_id": str(uuid.uuid4())}},
        )


@pytest.mark.asyncio
async def test_resume_pipeline_invalid_from_node():
    """resume_pipeline raises ValueError for an unknown from_node."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    from schemas.state import PipelineState

    builder = StateGraph(PipelineState)
    builder.add_node("p", lambda s: {})
    builder.add_edge(START, "p")
    builder.add_edge("p", END)
    g = builder.compile(checkpointer=MemorySaver())

    from capabilities.target_validation.workflow import resume_pipeline

    with pytest.raises(ValueError, match="Unknown --from-node"):
        await resume_pipeline(
            g,
            old_thread_id="any",
            from_node="foobar",
            config={"configurable": {"thread_id": str(uuid.uuid4())}},
        )
