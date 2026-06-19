# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinicalLensAgent — clinical precedent + clinical validation axes."""

from __future__ import annotations

from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, run_lens
from agents.interpretation.clinical_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage


class ClinicalLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        parts: list[str] = []

        # Surface published trial-result literature that matched a registry ID
        # (derived summary injected by the workflow node — does not reclassify evidence type)
        published_trial_results = spec.get("published_trial_results") or ""
        if published_trial_results:
            parts.append(
                f"Published trial results (derived from literature evidence matching a "
                f"registry ID or 'phase N trial'):\n{published_trial_results}"
            )

        extra = "\n".join(parts) + "\n" if parts else ""
        return await run_lens(
            msg,
            ctx,
            lens="clinical",
            evidence_types=LENS_EVIDENCE_TYPES["clinical"],
            skill_name="clinical_lens",
            extra_context=extra,
        )
