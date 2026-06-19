# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for LoopGuard (MP-22)."""

from __future__ import annotations

import pytest

from core.exceptions import LoopLimitExceeded
from harness.contract import AgentContract
from harness.loop_guard import LoopGuard


@pytest.fixture()
def contract() -> AgentContract:
    return AgentContract(
        name="literature",
        consumes={"target_gene"},
        produces={"literature_evidence"},
        max_loops=3,
    )


@pytest.fixture()
def guard() -> LoopGuard:
    return LoopGuard(step_budget=100)


def test_check_increments_loop_counter(guard: LoopGuard, contract: AgentContract) -> None:
    guard.check(contract, "lit_retry")
    assert guard.loop_counters["lit_retry"] == 1


def test_check_increments_step_budget(guard: LoopGuard, contract: AgentContract) -> None:
    guard.check(contract, "lit_retry")
    assert guard.step_budget == 99


def test_check_allows_up_to_max_loops(guard: LoopGuard, contract: AgentContract) -> None:
    for _ in range(contract.max_loops):
        guard.check(contract, "lit_retry")
    assert guard.loop_counters["lit_retry"] == contract.max_loops


def test_check_raises_on_exceeding_max_loops(guard: LoopGuard, contract: AgentContract) -> None:
    for _ in range(contract.max_loops):
        guard.check(contract, "lit_retry")
    with pytest.raises(LoopLimitExceeded, match="lit_retry"):
        guard.check(contract, "lit_retry")


def test_check_raises_on_exhausted_step_budget(contract: AgentContract) -> None:
    guard = LoopGuard(step_budget=1)
    guard.check(contract, "edge_a")
    with pytest.raises(LoopLimitExceeded, match="step budget"):
        guard.check(contract, "edge_b")


def test_independent_edge_keys_tracked_separately(
    guard: LoopGuard, contract: AgentContract
) -> None:
    guard.check(contract, "edge_a")
    guard.check(contract, "edge_b")
    assert guard.loop_counters["edge_a"] == 1
    assert guard.loop_counters["edge_b"] == 1


def test_loop_guard_raises_on_zero_budget(contract: AgentContract) -> None:
    guard = LoopGuard(step_budget=0)
    with pytest.raises(LoopLimitExceeded):
        guard.check(contract, "any_edge")
