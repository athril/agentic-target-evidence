# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CommercialLensAgent — IP landscape + competitive opportunity axes.

Reads patent claims from extracted_claims plus raw patent/trial counts
passed by the graph node. Replaces CompetitiveAgent in the reasoning graph.
"""

from __future__ import annotations

from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, run_lens
from agents.interpretation.commercial_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from services.evidence.constraint_interpret import interpret_patent_landscape


class CommercialLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        patent_count = spec.get("patent_count", 0)
        trial_count = spec.get("trial_count", 0)
        parts = [
            f"Patent count in retrieval: {patent_count}",
            f"Trial count in retrieval: {trial_count}",
        ]
        # Inject patent-landscape framing to prevent self-contradictory IP claims
        parts.append(interpret_patent_landscape(patent_count))

        if spec.get("ot_known_drugs_text"):
            parts.append(f"Known drugs (Open Targets): {spec['ot_known_drugs_text']}")
        approved = spec.get("ot_known_drugs_approved_count", 0)
        phase3 = spec.get("ot_known_drugs_phase3_count", 0)
        if approved or phase3:
            parts.append(f"Approved drugs targeting this gene: {approved}; Phase 3: {phase3}")
        if spec.get("fda_label_text"):
            parts.append(spec["fda_label_text"])
        if spec.get("orphanet_prevalence_text"):
            parts.append(spec["orphanet_prevalence_text"])
        extra = "\n".join(parts) + "\n"
        return await run_lens(
            msg,
            ctx,
            lens="commercial",
            evidence_types=LENS_EVIDENCE_TYPES["commercial"],
            skill_name="commercial_lens",
            extra_context=extra,
        )
