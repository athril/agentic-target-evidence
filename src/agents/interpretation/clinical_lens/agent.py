# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ClinicalLensAgent — clinical precedent + clinical validation axes."""

from __future__ import annotations

from agents.interpretation._lens_base import (
    LENS_EVIDENCE_TYPES,
    build_guard_facts,
    run_lens,
)
from agents.interpretation.clinical_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from services.evidence.clinical_trial_interpret import outcome_terms_in
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

        # The registry trial facts the guards check the prose against.
        guard_facts = build_guard_facts(spec)
        # Outcome terms the data report, so prose naming any other outcome is flagged.
        claim_texts = [str(c.get("claim_text") or "") for c in spec.get("extracted_claims") or []]
        guard_facts = guard_facts.model_copy(
            update={"reported_outcomes": outcome_terms_in(claim_texts)}
        )

        extra = "\n".join(parts) + "\n" if parts else ""
        return await run_lens(
            msg,
            ctx,
            lens="clinical",
            evidence_types=LENS_EVIDENCE_TYPES["clinical"],
            skill_name="clinical_lens",
            extra_context=extra,
            # Post-LLM safety net (repaired once, then annotated): text that misstates a
            # trial's phase or recruitment status — e.g. conflating two distinct-phase
            # trials into "two Phase 3 trials" — or calls a single-trial phase
            # "multiple"/"several" trials.
            guards=(
                guard_facts,
                (
                    "clinical_trial",
                    "outcome_terms",
                    "quantity",
                    "trial_readout",
                    "drug_approval",
                    "modality",
                    "trial_attribution",
                ),
            ),
        )
