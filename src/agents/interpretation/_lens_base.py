# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared base logic for all six interpretation lens agents.

Each lens follows the same contract:
  1. Deserialise CoreClaim dicts from task_spec["extracted_claims"]
  2. Filter to this lens's relevant evidence types
  3. Call LLM with the lens-specific skill
  4. Parse LensVerdict from the response
  5. Return {"lens_verdicts": [verdict.model_dump(mode="json")]}

Concrete lens agents import `run_lens` and supply their `LENS_NAME`,
`EVIDENCE_TYPES`, and `skill_name`. They declare their own AgentContract.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Literal

from core.json_utils import loads_recovering, strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, Direction, EvidenceType
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag
from services.evidence.clinical_trial_interpret import TrialFact, apply_clinical_phase_guard
from services.evidence.constraint_interpret import (
    ConstraintReading,
    apply_constraint_guards,
    apply_depmap_relevance_guard,
)
from services.evidence.disease_tissue import apply_tissue_relevance_guard
from services.evidence.evidence_hierarchy import (
    evidence_weight,
    infer_evidence_subtype,
    llm_prior_weight,
)

LensName = Literal["genetics", "biology", "safety", "clinical", "commercial", "regulatory"]

# Single source of truth for which *structured* evidence types each lens reasons
# over. Both the lens agents (to filter the claims they consume) and the lens report
# writer (to cross-reference the kept source evidence) read from this map.
#
# Note: free-text literature types (ARTICLE/ABSTRACT/BOOK/CONFERENCE) are deliberately
# absent here — they carry no native sub-type and route by per-claim `topics` tags
# instead (see `claim_matches_lens`). Biology is therefore topic-routed for literature
# just like the other three literature-consuming lenses; it keeps only its structured
# types below.
LENS_EVIDENCE_TYPES: dict[LensName, tuple[EvidenceType, ...]] = {
    "genetics": (EvidenceType.GENETICS, EvidenceType.CONSTRAINT),
    "biology": (
        EvidenceType.FUNCTIONAL_GENOMICS,
        EvidenceType.DRUGGABILITY,
        EvidenceType.OMICS,  # tissue/anatomical expression — mechanism & disease-tissue overlap
        EvidenceType.EXPRESSION,
        EvidenceType.REGULATORY_ELEMENT,  # cis-regulatory assay coverage at the locus (ENCODE)
    ),
    "safety": (
        EvidenceType.OMICS,
        EvidenceType.EXPRESSION,
        EvidenceType.GENETICS,
        EvidenceType.CONSTRAINT,
        EvidenceType.REGULATORY,  # FAERS signal + black-box / contraindications
        EvidenceType.FUNCTIONAL_GENOMICS,  # DepMap essentiality + IMPC viability/lethality
    ),
    "clinical": (EvidenceType.CLINICAL_TRIAL,),
    "commercial": (
        EvidenceType.PATENT,
        EvidenceType.REGULATORY,  # FDA-approved drug landscape / gene-in-MoA
    ),
    "regulatory": (EvidenceType.REGULATORY,),
}

_VALID_VERDICTS = {"support", "oppose", "neutral", "insufficient_evidence"}
_MAX_CLAIMS = 100  # cap to stay within local model context

# A literature claim whose source never resolved a quality score (no SJR/OpenAlex
# match) falls back to the same floor as a Q4 journal/preprint.
_UNSCORED_LITERATURE_WEIGHT = 0.2

# Free-text literature types. A literature claim carries no native sub-type, so it
# routes to lenses by its per-claim ``topics`` tags rather than by ``evidence_type`` —
# for every lens, including biology.
_LITERATURE_TYPES: frozenset[EvidenceType] = frozenset(
    {
        EvidenceType.ARTICLE,
        EvidenceType.ABSTRACT,
        EvidenceType.BOOK,
        EvidenceType.CONFERENCE,
    }
)


def claim_matches_lens(claim: CoreClaim, lens: LensName) -> bool:
    """Single source of truth for "does this claim belong to this lens".

    Shared by lens-input routing (`_filter_claims`) and lens-report citation
    selection (`lens_report._render`) so the two can never drift apart.

    - **Literature** claims route purely by their multi-valued `topics` tags: a claim
      reaches a lens iff that lens is named in `topics`. This holds for *every* lens,
      biology included. commercial/regulatory are not in `LensTopic`, so literature
      never reaches them.
    - **Structured** claims route by `evidence_type` membership in `LENS_EVIDENCE_TYPES`.

    ``lens`` is a plain str; `topics` holds `LensTopic` (a `StrEnum`), so ``lens in
    claim.topics`` compares by value as intended.
    """
    if claim.evidence_type in _LITERATURE_TYPES:
        return lens in claim.topics
    return claim.evidence_type in LENS_EVIDENCE_TYPES.get(lens, ())


def _deserialise_claims(raw: list) -> list[CoreClaim]:
    claims: list[CoreClaim] = []
    for item in raw:
        if isinstance(item, CoreClaim):
            claims.append(item)
        elif isinstance(item, dict):
            with contextlib.suppress(Exception):
                claims.append(CoreClaim.model_validate(item))
    return claims


def _filter_claims(
    claims: list[CoreClaim],
    evidence_types: tuple[EvidenceType, ...],
    lens: LensName | None = None,
) -> list[CoreClaim]:
    """Select the claims a lens reasons over.

    With ``lens`` supplied, delegates to `claim_matches_lens` (structured types route
    by `evidence_type`; literature routes by `topics`). With ``lens=None``, falls back
    to a pure `evidence_type` filter over ``evidence_types`` — kept for direct
    callers/tests that only want type filtering.
    """
    if lens is None:
        return [c for c in claims if c.evidence_type in evidence_types]
    return [c for c in claims if claim_matches_lens(c, lens)]


def _claim_weight(
    claim: CoreClaim, quality_map: dict, disease_classes: frozenset[str]
) -> float:
    """Evidence-strength weight on the same 0-1 scale for both literature and
    structured claims — literature uses its resolved `sjr_score` (or the
    Q4/preprint floor if unscored); structured evidence uses the disease-class-
    conditional `evidence_weight` hierarchy (config/evidence_hierarchy.yaml),
    replacing the old flat `_NON_LITERATURE_WEIGHT`.
    """
    if claim.evidence_type in _LITERATURE_TYPES:
        q = quality_map.get(str(claim.source_evidence_id)) if claim.source_evidence_id else None
        score = q.get("sjr_score") if q else None
        return score if score is not None else _UNSCORED_LITERATURE_WEIGHT
    subtype = infer_evidence_subtype(claim.evidence_type, claim.claim_text)
    return evidence_weight(claim.evidence_type, subtype, disease_classes)


def _claim_sort_key(
    claim: CoreClaim, quality_map: dict, disease_classes: frozenset[str] = frozenset()
) -> tuple[float, float]:
    """Rank claims best-first so truncation drops the weakest ones, not whichever
    happened to land past index `_MAX_CLAIMS` in extraction order.

    Primary key: `_claim_weight` (descending). Secondary key: claim confidence,
    descending.
    """
    weight = _claim_weight(claim, quality_map, disease_classes)
    return (-weight, -(claim.confidence or 0.0))


def _claims_to_json(
    claims: list[CoreClaim],
    quality_map: dict | None = None,
    disease_classes: frozenset[str] = frozenset(),
) -> str:
    quality_map = quality_map or {}
    ranked = sorted(claims, key=lambda c: _claim_sort_key(c, quality_map, disease_classes))
    items = []
    for c in ranked[:_MAX_CLAIMS]:
        item = {
            "claim_id": str(c.evidence_id),
            "evidence_type": c.evidence_type.value,
            "claim_text": c.claim_text[:200],
            "direction": c.direction.value,
            "confidence": c.confidence,
        }
        q = quality_map.get(str(c.source_evidence_id)) if c.source_evidence_id else None
        if q:
            item["quality"] = {
                "score": q.get("sjr_score"),
                "quartile": q.get("sjr_quartile"),
                "predatory": q.get("predatory_flag"),
                "preprint": q.get("preprint_flag"),
            }
        items.append(item)
    return json.dumps(items, ensure_ascii=False)


def _build_evidence_ledger(
    claims: list[CoreClaim], quality_map: dict, disease_classes: frozenset[str]
) -> str:
    """Render the evidence-strength ledger injected alongside the claims JSON.

    Groups claims by (evidence_type, subtype) bucket and reports the claim count
    and max deterministic weight per bucket, descending by weight, so the LLM sees
    at a glance which evidence categories are strongest before it reasons over the
    individual claims. Closes with the `llm_prior_weight` floor rule so uncited
    model prior knowledge cannot be used to inflate confidence. Returns "" for an
    empty claim list (mirrors the other guards' no-op-when-nothing-to-say shape).
    """
    if not claims:
        return ""

    buckets: dict[tuple[str, str | None], list[float]] = {}
    for c in claims:
        weight = _claim_weight(c, quality_map, disease_classes)
        subtype = (
            None
            if c.evidence_type in _LITERATURE_TYPES
            else infer_evidence_subtype(c.evidence_type, c.claim_text)
        )
        buckets.setdefault((c.evidence_type.value, subtype), []).append(weight)

    rows = [
        (max(weights), f"{etype}/{subtype}" if subtype else etype, len(weights))
        for (etype, subtype), weights in buckets.items()
    ]
    rows.sort(key=lambda r: -r[0])

    lines = [f"- {label}: {count} claim(s), weight {weight:.2f}" for weight, label, count in rows]
    header = (
        "Evidence-strength ledger (deterministic weights, 0-1 scale; "
        "use to calibrate confidence, strongest first):"
    )
    footer = (
        f"Uncited prior knowledge not backed by a claim above carries weight "
        f"{llm_prior_weight():.2f} and must not by itself raise verdict confidence."
    )
    return header + "\n" + "\n".join(lines) + "\n" + footer


def _direction_enum(direction: str) -> Direction:
    return (
        Direction(direction) if direction in Direction._value2member_map_ else Direction.UNSPECIFIED
    )


def _insufficient_verdict(
    lens: LensName,
    target_gene: str,
    disease: str,
    direction_enum: Direction,
    run_id: uuid.UUID,
    trace_id: str,
    *,
    rationale: str = "LLM response could not be parsed.",
    narrative: str = "",
) -> LensVerdict:
    return LensVerdict(
        run_id=run_id,
        trace_id=trace_id,
        lens=lens,
        target_gene=target_gene,
        disease=disease,
        direction=direction_enum,
        overall_verdict="insufficient_evidence",
        confidence=0.0,
        axes=[],
        rationale=rationale,
        narrative=narrative,
    )


def _parse_verdict(
    raw: str,
    lens: LensName,
    target_gene: str,
    disease: str,
    direction: str,
    run_id: uuid.UUID,
    trace_id: str,
) -> LensVerdict:
    direction_enum = _direction_enum(direction)
    try:
        # loads_recovering: tolerates two recurring local-model formatting faults
        # that would otherwise discard an otherwise-valid verdict as "could not be
        # parsed" — (1) literal newlines inside multi-paragraph prose fields (e.g.
        # the biology lens's mandated 2-4 paragraph `narrative`), via strict=False;
        # and (2) a prematurely-closed root object whose remaining keys (e.g. `axes`)
        # spill out as siblings, by splicing out the stray closing brace.
        data = loads_recovering(strip_json_fence(raw))
        if isinstance(data, dict):
            ov = data.get("overall_verdict", "insufficient_evidence")
            if ov not in _VALID_VERDICTS:
                ov = "insufficient_evidence"
            axes = [
                AxisVerdict(
                    axis=ax.get("axis", ""),
                    verdict=ax.get("verdict"),
                    confidence=float(max(0.0, min(1.0, ax.get("confidence", 0.0)))),
                    rationale=ax.get("rationale", ""),
                    supporting_claim_ids=[str(x) for x in (ax.get("supporting_claim_ids") or [])],
                )
                for ax in (data.get("axes") or [])
            ]
            return LensVerdict(
                run_id=run_id,
                trace_id=trace_id,
                lens=lens,
                target_gene=target_gene,
                disease=disease,
                direction=direction_enum,
                overall_verdict=ov,
                confidence=float(max(0.0, min(1.0, data.get("confidence", 0.0)))),
                axes=axes,
                rationale=data.get("rationale", ""),
                narrative=data.get("narrative", ""),
            )
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return _insufficient_verdict(lens, target_gene, disease, direction_enum, run_id, trace_id)


async def run_lens(
    msg: AgentMessage,
    ctx: RunContext,
    *,
    lens: LensName,
    evidence_types: tuple[EvidenceType, ...],
    skill_name: str,
    extra_context: str = "",
    guard_empty: bool = False,
    has_fallback_evidence: bool = False,
    empty_evidence_note: str = "",
) -> AgentMessage:
    """Execute the lens reasoning pipeline and return an AgentMessage."""
    spec = msg.task_spec or {}
    target_gene = spec.get("target_gene", "unknown")
    disease = spec.get("disease", "unknown")
    direction = spec.get("direction") or "unspecified"
    disease_classes = frozenset(spec.get("disease_classes") or ())

    all_claims = _deserialise_claims(spec.get("extracted_claims") or [])
    relevant = _filter_claims(all_claims, evidence_types, lens)
    quality_map = spec.get("source_quality") or {}

    if guard_empty and not relevant and not has_fallback_evidence:
        verdict = _insufficient_verdict(
            lens,
            target_gene,
            disease,
            _direction_enum(direction),
            msg.run_id,
            msg.trace_id,
            rationale=(
                "No evidence of this lens's type passed screening; "
                "verdict reflects the evidence gap, not a negative finding."
            ),
            narrative=empty_evidence_note,
        )
        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"lens_verdicts": [verdict.model_dump(mode="json")]},
            trace_id=msg.trace_id,
        )

    skill_text = ctx.load_skill(skill_name)
    classification = classify(relevant) if relevant else DataClass.NON_SENSITIVE
    provider, _model = ctx.select_model(classification, f"{lens}_lens")

    claims_json = _claims_to_json(relevant, quality_map, disease_classes) if relevant else "[]"
    ledger = _build_evidence_ledger(relevant, quality_map, disease_classes) if relevant else ""
    ledger_block = f"{ledger}\n\n" if ledger else ""
    user_content = (
        f"Gene: {target_gene}\n"
        f"Disease: {disease}\n"
        f"Therapeutic direction: {direction}\n"
        f"{extra_context}"
        f"\n{ledger_block}"
        f"Relevant claims ({len(relevant)}):\n{claims_json}\n\n"
        f"Return the {lens} lens verdict JSON object."
    )

    completion = await provider.complete(
        CompletionRequest(
            messages=[{"role": "user", "content": user_content}],
            system=skill_text,
            classification=classification,
            task=f"{lens}_lens",
            model_override=_model,
        )
    )

    verdict = _parse_verdict(
        completion.content,
        lens=lens,
        target_gene=target_gene,
        disease=disease,
        direction=direction,
        run_id=msg.run_id,
        trace_id=msg.trace_id,
    )

    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=msg.run_id,
        from_agent=msg.to_agent,
        to_agent=msg.from_agent,
        intent="result",
        payload={"lens_verdicts": [verdict.model_dump(mode="json")]},
        trace_id=msg.trace_id,
    )


def apply_constraint_guard_to_result(
    result: AgentMessage,
    constraint_reading: dict,
    *,
    lens: LensName,
) -> AgentMessage:
    """Post-LLM safety net shared by the genetics and safety lenses.

    Annotates (never silently rewrites) narrative/rationale/axis text that
    contradicts the pre-computed gnomAD constraint bands — e.g. claiming
    haploinsufficiency when LOEUF >= 0.35, or "strong missense constraint" /
    "high mis_z" when mis_z and MOEUF do not clear the constraint threshold.
    Both lenses already receive the correct bands in their prompt; this catches
    the cases where the LLM still inverts or hallucinates a claim. Every
    activation is recorded as a ValidationFlag for Langfuse/HITL audit.
    """
    if not constraint_reading:
        return result
    verdicts = result.payload.get("lens_verdicts") or []
    if not verdicts:
        return result

    verdict = LensVerdict.model_validate(verdicts[0])
    reading = ConstraintReading.model_validate(constraint_reading)

    guarded_rationale = apply_constraint_guards(verdict.rationale, reading)
    guarded_narrative = apply_constraint_guards(verdict.narrative, reading)
    axes = [
        ax.model_copy(update={"rationale": apply_constraint_guards(ax.rationale, reading)})
        for ax in verdict.axes
    ]

    fired = any(
        "CONSTRAINT GUARD" in t
        for t in (guarded_rationale, guarded_narrative, *(ax.rationale for ax in axes))
    )
    if not fired:
        return result

    flag = ValidationFlag(
        lens=lens,
        severity="medium",
        rule_id="constraint_interpretation_guard",
        claim_excerpt="",
        message="Constraint guard activated: narrative/rationale contained an "
        "unsupported haploinsufficiency, missense-criticality/constraint, or "
        "mis_z-direction claim that contradicts the precomputed gnomAD constraint "
        "bands; annotated rather than silently rewritten.",
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


def apply_clinical_phase_guard_to_result(
    result: AgentMessage,
    trial_facts: list[dict],
    *,
    lens: LensName = "clinical",
) -> AgentMessage:
    """Post-LLM safety net for the clinical lens.

    Annotates (never silently rewrites) narrative/rationale/axis text that
    misstates a registry trial's phase or recruitment status — e.g. reporting
    "two Phase 3 trials" when only one is Phase 3, or calling a COMPLETED trial
    "recruiting". The per-trial phase/status are authoritative structured fields
    (`trial_facts`, built from Evidence.extra), so contradictions are detected
    deterministically. Mirrors the constraint/tissue guards; records a
    ValidationFlag for Langfuse/HITL audit on activation.
    """
    if not trial_facts:
        return result
    verdicts = result.payload.get("lens_verdicts") or []
    if not verdicts:
        return result

    verdict = LensVerdict.model_validate(verdicts[0])
    facts = [TrialFact.model_validate(f) for f in trial_facts]

    guarded_rationale = apply_clinical_phase_guard(verdict.rationale, facts)
    guarded_narrative = apply_clinical_phase_guard(verdict.narrative, facts)
    axes = [
        ax.model_copy(update={"rationale": apply_clinical_phase_guard(ax.rationale, facts)})
        for ax in verdict.axes
    ]

    fired = any(
        "CLINICAL TRIAL GUARD" in t
        for t in (guarded_rationale, guarded_narrative, *(ax.rationale for ax in axes))
    )
    if not fired:
        return result

    flag = ValidationFlag(
        lens=lens,
        severity="medium",
        rule_id="clinical_trial_phase_guard",
        claim_excerpt="",
        message="Clinical trial guard activated: narrative/rationale misstated a trial's "
        "phase or recruitment status (e.g. conflating trials of different phases under one "
        "phase number, or calling a closed trial 'recruiting'); annotated rather than "
        "silently rewritten.",
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


def apply_tissue_relevance_guard_to_result(
    result: AgentMessage,
    top_tissues: list[str],
    disease_relevant_tissues: list[str],
    disease: str,
    *,
    lens: LensName,
) -> AgentMessage:
    """Post-LLM safety net shared by the biology and safety lenses.

    Annotates (never silently rewrites) verdict text that treats a high-bulk-TPM,
    non-disease tissue as disease-relevant — bulk GTEx TPM rank is not a relevance
    proxy. The pre-computed "Disease-tissue expression grounding" block already tells
    the LLM which tissue matters; this catches the cases where it still ranks by TPM.
    Records a ValidationFlag when it fires.
    """
    if not top_tissues or not disease_relevant_tissues:
        return result
    verdicts = result.payload.get("lens_verdicts") or []
    if not verdicts:
        return result

    verdict = LensVerdict.model_validate(verdicts[0])

    def _guard(text: str) -> str:
        return apply_tissue_relevance_guard(text, top_tissues, disease_relevant_tissues, disease)

    guarded_rationale = _guard(verdict.rationale)
    guarded_narrative = _guard(verdict.narrative)
    axes = [ax.model_copy(update={"rationale": _guard(ax.rationale)}) for ax in verdict.axes]

    fired = any(
        "TISSUE RELEVANCE GUARD" in t
        for t in (guarded_rationale, guarded_narrative, *(ax.rationale for ax in axes))
    )
    if not fired:
        return result

    flag = ValidationFlag(
        lens=lens,
        severity="medium",
        rule_id="tissue_relevance_guard",
        claim_excerpt="",
        message="Tissue relevance guard activated: verdict text tied a high-bulk-TPM, "
        "non-disease tissue to disease relevance; bulk GTEx TPM rank is not a proxy for "
        "disease relevance — annotated rather than silently rewritten.",
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


def apply_depmap_relevance_guard_to_result(
    result: AgentMessage,
    *,
    is_oncology_indication: bool,
    mean_chronos: float | None,
    dependency_fraction: float | None,
    is_common_essential: bool,
    lens: LensName = "biology",
) -> AgentMessage:
    """Post-LLM safety net for the biology lens's DepMap dependency block.

    Annotates (never silently rewrites) verdict text that still cites cancer-
    cell-line DepMap essentiality as supportive mechanism evidence when
    `depmap_is_uninformative` (constraint_interpret.py) says it carries no signal
    for this target — e.g. a non-oncology indication with no meaningful dependency.
    Records a ValidationFlag when it fires.
    """
    verdicts = result.payload.get("lens_verdicts") or []
    if not verdicts:
        return result

    verdict = LensVerdict.model_validate(verdicts[0])

    def _guard(text: str) -> str:
        return apply_depmap_relevance_guard(
            text,
            is_oncology_indication=is_oncology_indication,
            mean_chronos=mean_chronos,
            dependency_fraction=dependency_fraction,
            is_common_essential=is_common_essential,
        )

    guarded_rationale = _guard(verdict.rationale)
    guarded_narrative = _guard(verdict.narrative)
    axes = [ax.model_copy(update={"rationale": _guard(ax.rationale)}) for ax in verdict.axes]

    fired = any(
        "DEPMAP RELEVANCE GUARD" in t
        for t in (guarded_rationale, guarded_narrative, *(ax.rationale for ax in axes))
    )
    if not fired:
        return result

    flag = ValidationFlag(
        lens=lens,
        severity="medium",
        rule_id="depmap_relevance_guard",
        claim_excerpt="",
        message="DepMap relevance guard activated: verdict text cited cancer-cell-line "
        "DepMap essentiality as supportive mechanism evidence for a target where it "
        "carries no signal (non-oncology, no meaningful dependency); annotated rather "
        "than silently rewritten.",
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
