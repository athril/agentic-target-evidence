# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from schemas.state import PipelineState, _append, replace_last


def test_replace_last_returns_new_when_non_empty() -> None:
    assert replace_last(["a"], ["b", "c"]) == ["b", "c"]


def test_replace_last_keeps_old_when_new_is_empty() -> None:
    assert replace_last(["a", "b"], []) == ["a", "b"]


def test_append_concatenates() -> None:
    assert _append(["a", "b"], ["c"]) == ["a", "b", "c"]


def test_append_empty_new_returns_old() -> None:
    assert _append(["a"], []) == ["a"]


def test_append_empty_old_returns_new() -> None:
    assert _append([], ["x"]) == ["x"]


def test_pipeline_state_has_all_evidence_buckets() -> None:
    buckets = {
        "literature_evidence",
        "patent_evidence",
        "trial_evidence",
        "opentargets_evidence",
        "genetics_evidence",
        "omics_evidence",
        "screened_evidence",
    }
    annotations = PipelineState.__annotations__
    assert buckets <= set(annotations.keys())


def test_pipeline_state_has_hitl_fields() -> None:
    annotations = PipelineState.__annotations__
    assert "hitl_approved" in annotations
    assert "hitl_overrides" in annotations


def test_pipeline_state_has_loop_safety_fields() -> None:
    annotations = PipelineState.__annotations__
    assert "step_budget_remaining" in annotations
    assert "loop_counters" in annotations
