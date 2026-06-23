# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GapDetectionAgent — bounded replanning advisory.

Runs after the reviewer and reconciler complete. Reads review_gaps and the
agreement_map to decide whether a second reasoning pass is warranted.

Produces:
  replan_decision — "proceed" | "replan"
  gap_guidance    — one-sentence explanation of the decision

The agent has max_loops=2 so it can be called once per run (initial) and
once on a second pass if replanning was triggered. On the second pass the
graph node guarantees replan_count >= 1, so the skill instructs the LLM not
to replan again.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from agents.synthesis.gap_detection.contract import CONTRACT
from core.json_utils import strip_json_fence
from core.routing.providers.base import CompletionRequest
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import DataClass
from schemas.messages import AgentMessage


def _parse_gap_decision(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(strip_json_fence(raw))
        if isinstance(data, dict) and data.get("replan_decision") in ("proceed", "replan"):
            return {
                "replan_decision": data["replan_decision"],
                "guidance": str(data.get("guidance", "")),
            }
    except (json.JSONDecodeError, ValueError):
        pass
    return {
        "replan_decision": "proceed",
        "guidance": "Could not parse gap assessment — proceeding.",
    }


class GapDetectionAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        target_gene = spec.get("target_gene", "unknown")
        disease = spec.get("disease", "unknown")
        replan_count = spec.get("replan_count", 0)
        review_gaps = spec.get("review_gaps") or []
        agreement_map = spec.get("agreement_map") or {}

        skill_text = ctx.load_skill("gap_detection")
        provider, _model = ctx.select_model(DataClass.NON_SENSITIVE, "gap_detection")

        consensus = agreement_map.get("consensus_verdict", "unknown")
        conflict_count = len(agreement_map.get("conflicts") or [])

        user_content = (
            f"Target gene: {target_gene}\n"
            f"Disease: {disease}\n"
            f"This is evaluation pass #{replan_count + 1}. "
            f"{'Do NOT replan — max replans reached.' if replan_count >= 1 else ''}\n\n"
            f"Agreement map: consensus={consensus}, conflicts={conflict_count}\n\n"
            f"Review gaps:\n{json.dumps(review_gaps, indent=2)}"
        )

        completion = await provider.complete(
            CompletionRequest(
                messages=[{"role": "user", "content": user_content}],
                system=skill_text,
                classification=DataClass.NON_SENSITIVE,
                task="gap_detection",
                model_override=_model,
            )
        )

        decision = _parse_gap_decision(completion.content)

        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={
                "replan_decision": decision["replan_decision"],
                "gap_guidance": decision["guidance"],
            },
            trace_id=msg.trace_id,
        )
