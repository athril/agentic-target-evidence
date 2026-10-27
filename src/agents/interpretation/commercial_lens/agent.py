# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CommercialLensAgent — IP landscape + competitive opportunity axes.

Reads patent claims from extracted_claims plus raw patent/trial counts
passed by the graph node. Replaces CompetitiveAgent in the reasoning graph.
"""

from __future__ import annotations

from typing import Any

from agents.interpretation._lens_base import (
    LENS_EVIDENCE_TYPES,
    build_guard_facts,
    map_lens_verdict,
    run_lens,
)
from agents.interpretation._lens_confidence import reaggregate
from agents.interpretation.commercial_lens.contract import CONTRACT
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import CoreClaim
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag, counted_by_gate_flag
from services.evidence.commercial_interpret import (
    competitive_position,
    interpret_competitive_landscape,
)
from services.evidence.constraint_interpret import interpret_patent_landscape
from services.evidence.disease_class_rules import build_disease_class_note


def _market_size_unsized(verdict: LensVerdict, _claims: list[CoreClaim]) -> LensVerdict:
    """Pre-scoring rule when no prevalence names the indication (no matching Orphanet
    record, no GBD burden): an unfavourable market size is uncertain, not "ultra-rare" —
    a gene-associated rare disorder's band is not the indication's (PCSK9 × MI)."""
    axes = [
        a.model_copy(update={"verdict": None})
        if a.axis == "market_size" and a.verdict is False
        else a
        for a in verdict.axes
    ]
    if axes == list(verdict.axes):
        return verdict
    flag = ValidationFlag(
        lens=verdict.lens,
        severity="medium",
        rule_id="market_size_unsized",
        claim_excerpt="",
        message=(
            f"No prevalence record names {verdict.disease}; market_size set to uncertain "
            f"rather than sized from another disorder's prevalence."
        ),
    )
    return verdict.model_copy(
        update={"axes": axes, "validation_flags": [*verdict.validation_flags, flag]}
    )


def _ip_unjudged(verdict: LensVerdict, _claims: list[CoreClaim]) -> LensVerdict:
    """Pre-scoring rule: the retrieved patents are titles and abstracts naming the
    target, which show activity around it, not encumbrance — that needs claim scope,
    jurisdiction and legal status. Every run read a title count as "heavily encumbered"."""
    axes = [
        a.model_copy(
            update={
                "verdict": None,
                "rationale": (
                    "Patents naming the target show activity around it; freedom to operate "
                    "needs claim-level and legal-status review, which this run does not have. "
                    + a.rationale
                ).strip(),
            }
        )
        if a.axis == "ip_landscape" and a.verdict is not None
        else a
        for a in verdict.axes
    ]
    if axes == list(verdict.axes):
        return verdict
    flag = ValidationFlag(
        lens=verdict.lens,
        severity="medium",
        rule_id="ip_landscape_unjudged",
        claim_excerpt="",
        message="ip_landscape set to uncertain: patent titles cannot establish encumbrance.",
    )
    return verdict.model_copy(
        update={"axes": axes, "validation_flags": [*verdict.validation_flags, flag]}
    )


def computed_competitive_axis(verdict: LensVerdict, spec: dict[str, Any]) -> LensVerdict:
    """Set competitive_opportunity from the programmes and readings (commercial_interpret.
    competitive_position), then the verdict from the axes: support when the position is
    favourable and the market is not too small, oppose when it is unfavourable, neutral
    otherwise.

    The model set the axis unfavourable for every indication with any approved drug,
    copying the landscape block's wording as its rationale, whatever the target's position.
    """
    reading = spec.get("programme_reading") or {}
    known = bool(spec.get("indication_landscape_known"))
    position = competitive_position(
        precedent_status=str(spec.get("precedent_status") or ""),
        programme_outcomes=[str(p.get("outcome") or "") for p in reading.get("programmes") or []],
        indication_approved_drug_count=(
            int(spec.get("indication_approved_drug_count") or 0) if known else None
        ),
        disease=str(spec.get("disease") or "the indication"),
    )
    axes = [a for a in verdict.axes if a.axis != "competitive_opportunity"]
    previous = next((a for a in verdict.axes if a.axis == "competitive_opportunity"), None)
    axes.append(
        AxisVerdict(
            axis="competitive_opportunity",
            verdict=position.value,
            confidence=previous.confidence if previous else 0.0,
            rationale=position.reason,
            supporting_claim_ids=list(previous.supporting_claim_ids) if previous else [],
        )
    )
    market = next((a for a in axes if a.axis == "market_size"), None)
    if position.value is True and not (market is not None and market.verdict is False):
        overall = "support"
    elif position.value is False:
        overall = "oppose"
    else:
        overall = "neutral"
    message = f"competitive_opportunity set by rule ({position.case})"
    if overall != verdict.overall_verdict:
        message += f"; verdict {verdict.overall_verdict} → {overall}"
    flag = ValidationFlag(
        lens=verdict.lens,
        severity="low",
        rule_id="commercial.competitive_computed",
        claim_excerpt="",
        message=message + ".",
    )
    flags = [*verdict.validation_flags, flag]
    if overall == "oppose" and position.reports_precedent:
        flags.append(counted_by_gate_flag(verdict.lens, f"Oppose reports {position.case}."))
    return verdict.model_copy(
        update={
            "axes": axes,
            "overall_verdict": overall,
            "validation_flags": flags,
        }
    )


def validated_is_not_oppose(verdict: LensVerdict, precedent_status: str) -> LensVerdict:
    """A drug acting in the requested direction is approved in the indication: the market
    is proven and crowding is a question of differentiation, so the lens is at most
    neutral — unless the population is ultra-rare. The lens opposed every validated
    target in a panel for being "crowded"."""
    market = next((a for a in verdict.axes if a.axis == "market_size"), None)
    if (
        precedent_status != "validated"
        or verdict.overall_verdict != "oppose"
        or (market is not None and market.verdict is False)
    ):
        return verdict
    flag = ValidationFlag(
        lens=verdict.lens,
        severity="medium",
        rule_id="commercial.validated_not_oppose",
        claim_excerpt="",
        message=(
            "A same-direction drug is approved in the indication: validated but competitive "
            "is neutral, not oppose; verdict oppose → neutral."
        ),
    )
    return reaggregate(
        verdict.model_copy(
            update={
                "overall_verdict": "neutral",
                "validation_flags": [*verdict.validation_flags, flag],
            }
        )
    )


class CommercialLensAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        patent_count = spec.get("patent_count", 0)
        indication_patent_count = spec.get("indication_patent_count", 0)
        trial_count = spec.get("trial_count", 0)
        approved = spec.get("ot_known_drugs_approved_count", 0)
        phase3 = spec.get("ot_known_drugs_phase3_count", 0)
        known_drugs_count = spec.get("ot_known_drugs_count", 0)
        indication_approved = spec.get("indication_approved_drug_count", 0)
        indication_active_trials = spec.get("indication_active_trial_count", 0)
        indication_phase3_trials = spec.get("indication_phase3_trial_count", 0)
        indication_total_trials = spec.get("indication_total_trial_count", 0)
        parts = [
            f"Patents naming {spec.get('target_gene', 'the target')} in the title (target IP): "
            f"{patent_count}",
            f"Patents naming only the indication in the title (other mechanisms): "
            f"{indication_patent_count}",
            f"Trial count in retrieval: {trial_count}",
        ]
        disease_class_note = build_disease_class_note(
            spec.get("disease_classes") or (), "commercial"
        )
        if disease_class_note:
            parts.append(disease_class_note)
        # Inject patent-landscape framing to prevent self-contradictory IP claims
        parts.append(interpret_patent_landscape(patent_count, indication_patent_count))
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
                gene=spec.get("target_gene") or "",
                disease=spec.get("disease") or "",
                target_programmes=spec.get("target_programmes") or [],
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
        prevalence_found = bool(spec.get("indication_prevalence_found", True))

        def adjust(verdict: LensVerdict, claims: list[CoreClaim]) -> LensVerdict:
            verdict = _ip_unjudged(verdict, claims)
            if not prevalence_found:
                verdict = _market_size_unsized(verdict, claims)
            return computed_competitive_axis(verdict, spec)

        result = await run_lens(
            msg,
            ctx,
            lens="commercial",
            evidence_types=LENS_EVIDENCE_TYPES["commercial"],
            skill_name="commercial_lens",
            extra_context=extra,
            # Post-LLM safety net (repaired once, then annotated): commercial
            # overstatements — blanket no-drugs claims, indication-level "underserved",
            # market-size-unknown.
            guards=(build_guard_facts(spec), ("commercial", "drug_approval", "modality")),
            adjust_verdict=adjust,
        )
        status = str(spec.get("precedent_status") or "")
        return map_lens_verdict(result, lambda v: validated_is_not_oppose(v, status))
