# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from core.routing.providers.base import ModelProvider
from core.routing.router import Router
from harness.skills import load_skill
from schemas.evidence import DataClass


@dataclass
class RunContext:
    """Execution context passed to every agent's act() method.

    Agents call ctx.select_model() to get a (provider, model_name) pair; the
    harness injects agent_name automatically so agents never pass their own name.
    ctx.load_skill() retrieves domain prompts without importing paths.
    """

    run_id: UUID
    trace_id: str
    router: Router
    agent_name: str = ""  # stamped by BaseAgent.run() before act() is called
    # Loop guard is injected by the harness; agents must not touch it directly.
    _loop_guard: object = field(repr=False, default=None)

    def select_model(
        self,
        classification: DataClass,
        task: str = "",
    ) -> tuple[ModelProvider, str]:
        """Return (provider, model_name), injecting agent_name automatically."""
        return self.router.select(classification, task, agent=self.agent_name)

    def load_skill(self, name: str) -> str:
        return load_skill(name)

    @property
    def model(self) -> Router:
        """Legacy alias — prefer ctx.select_model() for new agent code."""
        return self.router
