# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""GeneticsLensAgent — causality + genetic validity axes."""

from __future__ import annotations

import uuid
from typing import Any

from agents.interpretation._lens_base import (
    LENS_EVIDENCE_TYPES,
    build_guard_facts,
    run_lens,
)
from agents.interpretation._lens_confidence import reaggregate
from agents.interpretation.genetics_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag
from services.evidence.constraint_interpret import (
    MENDELIAN_GWAS_ABSENCE_PHRASE,
    ConstraintReading,
    mendelian_grade_from_signals,
    missense_framing_note,
)
from services.evidence.disease_class_rules import build_disease_class_note

# Floor confidence applied to the causality axis when the Mendelian floor
# activates — matches the inheritance tie-break floor for consistency.
_MENDELIAN_FLOOR_CONFIDENCE = 0.60


def _apply_mendelian_floor(result: AgentMessage) -> AgentMessage:
    """Post-LLM reconciliation: clamp the causality axis to favourable with floor
    confidence when Mendelian-grade causality is already established. Every
    activation is logged via a ValidationFlag for Langfuse/HITL audit.

    The companion text check — GWAS/coloc absence cited as a causality negative —
    is the ``mendelian_floor`` guard family, run with the other guards by
    `apply_guards_to_result` before this clamp so its annotations carry over.
    """
    if not isinstance(result.payload, dict):
        return result
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
        # model_copy keeps the scorer's fields (model_confidence, evidence_sources).
        axes.append(
            ax.model_copy(
                update={
                    "verdict": True,
                    "confidence": max(ax.confidence, _MENDELIAN_FLOOR_CONFIDENCE),
                }
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
        "favourable with floor confidence.",
    )

    # The clamp changes an axis after scoring, so the lens number is re-aggregated.
    updated = reaggregate(
        verdict.model_copy(
            update={
                "axes": axes,
                "validation_flags": [*verdict.validation_flags, flag],
            }
        )
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
        floor_signals: dict[str, Any] = spec.get("floor_signals") or {}
        mechanism_direction: dict[str, Any] | None = floor_signals.get("mechanism_direction")
        constraint_reading: dict[str, Any] = floor_signals.get("constraint_reading") or {}

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

        # Missense framing: LLMs default to "LoF-tolerant but missense-intolerant,
        # consistent with GoF" even when the missense metrics are tolerant. Give the
        # correct framing (GoF acts at specific residues) before the guard has to flag it.
        if constraint_reading:
            missense_note = missense_framing_note(
                ConstraintReading.model_validate(constraint_reading),
                (mechanism_direction or {}).get("mechanism"),
            )
            if missense_note:
                extra_parts.append(missense_note)

        # SPOKE graph association — route onto the causality axis.
        graph_association: dict[str, Any] | None = floor_signals.get("graph_association")
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
        mendelian_grade = mendelian_grade_from_signals(floor_signals)
        if mendelian_grade:
            disease_label = spec.get("disease") or "this disease"
            curation = floor_signals.get("clingen_classification")
            curation_line = (
                f"  ClinGen/GenCC classify the gene's relationship with {disease_label} (or a "
                f"subtype of it) as {curation}: cite this curation first on the causality "
                "axis — it is Mendelian validation for this indication.\n"
                if curation
                else ""
            )
            extra_parts.append(
                "Mendelian context (pre-computed, deterministic):\n"
                "  This gene-disease pair already has Mendelian-grade genetic validation.\n"
                f"{curation_line}"
                + (
                    f"  GWAS also reports {floor_signals['gwas_traits_in_scope']} trait(s) matched "
                    "to this disease: cite them as corroboration of the Mendelian evidence — "
                    "never describe GWAS signal as absent."
                    if floor_signals.get("gwas_traits_in_scope")
                    else f"  {MENDELIAN_GWAS_ABSENCE_PHRASE}"
                )
            )

        other_curations = floor_signals.get("out_of_scope_curations") or []
        if other_curations and not mendelian_grade:
            extra_parts.append(
                "Curation scope (pre-computed, deterministic):\n"
                f"  ClinGen/GenCC curate this gene for: {'; '.join(other_curations)}. "
                f"None of these is {spec.get('disease') or 'the indication'} or one of "
                "its subtypes, so they are NOT Mendelian validation for this indication. "
                "You may cite them as evidence that the gene has a causal role in a "
                "related disorder (and for direction of effect), never as a curation of "
                "this gene-disease pair. Gene-wide ClinVar P/LP counts concern those "
                "disorders too."
            )

        extra_context = "\n".join(extra_parts) + ("\n" if extra_parts else "")

        result = await run_lens(
            msg,
            ctx,
            lens="genetics",
            evidence_types=LENS_EVIDENCE_TYPES["genetics"],
            skill_name="genetics_lens",
            extra_context=extra_context,
            # Post-LLM safety nets (repaired once, then annotated): text that inverts the
            # gnomAD constraint bands or treats GWAS/coloc absence as a causality negative.
            guards=(
                build_guard_facts(
                    spec, constraint_reading=constraint_reading, mendelian_grade=mendelian_grade
                ),
                ("constraint", "mendelian_floor"),
            ),
        )

        if mendelian_grade:
            result = _apply_mendelian_floor(result)

        return result
