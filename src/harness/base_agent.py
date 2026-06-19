# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod

from core.telemetry.langfuse import span
from harness.context import RunContext
from harness.contract import AgentContract, validate_inbound, validate_outbound
from harness.loop_guard import LoopGuard
from schemas.evidence import Evidence
from schemas.messages import AgentMessage

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Harness base class for all agents.

    Subclasses declare a class-level ``contract`` and implement ``act()``.
    The ``run()`` method enforces the full harness guarantee in order:
      1. validate_inbound  — reject undeclared task_spec keys
      2. telemetry span    — wraps act() with OTel + Langfuse tracing
      3. loop_guard.check  — enforce per-edge and step-budget limits
      4. act()             — the agent's domain logic
      5. validate_outbound — reject undeclared payload keys
    """

    contract: AgentContract  # must be set as a class attribute on every subclass

    async def run(
        self,
        msg: AgentMessage,
        ctx: RunContext,
        *,
        loop_guard: LoopGuard | None = None,
        edge_key: str | None = None,
    ) -> AgentMessage:
        validate_inbound(msg, self.contract)

        _guard = loop_guard or getattr(ctx, "_loop_guard", None)
        _edge = edge_key or self.contract.name

        ctx.agent_name = self.contract.name
        input_str = json.dumps(msg.task_spec, default=str) if msg.task_spec else "{}"
        t0 = time.monotonic()
        logger.info("[agent] %s starting (run_id=%s)", self.contract.name, msg.run_id)
        async with span(
            self.contract.name, trace_id=msg.trace_id, input_data=input_str
        ) as current_span:
            if _guard is not None:
                _guard.check(self.contract, _edge)
            result = await self.act(msg, ctx)
            n_items = (
                len(result.payload)
                if isinstance(result.payload, list)
                else (1 if result.payload else 0)
            )
            if (
                isinstance(result.payload, list)
                and result.payload
                and isinstance(result.payload[0], Evidence)
            ):
                summary = [
                    {"source": e.source, "type": e.evidence_type.value, "scope": e.scope}
                    for e in result.payload[:50]
                ]
                current_span.set_attribute("gen_ai.completion", json.dumps(summary))
                if n_items > 50:
                    current_span.set_attribute("gen_ai.completion.truncated", str(n_items - 50))
            else:
                current_span.set_attribute("gen_ai.completion", f"{n_items} item(s) produced")

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "[agent] %s done in %.0f ms — %d payload item(s)",
            self.contract.name,
            elapsed_ms,
            n_items,
        )

        validate_outbound(result, self.contract)
        return result

    @abstractmethod
    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        """Domain logic — implement in every concrete agent subclass."""
