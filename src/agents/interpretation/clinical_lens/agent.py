# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinicalLensAgent — clinical precedent + clinical validation axes."""

from __future__ import annotations

from agents.interpretation._lens_base import (
    LENS_EVIDENCE_TYPES,
    apply_guards_to_result,
    build_guard_facts,
    run_lens,
)
from agents.interpretation.clinical_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from services.evidence.clinical_trial_interpret import trial_count_prompt
from services.evidence.disease_class_rules import build_disease_class_note


class ClinicalLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        parts: list[str] = []

        disease_class_note = build_disease_class_note(spec.get("disease_classes") or (), "clinical")
        if disease_class_note:
            parts.append(disease_class_note)

        # Surface published trial-result literature that matched a registry ID
        # (derived summary injected by the workflow node — does not reclassify evidence type)
        published_trial_results = spec.get("published_trial_results") or ""
        if published_trial_results:
            parts.append(
                f"Published trial results (derived from literature evidence matching a "
                f"registry ID or 'phase N trial'):\n{published_trial_results}"
            )

        # Exact per-phase registry counts, so the prose quotes numbers instead of
        # "multiple"/"several" — the same facts the quantity guard checks afterwards.
        guard_facts = build_guard_facts(spec)
        trial_counts = trial_count_prompt(guard_facts.trial_facts)
        if trial_counts:
            parts.append(trial_counts)

        extra = "\n".join(parts) + "\n" if parts else ""
        result = await run_lens(
            msg,
            ctx,
            lens="clinical",
            evidence_types=LENS_EVIDENCE_TYPES["clinical"],
            skill_name="clinical_lens",
            extra_context=extra,
        )

        # Post-LLM safety net: annotate (never silently rewrite) verdict text that
        # misstates a trial's phase or recruitment status — e.g. conflating two
        # distinct-phase trials into "two Phase 3 trials" — or calls a single-trial
        # phase "multiple"/"several" trials. Mirrors the safety lens's guard wiring.
        return apply_guards_to_result(
            result,
            guard_facts,
            ("clinical_trial", "quantity"),
            lens="clinical",
        )
