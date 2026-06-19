# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Transition validator.

validate_transition() is called by the Planner before every edge traversal.
Returns False to block a transition; the Planner should route to the error
terminal when this happens.
"""

from __future__ import annotations

from schemas.state import PipelineState

# All nodes that sit behind the HITL gate
_POST_HITL_NODES: frozenset[str] = frozenset(
    {"hypothesis", "experiment", "competitive", "critic", "reviewer", "report"}
)


def validate_transition(
    state: PipelineState,
    from_node: str,
    to_node: str,
) -> bool:
    """Return True if the transition is permitted; False to block it.

    Three invariants are enforced:
    1. HITL gate — reasoning nodes are blocked until hitl_approved is set.
    2. Step budget — no transition is allowed when the budget is exhausted.
    3. Literature retry cap — literature may not retry itself more than 3 times.
    """
    # 1. HITL gate: reasoning and downstream nodes require explicit approval
    if to_node in _POST_HITL_NODES and not state.get("hitl_approved", False):
        return False

    # 2. Step budget guard
    if state.get("step_budget_remaining", 1) <= 0:
        return False

    # 3. Literature self-loop cap
    if to_node == "literature" and from_node == "literature":
        counters = state.get("loop_counters", {})
        if counters.get("literature_retry", 0) >= 3:
            return False

    return True
