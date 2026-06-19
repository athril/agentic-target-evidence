# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claim extraction service — governed model-op.

Decomposes each screened Evidence document into atomic, typed CoreClaims.
Each claim carries claim_text, confidence, direction, and propagated provenance.

Governance: routes via ctx.model, emits a Langfuse span, and counts against
step_budget — exactly like any other model-op in the system.
"""

from __future__ import annotations

import json
import logging
import uuid
from uuid import UUID

from core.evidence_text import screenable_text as _generic_screenable_text
from core.json_utils import strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from core.telemetry.langfuse import span
from harness.context import RunContext
from harness.contract import ServiceContract
from schemas.evidence import (
    CoreClaim,
    Direction,
    Evidence,
    EvidenceType,
    LensTopic,
    Provenance,
)
from services.evidence.constraint_interpret import interpret_constraint as _interpret_constraint

logger = logging.getLogger(__name__)

CONTRACT = ServiceContract(
    name="claim_extraction",
    consumes={"target_gene", "disease", "direction"},
    produces={"extracted_claims"},
    max_loops=1,
    skills=["claim_extraction"],
)

_BATCH_SIZE = 5  # evidence items per LLM call — small to stay within local context
_MAX_CLAIMS_PER_DOC = 5

# Free-text literature types are the only ones that carry lens-routing topics; every
# other type routes deterministically by evidence_type, so topics are ignored there.
_LITERATURE_TYPES = frozenset(
    {
        EvidenceType.ARTICLE,
        EvidenceType.ABSTRACT,
        EvidenceType.BOOK,
        EvidenceType.CONFERENCE,
    }
)


def _parse_topics(raw: object) -> list[LensTopic]:
    """Validate an LLM-supplied topics list to known LensTopic values, deduped.

    Unknown strings are dropped rather than raising, so a sloppy extraction
    degrades to "no topic" (biology-only) instead of failing the whole claim.
    """
    if not isinstance(raw, list):
        return []
    seen: list[LensTopic] = []
    for item in raw:
        if isinstance(item, str) and item in LensTopic._value2member_map_:
            topic = LensTopic(item)
            if topic not in seen:
                seen.append(topic)
    return seen


_STRUCTURED_TYPES = frozenset(
    {
        EvidenceType.GENETICS,
        EvidenceType.CONSTRAINT,
        EvidenceType.OMICS,
        EvidenceType.EXPRESSION,
        EvidenceType.FUNCTIONAL_GENOMICS,
        EvidenceType.REGULATORY_ELEMENT,
    }
)

# Types that always get a deterministic claim, even when the LLM extracts nothing.
# Superset of _STRUCTURED_TYPES; clinical trials are deterministic for claim
# generation but keep the generic free-text path for LLM extraction (see _evidence_summary).
_DETERMINISTIC_TYPES = _STRUCTURED_TYPES | frozenset({EvidenceType.CLINICAL_TRIAL})


def _structured_text(ev: Evidence) -> str:
    """Synthesise a screenable text representation from structured database evidence."""
    x = ev.extra
    if ev.evidence_type == EvidenceType.GENETICS:
        # GWAS row (internal DB)
        if "pvalue" in x:
            return (
                f"GWAS hit: {ev.gene} associated with {ev.disease} "
                f"(p={x.get('pvalue')}, beta={x.get('beta')}, OR={x.get('odds_ratio')}, "
                f"study={x.get('study_id')}, lof_score={x.get('lof_score')}, "
                f"is_lof_intolerant={x.get('is_lof_intolerant')})"
            )
        # OpenTargets association
        if "overall_score" in x:
            parts = [
                f"Open Targets: {ev.gene}↔{ev.disease} overall_score={x.get('overall_score')}, "
                f"genetic_score={x.get('genetic_score')}, known_drugs_score={x.get('known_drugs_score')}, "
                f"tractability_sm={x.get('tractability_small_molecule')}, "
                f"tractability_ab={x.get('tractability_antibody')}",
            ]
            if x.get("assoc_text"):
                parts.append(str(x["assoc_text"])[:300])
            if x.get("tract_text"):
                parts.append(str(x["tract_text"])[:300])
            return " | ".join(parts)
        # Breadth-summary rows (GWAS/coloc): text stored in extra["summary"]
        if "summary" in x:
            return str(x["summary"])
    if ev.evidence_type == EvidenceType.CONSTRAINT:
        base = x.get("text") or ""

        # ClinVar bundle (discriminated by "pathogenic" list key)
        if "pathogenic" in x and isinstance(x.get("pathogenic"), list):
            pvars = (x.get("pathogenic") or [])[:3]
            lpvars = (x.get("likely_pathogenic") or [])[:3]
            details: list[str] = []
            for v in pvars:
                desc = v.get("hgvsp") or v.get("hgvsc") or v.get("variant_id", "")
                cons = v.get("major_consequence", "")
                stars = v.get("gold_stars")
                details.append(f"{desc} ({cons}, stars={stars})" if stars else f"{desc} ({cons})")
            for v in lpvars:
                desc = v.get("hgvsp") or v.get("hgvsc") or v.get("variant_id", "")
                cons = v.get("major_consequence", "")
                details.append(f"{desc} ({cons}, LP)")
            if details:
                return base + " Key variants: " + "; ".join(details)
            return base

        # LoF variant bundle (discriminated by "hc_lof_count" key)
        if "hc_lof_count" in x:
            rep = (x.get("reported_variants") or [])[:3]
            details = []
            for v in rep:
                desc = v.get("hgvsc") or v.get("variant_id", "")
                af = v.get("af")
                hom = v.get("homozygote_count") or 0
                af_str = f" AF={af:.1e}" if af is not None else ""
                hom_str = f" hom={hom}" if hom else ""
                details.append(f"{desc}{af_str}{hom_str}")
            extra = (" Top HC pLoF: " + "; ".join(details)) if details else ""
            return base + extra

        # Constraint bundle fallback
        return base or (
            f"gnomAD constraint: LOEUF={x.get('loeuf')}, pLI={x.get('pli')}, "
            f"pRec={x.get('p_rec')}, MOEUF={x.get('moeuf')}, mis_z={x.get('mis_z')}"
        )
    if ev.evidence_type in (EvidenceType.OMICS, EvidenceType.EXPRESSION):
        return (
            x.get("text")
            or x.get("description")
            or f"Expression data for {ev.gene}: {json.dumps({k: v for k, v in x.items() if k not in ('gene_id', 'disease_id')}, default=str)[:300]}"
        )
    if ev.evidence_type == EvidenceType.FUNCTIONAL_GENOMICS:
        return x.get("text") or x.get("description") or json.dumps(x, default=str)[:300]
    if ev.evidence_type == EvidenceType.REGULATORY_ELEMENT:
        return (
            x.get("text")
            or f"Regulatory-element coverage for {ev.gene}: {json.dumps({k: v for k, v in x.items() if k not in ('gene_id', 'disease_id')}, default=str)[:300]}"
        )
    return ""


def _evidence_summary(ev: Evidence) -> str:
    if ev.evidence_type in _STRUCTURED_TYPES:
        text = _structured_text(ev)
    else:
        text = _generic_screenable_text(ev)
    title = ev.extra.get("title", ev.source)[:120]
    # Clinical trials get a larger window: gene name often appears only in
    # eligibility text that is concatenated after the brief_summary.
    # Literature abstracts get a window covering ~p99 of observed abstract
    # lengths (measured 2559 across archived papers): structured RCT abstracts
    # (BACKGROUND/METHODS/FINDINGS/INTERPRETATION) put the actual outcome data
    # in the back half, past where a tighter cap would cut off.
    cap = 5000 if ev.evidence_type == EvidenceType.CLINICAL_TRIAL else 2500
    return json.dumps(
        {
            "evidence_id": str(ev.evidence_id),
            "evidence_type": ev.evidence_type.value,
            "source": ev.source,
            "title": title,
            "text": text[:cap],
        },
        ensure_ascii=False,
    )


def _parse_extraction(raw: str, source_evidences: list[Evidence]) -> list[dict]:
    """Parse LLM output into per-document claim lists; fall back on error."""
    try:
        data = json.loads(strip_json_fence(raw))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: return placeholder claims so the pipeline stays green.
    return [
        {
            "evidence_id": str(ev.evidence_id),
            "claims": [{"claim_text": "", "direction": "unspecified", "confidence": None}],
        }
        for ev in source_evidences
    ]


def _build_core_claim(
    claim: dict,
    source: Evidence,
    run_id: UUID,
    trace_id: str,
) -> CoreClaim | None:
    text = (claim.get("claim_text") or "").strip()
    if not text:
        return None
    raw_dir = claim.get("direction", "unspecified")
    direction = (
        Direction(raw_dir) if raw_dir in Direction._value2member_map_ else Direction.UNSPECIFIED
    )
    confidence = claim.get("confidence")
    if confidence is not None:
        confidence = float(max(0.0, min(1.0, confidence)))
    # Topics only route literature claims; ignore any tags on structured types. An
    # untagged (or junk-tagged) literature claim defaults to BIOLOGY so it is never
    # silently dropped from every lens — biology is the home for generic mechanism
    # claims and the safety net for unreliable extraction.
    if source.evidence_type in _LITERATURE_TYPES:
        topics = _parse_topics(claim.get("topics")) or [LensTopic.BIOLOGY]
    else:
        topics = []
    prov = Provenance(
        agent_name="services/evidence/claim_extraction",
        tool_name="llm_extraction",
        timestamp=source.provenance.timestamp,
        trace_id=trace_id,
    )
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=source.evidence_id,  # keep the link back to the source document
        run_id=run_id,
        gene=source.gene,
        gene_id=source.gene_id,
        disease=source.disease,
        disease_id=source.disease_id,
        direction=direction,
        population=source.population,
        evidence_type=source.evidence_type,
        claim_text=text,
        confidence=confidence,
        topics=topics,
        availability_date=source.availability_date,
        provenance=prov,
        classification=source.classification,
    )


def _trial_confidence(phase: str, status: str) -> float:
    """Map trial phase/status to a confidence score; floor 0.5."""
    p = phase.upper()
    s = status.upper()
    if any(tok in p for tok in ("PHASE 3", "PHASE III", "PHASE 4", "PHASE IV")):
        base = 0.8
    elif any(tok in p for tok in ("PHASE 2", "PHASE II")):
        base = 0.65
    else:
        base = 0.5
    if "COMPLETED" in s:
        base = min(1.0, base + 0.1)
    return max(0.5, base)


def structured_claims(ev: Evidence, run_id: UUID, trace_id: str) -> list[CoreClaim]:
    """Build CoreClaims deterministically from structured database evidence (B1).

    Covers OT association, ClinVar P/LP, and gnomAD constraint so that
    the genetics lens always has at least one claim to reason from when
    these structured records exist — independent of whether the LLM extraction
    succeeds. Free-text evidence types are left to the LLM pass.
    """
    x = ev.extra or {}
    prov = Provenance(
        agent_name="services/evidence/claim_extraction",
        tool_name="structured_extraction",
        timestamp=ev.provenance.timestamp,
        trace_id=trace_id,
    )

    def _make(text: str, confidence: float) -> CoreClaim:
        return CoreClaim(
            evidence_id=uuid.uuid4(),
            source_evidence_id=ev.evidence_id,
            run_id=run_id,
            gene=ev.gene,
            gene_id=ev.gene_id,
            disease=ev.disease,
            disease_id=ev.disease_id,
            direction=Direction.UNSPECIFIED,
            population=ev.population,
            evidence_type=ev.evidence_type,
            claim_text=text,
            confidence=float(max(0.0, min(1.0, confidence))),
            availability_date=ev.availability_date,
            provenance=prov,
            classification=ev.classification,
        )

    claims: list[CoreClaim] = []

    if ev.evidence_type == EvidenceType.GENETICS:
        gs = x.get("genetic_score")
        if isinstance(gs, (int, float)) and gs >= 0.5:
            strength = "strong" if gs >= 0.7 else "moderate"
            text = (
                f"OpenTargets genetic association {ev.gene}↔{ev.disease_id} "
                f"score={gs:.3f} ({strength})"
            )
            claims.append(_make(text, float(gs)))
        elif "gwas_pvalue" in x or "diseases_score" in x:
            pvalue = x.get("gwas_pvalue")
            score = x.get("diseases_score")
            disease_name = x.get("disease_name") or ev.disease
            sources = ", ".join(x.get("edge_sources") or [])
            if pvalue is not None:
                confidence = 0.9 if pvalue < 5e-8 else 0.6
                text = (
                    f"SPOKE knowledge graph: {ev.gene}↔{disease_name} GWAS association "
                    f"(p={pvalue:.2e}, source={sources or 'GWAS'})"
                )
            else:
                confidence = float(max(0.0, min(1.0, (score or 0.0) / 10.0)))
                text = (
                    f"SPOKE knowledge graph: {ev.gene}↔{disease_name} association "
                    f"(DISEASES score={score}, source={sources or 'DISEASES'})"
                )
            claims.append(_make(text, confidence))
        elif "summary" in x:
            summary = str(x["summary"])[:400]
            if summary:
                claims.append(_make(summary, 0.5))

    elif ev.evidence_type == EvidenceType.CONSTRAINT:
        if ("loeuf" in x or "pli" in x) and "pathogenic" not in x:
            reading = _interpret_constraint(
                gene_symbol=ev.gene or "unknown",
                loeuf=x.get("loeuf"),
                pli=x.get("pli"),
                mis_z=x.get("mis_z"),
                moeuf=x.get("moeuf"),
            )
            if reading.summary_text:
                claims.append(
                    _make(
                        reading.summary_text,
                        0.9 if reading.is_lof_constrained else 0.75,
                    )
                )
        elif "pathogenic" in x:
            p_list = x.get("pathogenic") or []
            lp_list = x.get("likely_pathogenic") or []
            total = len(p_list) + len(lp_list)
            if total > 0:
                text = (
                    f"{total} Pathogenic/Likely-Pathogenic ClinVar variant(s) in {ev.gene} "
                    f"({len(p_list)} P, {len(lp_list)} LP)"
                )
                high_star = any((v.get("gold_stars") or 0) >= 1 for v in p_list[:5])
                claims.append(_make(text, 0.85 if high_star else 0.6))

    elif ev.evidence_type == EvidenceType.CLINICAL_TRIAL:
        phase = (x.get("phase") or "").strip()
        status = (x.get("status") or "").strip()
        interventions = ", ".join(x.get("interventions") or []) or "—"
        conditions = ", ".join(x.get("conditions") or []) or ev.disease
        enrollment = x.get("enrollment")
        text = (
            f"{phase or 'Trial'} {status} clinical trial {ev.source}: "
            f"{interventions} in {conditions}" + (f" (n={enrollment})" if enrollment else "")
        )
        conf = _trial_confidence(phase, status)
        claims.append(_make(text, conf))

    return claims


async def extract_claims(
    evidences: list[Evidence],
    target_gene: str,
    disease: str,
    direction: str,
    ctx: RunContext,
) -> list[CoreClaim]:
    """Extract atomic CoreClaims from screened evidence via LLM.

    Each evidence document may yield 1-N atomic claims. Claims with empty
    claim_text are discarded. The returned list is empty if evidences is empty.
    """
    if not evidences:
        return []

    skill_text = ctx.load_skill("claim_extraction")
    classification = classify(evidences)
    provider, _model = ctx.router.select(classification, "claim_extraction")

    all_claims: list[CoreClaim] = []
    run_id = evidences[0].run_id
    trace_id = ctx.trace_id

    for i in range(0, len(evidences), _BATCH_SIZE):
        batch = evidences[i : i + _BATCH_SIZE]
        batch_num = i // _BATCH_SIZE + 1

        # B1: Build deterministic claims for structured/deterministic types first so
        # the genetics and clinical lenses always have signal even when LLM extraction fails.
        det_claims: list[CoreClaim] = []
        for ev in batch:
            if ev.evidence_type in _DETERMINISTIC_TYPES:
                det_claims.extend(structured_claims(ev, run_id, trace_id))
        all_claims.extend(det_claims)

        docs_json = "[\n" + ",\n".join(_evidence_summary(e) for e in batch) + "\n]"

        user_content = (
            f"Gene: {target_gene}\n"
            f"Disease: {disease}\n"
            f"Therapeutic direction context: {direction}\n\n"
            f"Evidence documents:\n{docs_json}\n\n"
            f"Extract up to {_MAX_CLAIMS_PER_DOC} atomic claims per document."
        )

        struct_docs_in_batch = sum(1 for ev in batch if ev.evidence_type in _DETERMINISTIC_TYPES)

        async with span(
            "claim_extraction:batch",
            trace_id=trace_id,
            input_data=f"batch {batch_num}, {len(batch)} docs",
        ) as s:
            completion = await provider.complete(
                CompletionRequest(
                    messages=[{"role": "user", "content": user_content}],
                    system=skill_text,
                    classification=classification,
                    task="claim_extraction",
                    model_override=_model,
                )
            )
            extraction = _parse_extraction(completion.content, batch)
            llm_claim_count = sum(len(d.get("claims", [])) for d in extraction)

            # B3: Loss guard — log when LLM produces nothing for structured docs.
            if struct_docs_in_batch > 0 and llm_claim_count == 0:
                logger.warning(
                    "claim_extraction batch %d: LLM returned 0 claims for %d structured doc(s) "
                    "— deterministic fallback covers them",
                    batch_num,
                    struct_docs_in_batch,
                )
                s.set_attribute("extraction.dropped_structured", struct_docs_in_batch)

            s.set_attribute(
                "gen_ai.completion",
                f"{llm_claim_count} LLM + {len(det_claims)} structured claims from {len(batch)} docs",
            )

        evidence_by_id = {str(ev.evidence_id): ev for ev in batch}
        for doc in extraction:
            source_ev = evidence_by_id.get(doc.get("evidence_id", ""))
            if source_ev is None:
                continue
            for claim_dict in (doc.get("claims") or [])[:_MAX_CLAIMS_PER_DOC]:
                claim = _build_core_claim(claim_dict, source_ev, run_id, trace_id)
                if claim is not None:
                    all_claims.append(claim)

    return all_claims
