# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Open Targets data-acquisition agent — thin wrapper around the retrieval service."""

from __future__ import annotations

from agents._common import result_msg
from agents.retrieval.opentargets.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from services.retrieval.opentargets import fetch_opentargets


class OpenTargetsAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        result = await fetch_opentargets(
            gene=spec.get("target_gene", ""),
            disease=spec.get("disease", ""),
            gene_id=spec.get("gene_id") or "",
            disease_id=spec.get("disease_id") or "",
            run_id=msg.run_id,
            trace_id=msg.trace_id,
            direction=spec.get("direction") or "unspecified",
        )
        # gene_id / disease_id are in evidence.extra so opentargets_node can write them to state.
        return result_msg(msg, result.evidences)
