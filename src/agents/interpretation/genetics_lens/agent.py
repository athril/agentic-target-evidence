# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GeneticsLensAgent — causality + genetic validity axes."""

from __future__ import annotations

import uuid

from agents.interpretation._lens_base import (
    LENS_EVIDENCE_TYPES,
    apply_constraint_guard_to_result,
    run_lens,
)
from agents.interpretation.genetics_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag
from services.evidence.constraint_interpret import (
    apply_mendelian_floor_guard,
    compute_mendelian_grade,
)
from services.evidence.disease_class_rules import build_disease_class_note

# Floor confidence applied to the causality axis when the Mendelian floor
# activates — matches the inheritance tie-break floor for consistency.
_MENDELIAN_FLOOR_CONFIDENCE = 0.60


def _apply_mendelian_floor(result: AgentMessage) -> AgentMessage:
    """Post-LLM reconciliation: clamp the causality axis and strip GWAS/coloc-
    absence-as-negative rationale when Mendelian-grade causality is already
    established. Mirrors `apply_constraint_guards`'s annotate-rather-than-
    silently-rewrite pattern; every activation is logged via a ValidationFlag
    for Langfuse/HITL audit.
    """
    verdicts = result.payload.get("lens_verdicts") or []
    if not verdicts:
        return result

    verdict = LensVerdict.model_validate(verdicts[0])

    axes: list[AxisVerdict] = []
    found_causality = False
    for ax in verdict.axes:
        if ax.axis != "causality":
            axes.append(ax)
            continue
        found_causality = True
        axes.append(
            AxisVerdict(
                axis="causality",
                verdict=True if ax.verdict is not True else ax.verdict,
                confidence=max(ax.confidence, _MENDELIAN_FLOOR_CONFIDENCE),
                rationale=apply_mendelian_floor_guard(ax.rationale),
                supporting_claim_ids=ax.supporting_claim_ids,
            )
        )
    if not found_causality:
        axes.append(
            AxisVerdict(
                axis="causality",
                verdict=True,
                confidence=_MENDELIAN_FLOOR_CONFIDENCE,
                rationale="Mendelian causality floor applied: gold-star P/LP, "
                "ClinGen Definitive/Strong, and/or strong knowledge-graph "
                "corroboration establish causality independently of "
                "GWAS/colocalization signal.",
                supporting_claim_ids=[],
            )
        )

    flag = ValidationFlag(
        lens="genetics",
        severity="medium",
        rule_id="mendelian_causality_floor",
        claim_excerpt="",
        message="Mendelian causality floor activated: causality axis clamped to "
        "favourable with floor confidence; GWAS/coloc-absence-as-negative "
        "rationale annotated rather than silently rewritten.",
    )

    updated = verdict.model_copy(
        update={
            "axes": axes,
            "rationale": apply_mendelian_floor_guard(verdict.rationale),
            "narrative": apply_mendelian_floor_guard(verdict.narrative),
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


def _apply_constraint_guard(result: AgentMessage, constraint_reading: dict) -> AgentMessage:
    """Genetics-lens post-LLM constraint guard — delegates to the shared helper.

    See `apply_constraint_guard_to_result` in `_lens_base`; kept as a thin named
    wrapper so the genetics `act()` flow reads the same as before.
    """
    return apply_constraint_guard_to_result(result, constraint_reading, lens="genetics")


class GeneticsLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}

        extra_parts: list[str] = []

        # Disease-class-aware context (replaces the old oncology-only binary —
        # see config/disease_class_rules.yaml).
        disease_classes = spec.get("disease_classes") or ()
        disease_class_note = build_disease_class_note(disease_classes, "genetics")
        if disease_class_note:
            extra_parts.append(disease_class_note)

        # Include pre-rendered source evidence so the lens can reason directly
        # over structured gnomAD / ClinVar / OT records even when claims == 0.
        source_evidence_text: str = spec.get("source_evidence_text") or ""
        if source_evidence_text:
            extra_parts.append(source_evidence_text)

        # Inject pre-computed mechanism direction into the prompt so the LLM
        # inherits the deterministic inference rather than re-deriving it from raw floats.
        floor_signals: dict = spec.get("floor_signals") or {}
        mechanism_direction: dict | None = floor_signals.get("mechanism_direction")
        constraint_reading: dict = floor_signals.get("constraint_reading") or {}

        if mechanism_direction and mechanism_direction.get("mechanism") != "ambiguous":
            direction_label = mechanism_direction["direction"].upper()
            mechanism_label = (
                "gain-of-function (GoF)"
                if mechanism_direction["mechanism"] == "gof"
                else "loss-of-function / haploinsufficiency"
            )
            extra_parts.append(
                f"Mechanism direction (pre-computed, deterministic):\n"
                f"  Inferred mechanism: {mechanism_label}\n"
                f"  Therapeutic implication: {direction_label} the target\n"
                f"  Confidence: {mechanism_direction.get('confidence', 0):.0%}\n"
                f"  Rationale: {mechanism_direction.get('rationale', '')}\n"
                f"Use this direction in your narrative. Do not re-derive mechanism from raw floats."
            )

        # Inject constraint interpretation bands if available (prevents LLM inversion)
        constraint_summary = constraint_reading.get("summary_text") or ""
        if constraint_summary:
            extra_parts.append(
                f"Constraint interpretation (pre-computed bands — use verbatim, do not re-band):\n"
                f"  {constraint_summary}"
            )

        # SPOKE graph association — route onto the causality axis.
        graph_association: dict | None = floor_signals.get("graph_association")
        if graph_association:
            sources = ", ".join(graph_association.get("edge_sources") or []) or "unknown source"
            corroborates = graph_association.get("corroborates_causality")
            causality_note = (
                "This corroborates gene-disease causality independently of GWAS/coloc."
                if corroborates
                else "Treat as weak/co-mention signal, not independent corroboration."
            )
            extra_parts.append(
                "Causality axis input — SPOKE knowledge-graph association "
                "(pre-computed, deterministic):\n"
                f"  Disease: {graph_association.get('disease_name', '')}\n"
                f"  Edge sources: {sources}\n"
                f"  gwas_pvalue={graph_association.get('gwas_pvalue')}, "
                f"diseases_score={graph_association.get('diseases_score')}\n"
                f"  {causality_note}"
            )

        # Ontology constraints: inheritance mode (ClinGen/HPO) + HPO phenotype breadth.
        inheritance_mode: str | None = floor_signals.get("inheritance_mode")
        hpo_phenotype_count: int = floor_signals.get("hpo_phenotype_count") or 0
        hpo_specificity_band: str = floor_signals.get("hpo_specificity_band") or "unknown"
        if inheritance_mode or hpo_phenotype_count:
            onto_lines = ["Ontology constraints (pre-computed, deterministic):"]
            if inheritance_mode:
                onto_lines.append(f"  Mode of inheritance: {inheritance_mode}")
            if hpo_phenotype_count:
                onto_lines.append(
                    f"  HPO phenotype breadth: {hpo_phenotype_count} phenotype(s) "
                    f"(specificity band: {hpo_specificity_band})"
                )
                onto_lines.append(
                    "  A focal phenotype set corroborates a tissue-specific target; "
                    "broad multi-system pleiotropy is a tractability/safety caution."
                )
            extra_parts.append("\n".join(onto_lines))

        # Mendelian causality floor: gold-star P/LP, ClinGen Definitive/Strong,
        # or strong graph corroboration already establish causality — GWAS/coloc
        # absence must not be read as a negative on top of that.
        mendelian_grade = compute_mendelian_grade(
            high_star_plp=floor_signals.get("high_star_plp") or 0,
            plp_count=floor_signals.get("plp_count") or 0,
            clingen_classification=floor_signals.get("clingen_classification"),
            graph_association=floor_signals.get("graph_association"),
        )
        if mendelian_grade:
            extra_parts.append(
                "Mendelian context (pre-computed, deterministic):\n"
                "  This gene-disease pair already has Mendelian-grade genetic validation.\n"
                "  Absence of GWAS/colocalization signal is EXPECTED for a Mendelian "
                "disease gene and must NOT be cited to lower causality confidence."
            )

        extra_context = "\n".join(extra_parts) + ("\n" if extra_parts else "")

        result = await run_lens(
            msg,
            ctx,
            lens="genetics",
            evidence_types=LENS_EVIDENCE_TYPES["genetics"],
            skill_name="genetics_lens",
            extra_context=extra_context,
        )

        result = _apply_constraint_guard(result, constraint_reading)

        if mendelian_grade:
            result = _apply_mendelian_floor(result)

        return result
