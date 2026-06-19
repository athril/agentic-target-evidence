# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""SafetyLensAgent — toxicity + tissue-specificity axes."""

from __future__ import annotations

from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, run_lens
from agents.interpretation.safety_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from services.evidence.constraint_interpret import (
    interpret_expression_context,
    interpret_gof_tolerance_support,
)
from services.evidence.mouse_phenotype import render_mouse_phenotype


class SafetyLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        parts: list[str] = []
        if spec.get("ot_safety_text"):
            parts.append(f"Safety liabilities (Open Targets): {spec['ot_safety_text']}")
        events = spec.get("ot_safety_liability_events") or []
        if events:
            parts.append(f"Adverse event types: {', '.join(str(e) for e in events[:10])}")
        if spec.get("ot_mouse_text"):
            cleaned_mouse = render_mouse_phenotype(spec["ot_mouse_text"])
            parts.append(f"Mouse KO phenotypes (Open Targets): {cleaned_mouse}")
        if spec.get("safety_structured_text"):
            parts.append(spec["safety_structured_text"])
        if spec.get("faers_text"):
            parts.append(spec["faers_text"])
        if spec.get("disease_tissue_expression_note"):
            parts.append(
                f"Disease-tissue expression grounding: {spec['disease_tissue_expression_note']}"
            )

        # Inject expression-context caveat when bulk TPM may be misleading
        bulk_tpm = spec.get("bulk_tpm")
        hpa_specificity = spec.get("hpa_specificity") or ""
        disease_tissue = spec.get("disease_tissue") or spec.get("tissue") or "disease tissue"
        expr_caveat = interpret_expression_context(bulk_tpm, hpa_specificity, disease_tissue)
        if expr_caveat:
            parts.append(expr_caveat)

        # Inject constraint summary for safety lens (prevents HI misstatement)
        constraint_reading: dict = spec.get("constraint_reading") or {}
        if constraint_reading.get("summary_text"):
            parts.append(
                f"Constraint interpretation (pre-computed — do not re-band):\n"
                f"  {constraint_reading['summary_text']}"
            )

        # GoF mechanism + LoF-tolerance: supports tolerability of inhibition
        mechanism_direction: dict = spec.get("mechanism_direction") or {}
        gof_tolerance_text = interpret_gof_tolerance_support(
            mechanism_direction.get("mechanism"),
            constraint_reading.get("is_lof_tolerant", False),
        )
        if gof_tolerance_text:
            parts.append(gof_tolerance_text)

        extra = "\n".join(parts) + "\n" if parts else ""
        return await run_lens(
            msg,
            ctx,
            lens="safety",
            evidence_types=LENS_EVIDENCE_TYPES["safety"],
            skill_name="safety_lens",
            extra_context=extra,
        )
