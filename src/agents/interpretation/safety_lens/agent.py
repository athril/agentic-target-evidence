# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""SafetyLensAgent — toxicity + tissue-specificity axes."""

from __future__ import annotations

import logging
from typing import Any

from agents.interpretation._lens_base import (
    LENS_EVIDENCE_TYPES,
    build_guard_facts,
    map_lens_verdict,
    run_lens,
    split_enabled,
)
from agents.interpretation._lens_dependency import apply_common_essential, cap_unexposed_toxicity
from agents.interpretation.safety_lens.contract import CONTRACT
from agents.interpretation.safety_lens.split import (
    SplitUnavailable,
    run_split_safety,
    split_fallback_flag,
)
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from schemas.verdicts import LensVerdict
from services.evidence.constraint_interpret import (
    interpret_expression_context,
    interpret_gof_tolerance_support,
)
from services.evidence.disease_class import DiseaseClass
from services.evidence.disease_class_rules import build_disease_class_note
from services.evidence.drug_precedent import requested_effect
from services.evidence.guard_rules import GuardFamily
from services.evidence.liabilities import Liability, render_liabilities_block
from services.evidence.mouse_phenotype import render_mouse_phenotype
from services.evidence.readings import dependency_band_prompt

logger = logging.getLogger(__name__)

# Post-LLM safety nets (repaired once, then annotated): text that inverts the pre-computed
# gnomAD constraint bands, misuses bulk-TPM rank as a disease-relevance proxy, or words a
# banded quantity (cell-line dependency) more strongly than its band.
_GUARD_FAMILIES: tuple[GuardFamily, ...] = (
    "constraint",
    "tissue_relevance",
    "quantity",
    "phenotype_grounding",
    "tissue_claim",
    "phenotype_omission",
    "cell_type_claim",
)


class SafetyLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        parts: list[str] = []

        disease_class_note = build_disease_class_note(spec.get("disease_classes") or (), "safety")
        if disease_class_note:
            parts.append(disease_class_note)

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
        expr_caveat = interpret_expression_context(
            bulk_tpm, hpa_specificity, disease_tissue, spec.get("disease_cell_types") or ()
        )
        if expr_caveat:
            parts.append(expr_caveat)

        # Inject constraint summary for safety lens (prevents HI misstatement)
        constraint_reading: dict[str, Any] = spec.get("constraint_reading") or {}
        if constraint_reading.get("summary_text"):
            parts.append(
                f"Constraint interpretation (pre-computed — do not re-band):\n"
                f"  {constraint_reading['summary_text']}"
            )

        # GoF mechanism + LoF-tolerance: supports tolerability of inhibition
        mechanism_direction: dict[str, Any] = spec.get("mechanism_direction") or {}
        gof_tolerance_text = interpret_gof_tolerance_support(
            mechanism_direction.get("mechanism"),
            constraint_reading.get("is_lof_tolerant", False),
        )
        if gof_tolerance_text:
            parts.append(gof_tolerance_text)

        # Every candidate liability the gene's own data support (Rule 18 asks the lens to
        # name each one; the phenotype-omission guard checks the mouse-KO systems).
        if spec.get("candidate_liabilities") is not None:
            parts.append(
                render_liabilities_block(
                    [Liability.model_validate(li) for li in spec["candidate_liabilities"]]
                )
            )

        precedent = spec.get("drug_precedent") or {}
        if precedent.get("status") == "validated":
            approved = ", ".join(str(d).lower() for d in precedent.get("concordant_approved") or [])
            parts.append(
                "Human exposure (pre-computed): drugs acting on the target in the requested "
                f"direction are approved in this indication ({approved}). Their on-target "
                "safety is known from patients: weigh the candidate liabilities against that "
                "clinical experience (labels, trials, FAERS), which outranks mouse knockout "
                "phenotypes and bulk expression. Oppose only for a liability that the clinical "
                "record shows is limiting, not because knockouts or broad expression suggest one."
            )

        guard_facts = build_guard_facts(spec, constraint_reading=constraint_reading)
        if guard_facts.dependency is not None:
            parts.append(dependency_band_prompt(guard_facts.dependency))
        profile = spec.get("dependency_profile") or {}
        if profile.get("text"):
            parts.append(str(profile["text"]))

        extra = "\n".join(parts) + "\n" if parts else ""
        guards = (guard_facts, _GUARD_FAMILIES)
        liabilities = list(spec.get("candidate_liabilities") or [])
        validated = precedent.get("status") == "validated"

        def essential(v: LensVerdict) -> LensVerdict:
            return apply_common_essential(
                v,
                profile,
                lowers_activity=requested_effect(spec.get("direction")) != "increase",
                validated=validated,
                is_oncology=DiseaseClass.ONCOLOGY.value in (spec.get("disease_classes") or ()),
            )

        result = None
        fallback = None
        from_split = False
        if split_enabled("safety"):
            try:
                result = await run_split_safety(
                    msg, ctx, extra_context=extra, guards=guards, rules=essential
                )
                from_split = True
            except SplitUnavailable as exc:
                logger.warning(
                    "safety_lens: split path unavailable (%s) — using the single-call lens", exc
                )
                fallback = split_fallback_flag(exc.question_ids)
        if result is None:
            result = await run_lens(
                msg,
                ctx,
                lens="safety",
                evidence_types=LENS_EVIDENCE_TYPES["safety"],
                skill_name="safety_lens",
                extra_context=extra,
                guards=guards,
            )
        if fallback is not None:
            result = map_lens_verdict(
                result,
                lambda v: v.model_copy(
                    update={"validation_flags": [*v.validation_flags, fallback]}
                ),
            )
        # The split path applied the verdict rule before writing its narrative; the
        # confidence cap follows scoring on both paths.
        return map_lens_verdict(
            result,
            lambda v: cap_unexposed_toxicity(
                v if from_split else essential(v),
                liabilities,
                human_exposure=bool(spec.get("human_exposure")),
            ),
        )
