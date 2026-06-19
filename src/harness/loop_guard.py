# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field

from core.exceptions import LoopLimitExceeded
from harness.contract import AgentContract


@dataclass
class LoopGuard:
    """Tracks per-edge loop counts and the global step budget.

    check() must be called before every agent invocation on a loop-capable
    edge.  Raises LoopLimitExceeded if either the per-edge cap or the total
    step budget is exceeded — the caller should abort the run rather than retry.
    """

    step_budget: int
    loop_counters: dict[str, int] = field(default_factory=dict)

    def check(self, contract: AgentContract, edge_key: str) -> None:
        """Increment counters and enforce limits.

        Checks the per-edge counter first (LoopLimitExceeded with a descriptive
        message), then the global budget.  Both checks raise the same exception
        type so the graph can catch either with one clause.
        """
        count = self.loop_counters.get(edge_key, 0) + 1
        self.loop_counters[edge_key] = count

        if count > contract.max_loops:
            raise LoopLimitExceeded(
                f"Edge {edge_key!r} exceeded max_loops={contract.max_loops} "
                f"for agent {contract.name!r} (reached {count})"
            )

        self.step_budget -= 1
        if self.step_budget < 0:
            raise LoopLimitExceeded(
                f"Global step budget exhausted on edge {edge_key!r} (agent {contract.name!r})"
            )
