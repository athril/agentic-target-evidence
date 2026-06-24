# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CommercialLensAgent — IP landscape + competitive opportunity axes.

Reads patent claims from extracted_claims plus raw patent/trial counts
passed by the graph node. Replaces CompetitiveAgent in the reasoning graph.
"""

from __future__ import annotations

import uuid

from agents.interpretation._lens_base import LENS_EVIDENCE_TYPES, run_lens
from agents.interpretation.commercial_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from schemas.verdicts import LensVerdict, ValidationFlag
from services.evidence.commercial_interpret import (
    apply_commercial_guards,
    interpret_competitive_landscape,
)
from services.evidence.constraint_interpret import interpret_patent_landscape
from services.evidence.disease_class_rules import build_disease_class_note


def _apply_commercial_guard(
    result: AgentMessage,
    *,
    known_drugs_count: int,
    approved_count: int,
    indication_approved_drug_count: int = 0,
    indication_active_trial_count: int = 0,
) -> AgentMessage:
    """Post-LLM safety net: annotate commercial overstatements (blanket no-drugs
    claims, indication-level "underserved", market-size-unknown) on the parsed
    verdict. Mirrors the constraint/clinical guards — annotate, never silently
    rewrite — and records a ValidationFlag for Langfuse/HITL audit on activation.
    """
    if not isinstance(result.payload, dict):
        return result
    verdicts = result.payload.get("lens_verdicts") or []
    if not verdicts:
        return result

    verdict = LensVerdict.model_validate(verdicts[0])

    def _guard(text: str) -> str:
        return apply_commercial_guards(
            text,
            known_drugs_count=known_drugs_count,
            approved_count=approved_count,
            indication_approved_drug_count=indication_approved_drug_count,
            indication_active_trial_count=indication_active_trial_count,
        )

    guarded_rationale = _guard(verdict.rationale)
    guarded_narrative = _guard(verdict.narrative)
    axes = [ax.model_copy(update={"rationale": _guard(ax.rationale)}) for ax in verdict.axes]

    fired = any(
        "COMMERCIAL GUARD" in t
        for t in (guarded_rationale, guarded_narrative, *(ax.rationale for ax in axes))
    )
    if not fired:
        return result

    flag = ValidationFlag(
        lens="commercial",
        severity="medium",
        rule_id="commercial_overstatement_guard",
        claim_excerpt="",
        message="Commercial guard activated: verdict text asserted a blanket absence of "
        "drugs, called the indication 'underserved' on target-level evidence, or declared "
        "market size 'unknown' from Orphanet silence alone; annotated rather than silently "
        "rewritten.",
    )

    updated = verdict.model_copy(
        update={
            "rationale": guarded_rationale,
            "narrative": guarded_narrative,
            "axes": axes,
            "validation_flags": [*verdict.validation_flags, flag],
        }
    )

    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=result.run_id,
        from_agent=result.from_agent,
        to_agent=result.to_agent,
        intent=result.intent,
        payload={"lens_verdicts": [updated.model_dump(mode="json")]},
        trace_id=result.trace_id,
    )


class CommercialLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        patent_count = spec.get("patent_count", 0)
        trial_count = spec.get("trial_count", 0)
        approved = spec.get("ot_known_drugs_approved_count", 0)
        phase3 = spec.get("ot_known_drugs_phase3_count", 0)
        known_drugs_count = spec.get("ot_known_drugs_count", 0)
        indication_approved = spec.get("indication_approved_drug_count", 0)
        indication_active_trials = spec.get("indication_active_trial_count", 0)
        indication_phase3_trials = spec.get("indication_phase3_trial_count", 0)
        indication_total_trials = spec.get("indication_total_trial_count", 0)
        parts = [
            f"Patent count in retrieval: {patent_count}",
            f"Trial count in retrieval: {trial_count}",
        ]
        disease_class_note = build_disease_class_note(
            spec.get("disease_classes") or (), "commercial"
        )
        if disease_class_note:
            parts.append(disease_class_note)
        # Inject patent-landscape framing to prevent self-contradictory IP claims
        parts.append(interpret_patent_landscape(patent_count))
        # Inject competitive-landscape framing: approved/clinical/preclinical ladder
        # and target-level vs. indication-level whitespace distinctions.
        parts.append(
            interpret_competitive_landscape(
                approved,
                phase3,
                known_drugs_count,
                trial_count,
                indication_approved_drug_count=indication_approved,
                indication_active_trial_count=indication_active_trials,
                indication_phase3_trial_count=indication_phase3_trials,
                indication_total_trial_count=indication_total_trials,
            )
        )

        if spec.get("ot_known_drugs_text"):
            parts.append(f"Known drugs (Open Targets): {spec['ot_known_drugs_text']}")
        if approved or phase3:
            parts.append(f"Approved drugs targeting this gene: {approved}; Phase 3: {phase3}")
        if spec.get("fda_label_text"):
            parts.append(spec["fda_label_text"])
        if spec.get("indication_competition_text"):
            parts.append(f"Indication-level competition: {spec['indication_competition_text']}")
        if spec.get("gbd_prevalence_text"):
            parts.append(spec["gbd_prevalence_text"])
        if spec.get("orphanet_prevalence_text"):
            parts.append(spec["orphanet_prevalence_text"])
        extra = "\n".join(parts) + "\n"
        result = await run_lens(
            msg,
            ctx,
            lens="commercial",
            evidence_types=LENS_EVIDENCE_TYPES["commercial"],
            skill_name="commercial_lens",
            extra_context=extra,
        )
        return _apply_commercial_guard(
            result,
            known_drugs_count=known_drugs_count,
            approved_count=approved,
            indication_approved_drug_count=indication_approved,
            indication_active_trial_count=indication_active_trials,
        )
