# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""SafetyLensAgent — toxicity + tissue-specificity axes, computed from the gene's findings
(``verdict.py``), with one model call for the prose."""

from __future__ import annotations

from typing import Any

from agents.interpretation._lens_base import build_guard_facts
from agents.interpretation.safety_lens.contract import CONTRACT
from agents.interpretation.safety_lens.verdict import run_safety
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from services.evidence.constraint_interpret import interpret_expression_context
from services.evidence.disease_class_rules import build_disease_class_note
from services.evidence.guard_rules import GuardFamily
from services.evidence.mouse_phenotype import render_mouse_phenotype
from services.evidence.readings import dependency_band_prompt
from services.evidence.site_of_action import site_of_action_note

# Safety nets on the prose (repaired once, then annotated): text that inverts the
# pre-computed gnomAD constraint bands, misuses bulk-TPM rank as a disease-relevance
# proxy, words a banded quantity (cell-line dependency) more strongly than its band, or
# leaves out a system of the counted mouse finding.
_GUARD_FAMILIES: tuple[GuardFamily, ...] = (
    "constraint",
    "tissue_relevance",
    "quantity",
    "phenotype_grounding",
    "tissue_claim",
    "phenotype_omission",
    "cell_type_claim",
    # A toxicity set on a human-genetics direction the run's reading does not show.
    "direction",
)


def _facts_context(spec: dict[str, Any], constraint_reading: dict[str, Any]) -> str:
    """The facts the prose may draw on, beside the findings: the checks ground numbers and
    names in them."""
    parts: list[str] = []
    disease_class_note = build_disease_class_note(spec.get("disease_classes") or (), "safety")
    if disease_class_note:
        parts.append(disease_class_note)
    if spec.get("ot_safety_text"):
        parts.append(f"Safety liabilities (Open Targets): {spec['ot_safety_text']}")
    if spec.get("ot_mouse_text"):
        parts.append(
            f"Mouse KO phenotypes (Open Targets): {render_mouse_phenotype(spec['ot_mouse_text'])}"
        )
    for key in ("safety_structured_text", "faers_text"):
        if spec.get(key):
            parts.append(str(spec[key]))
    if spec.get("disease_tissue_expression_note"):
        parts.append(
            f"Disease-tissue expression grounding: {spec['disease_tissue_expression_note']}"
        )
    disease_tissue = spec.get("disease_tissue") or spec.get("tissue") or "disease tissue"
    caveat = interpret_expression_context(
        spec.get("bulk_tpm"),
        spec.get("hpa_specificity") or "",
        disease_tissue,
        spec.get("disease_cell_types") or (),
    )
    if caveat:
        parts.append(caveat)
    if spec.get("secreted"):
        parts.append(
            site_of_action_note(
                str(spec.get("target_gene") or "The target"),
                str(spec.get("disease_tissue") or ""),
                list(spec.get("top_tpm_tissues") or []),
                lens="safety",
            )
        )
    if constraint_reading.get("summary_text"):
        parts.append(f"Constraint interpretation: {constraint_reading['summary_text']}")
    profile = spec.get("dependency_profile") or {}
    if profile.get("text"):
        parts.append(str(profile["text"]))
    return "\n".join(parts)


class SafetyLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        constraint_reading: dict[str, Any] = spec.get("constraint_reading") or {}
        guard_facts = build_guard_facts(spec, constraint_reading=constraint_reading)
        extra = _facts_context(spec, constraint_reading)
        if guard_facts.dependency is not None:
            extra += "\n" + dependency_band_prompt(guard_facts.dependency)
        return await run_safety(
            msg, ctx, extra_context=extra, guards=(guard_facts, _GUARD_FAMILIES)
        )
