# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Patent data-acquisition agent — thin wrapper around the retrieval service."""

from __future__ import annotations

from agents._common import result_msg
from agents.retrieval.patent.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from services.retrieval.patent import fetch_patents


class PatentAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        ev = await fetch_patents(
            gene=spec["target_gene"],
            disease=spec["disease"],
            gene_id=spec.get("gene_id") or "",
            disease_id=spec.get("disease_id") or "",
            run_id=msg.run_id,
            trace_id=msg.trace_id,
            direction=spec.get("direction") or "unspecified",
        )
        return result_msg(msg, ev)
