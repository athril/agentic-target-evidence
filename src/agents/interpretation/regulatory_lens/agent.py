# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""RegulatoryLensAgent — approval precedent + label safety axes (Phase 2)."""

from __future__ import annotations

from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, run_lens
from agents.interpretation.regulatory_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage


class RegulatoryLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        has_label = bool(spec.get("fda_label_text"))
        parts: list[str] = []
        if has_label:
            parts.append(spec["fda_label_text"])
        extra = "\n".join(parts) + "\n" if parts else ""
        target_gene = spec.get("target_gene", "this target")
        empty_note = (
            f"No FDA drug-label records were retrieved for {target_gene} in this run. "
            "This lens reasons only over FDA label data; an absence of records most often "
            "indicates that no approved drug targets this gene (a first-in-class signal) but "
            "cannot by itself confirm it. Disease-space regulatory context — approved therapies "
            "of other mechanisms, accepted endpoints, established trial pathways for the "
            "indication — is outside this lens's scope and is assessed by the clinical and "
            "commercial lenses. No label-level safety liabilities (black-box warnings, "
            "contraindications) can be inferred where no in-class approved drug exists."
        )
        return await run_lens(
            msg,
            ctx,
            lens="regulatory",
            evidence_types=LENS_EVIDENCE_TYPES["regulatory"],
            skill_name="regulatory_lens",
            extra_context=extra,
            guard_empty=True,
            has_fallback_evidence=has_label,
            empty_evidence_note=empty_note,
        )
