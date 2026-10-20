# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The split safety lens: three typed questions, a verdict computed in code, then prose.

    facts and claims ──► liability_resolution ─┐
                         human_harm            ├─► safety_axes (code) ─► verdict
                         tissue                ┘                            │
    verdict and facts, fixed ──► narrative call (prose only) ───────────────┤
    the same checks every lens meets (finish_verdict) ◄─────────────────────┘

It produces the same ``LensVerdict`` (axes ``toxicity`` and ``tissue_specificity``) as the
single-call lens, so scoring, reconciliation, reports and the cache key shape are
unchanged; it adds only ``question_results``, the audit trail of what each question was
asked and answered. A lens must never disappear because a sub-question failed: when one
stays unanswered the caller falls back to the single-call path (``SplitUnavailable``).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any, cast

from agents.interpretation._lens_base import (
    LENS_EVIDENCE_TYPES,
    _deserialise_claims,
    _direction_enum,
    _filter_claims,
    _shown_claims,
    as_literature_index,
    finish_verdict,
    shown_input_counts,
)
from agents.interpretation._lens_questions import (
    QuestionResult,
    ask,
    question_digest,
)
from agents.interpretation.safety_lens import questions as q
from agents.interpretation.safety_lens.aggregate import (
    AGGREGATION_VERSION,
    safety_axes,
    settle_by_labels,
)
from core.json_utils import close_truncated, loads_recovering, strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from harness.context import RunContext
from schemas.evidence import DataClass, Evidence
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag
from services.decision.suitability import load_scoring_config
from services.evidence.constraint_interpret import interpret_expression_context
from services.evidence.guard_rules import GuardFacts, GuardFamily
from services.evidence.liabilities import DIRECTION_HARM_SYSTEM

logger = logging.getLogger(__name__)

NARRATIVE_SKILL = "safety_narrative"
_NARRATIVE_CLAIMS = 30
_CLAIM_CHARS = 200
# HPA specificity categories that mean enrichment in some tissue.
_HPA_ENRICHED = frozenset(
    {
        "tissue enhanced",
        "group enhanced",
        "cell type enhanced",
        "tissue enriched",
        "group enriched",
        "tissue specific",
        "cell type specific",
    }
)


class SplitUnavailable(Exception):
    """A question stayed unanswered; the caller falls back to the single-call lens."""

    def __init__(self, question_ids: list[str]) -> None:
        super().__init__(f"unanswered: {', '.join(question_ids)}")
        self.question_ids = question_ids


def split_digest() -> str:
    """Everything the split path adds to the lens cache key: question skills and answer
    schemas, the narrative skill and the aggregation rules' version."""
    return f"{question_digest(q.SAFETY_QUESTIONS, (NARRATIVE_SKILL,))}:{AGGREGATION_VERSION}"


def split_fallback_flag(question_ids: list[str]) -> ValidationFlag:
    return ValidationFlag(
        lens="safety",
        severity="medium",
        rule_id="split_lens.fallback",
        claim_excerpt="",
        message=(
            f"The split safety lens could not answer {', '.join(question_ids)}; this verdict "
            "comes from the single-call safety lens."
        ),
    )


def _direction_harms(spec: dict[str, Any], liabilities: list[dict[str, Any]]) -> bool:
    """Whether the evidence says the requested change worsens the disease.

    The direction-of-effect reading must *contradict* the requested direction with the
    strength the score gate uses; a "worsens the disease" liability alone exists as soon
    as one label warns of it, even when approved drugs of the same direction outweigh the
    warning (the reading is then "conflicting"). Without a reading (older callers) the
    liability decides.
    """
    reading = spec.get("direction_reading")
    if reading:
        return bool(
            reading.get("verdict") == "contradicts"
            and float(reading.get("against") or 0.0)
            >= load_scoring_config().direction_gate.get("min_against", 0.5)
        )
    return any(li.get("system") == DIRECTION_HARM_SYSTEM for li in liabilities)


def _disease_tissue_rank(spec: dict[str, Any]) -> int | None:
    ranks: dict[str, int] = spec.get("gtex_tissue_ranks") or {}
    found = [ranks[t] for t in spec.get("disease_relevant_tissues") or [] if t in ranks]
    return min(found) if found else None


def _liability_table(liabilities: list[dict[str, Any]], statuses: list[q.LiabilityStatus]) -> str:
    by_index = {s.index: s for s in statuses}
    lines = []
    for i, li in enumerate(liabilities, 1):
        s = by_index.get(i)
        status = s.status.replace("_", " ") if s else "unresolved"
        note = f" — {s.note}" if s and s.note else ""
        lines.append(
            f"{i}. {li.get('system')} [{li.get('severity')}; {li.get('source')}]: "
            f"{'; '.join(str(e) for e in li.get('evidence') or [])} — {status}{note}"
        )
    return "\n".join(lines) or "(no candidate liabilities)"


def _templated_prose(
    axes: list[AxisVerdict], overall: str, table: str, harm: q.HarmAnswer | None
) -> tuple[str, str]:
    """The prose when the narrative call fails: the axis rationales and the facts, so the
    lens is never left without an explanation."""
    rationale = " ".join(a.rationale for a in axes)
    events = (
        "; ".join(e.description for e in harm.events) if harm is not None and harm.events else ""
    )
    narrative = (
        f"Overall safety verdict: {overall}.\n\n"
        f"Candidate on-target liabilities and what human data showed:\n{table}\n\n"
        + (f"Harm reported in people: {events}.\n\n" if events else "")
        + rationale
    )
    return rationale, narrative


# The narrative reply (rationale + a few paragraphs) is about 600 tokens; a cap keeps a
# model that keeps writing from filling the window (two calls once stopped at 4,096).
_NARRATIVE_MAX_TOKENS = 1536
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def _parse_prose(raw: str) -> tuple[str, str] | None:
    """(rationale, narrative) from the reply. A reply cut off at its cap keeps its
    rationale and the narrative up to its last complete sentence."""
    salvaged = False
    try:
        data = loads_recovering(strip_json_fence(raw))
    except (ValueError, TypeError):
        closed = close_truncated(raw)
        if closed is None:
            return None
        data, salvaged = json.loads(closed, strict=False), True
    if not isinstance(data, dict):
        return None
    rationale, narrative = data.get("rationale"), data.get("narrative")
    if not (isinstance(rationale, str) and rationale.strip()):
        return None
    if not isinstance(narrative, str):
        if not salvaged:
            return None
        narrative = ""  # cut off before the narrative began
    if salvaged:
        ends = [m.end() for m in _SENTENCE_END.finditer(narrative)]
        narrative = narrative[: ends[-1]] if ends else ""
    return rationale.strip(), narrative.strip()


async def _narrate(
    ctx: RunContext,
    classification: DataClass,
    head: str,
    attempts: int = 2,
) -> tuple[str, str] | None:
    skill = ctx.load_skill(NARRATIVE_SKILL)
    provider, model = ctx.select_model(classification, "safety_lens")
    messages: list[dict[str, Any]] = [{"role": "user", "content": head}]
    for _ in range(attempts):
        completion = await provider.complete(
            CompletionRequest(
                messages=messages,
                system=skill,
                classification=classification,
                task="safety_lens",
                model_override=model,
                max_tokens=_NARRATIVE_MAX_TOKENS,
            )
        )
        parsed = _parse_prose(completion.content)
        if parsed is not None:
            return parsed
        messages = [
            *messages,
            {"role": "assistant", "content": completion.content},
            {
                "role": "user",
                "content": (
                    'Return only a JSON object with the string keys "rationale" and '
                    '"narrative". No prose before or after it, no code fence.'
                ),
            },
        ]
    return None


async def run_split_safety(
    msg: AgentMessage,
    ctx: RunContext,
    *,
    extra_context: str,
    guards: tuple[GuardFacts, tuple[GuardFamily, ...]],
    rules: Callable[[LensVerdict], LensVerdict] | None = None,
) -> AgentMessage:
    """Answer the three questions, compute the verdict, write the prose and run the checks.

    ``rules`` are the lens's deterministic verdict rules (e.g. common essentiality); they
    run on the computed verdict before the narrative call, so the prose describes the
    verdict as published — applied afterwards, the narrative said "the overall verdict
    is support" above a neutral verdict.

    Raises `SplitUnavailable` when a question stays unanswered after its retry.
    """
    spec = msg.task_spec or {}
    target_gene = spec.get("target_gene", "unknown")
    disease = spec.get("disease", "unknown")
    direction = spec.get("direction") or "unspecified"
    disease_classes = frozenset(spec.get("disease_classes") or ())
    quality_map = spec.get("source_quality") or {}
    papers = as_literature_index(spec.get("literature_index"))
    relevant = _filter_claims(
        _deserialise_claims(spec.get("extracted_claims") or []),
        LENS_EVIDENCE_TYPES["safety"],
        "safety",
    )
    classification = (
        classify(cast("list[Evidence]", relevant)) if relevant else DataClass.NON_SENSITIVE
    )
    liabilities: list[dict[str, Any]] = list(spec.get("candidate_liabilities") or [])
    precedent: dict[str, Any] = spec.get("drug_precedent") or {}
    human_exposure = bool(spec.get("human_exposure"))

    disease_tissue = spec.get("disease_tissue") or spec.get("tissue") or "disease tissue"
    caveat = interpret_expression_context(
        spec.get("bulk_tpm"),
        spec.get("hpa_specificity") or "",
        disease_tissue,
        spec.get("disease_cell_types") or (),
    )
    tissue_text, named_tissues = q.tissue_facts(spec, caveat)
    facts = {
        q.LIABILITIES.id: q.liability_facts(
            liabilities,
            human_exposure=human_exposure,
            approved=[str(d).lower() for d in precedent.get("concordant_approved") or []],
        ),
        q.HUMAN_HARM.id: q.harm_facts(
            str(direction), precedent, list(spec.get("harm_trials") or [])
        ),
        q.TISSUE.id: tissue_text,
    }
    candidates = {
        q.LIABILITIES.id: q.liability_claims(relevant),
        q.HUMAN_HARM.id: q.harm_claims(relevant),
        q.TISSUE.id: q.tissue_claims(relevant),
    }

    results: dict[str, QuestionResult] = {}
    for question in q.SAFETY_QUESTIONS:
        results[question.id] = await ask(
            question,
            facts=facts[question.id],
            claims=candidates[question.id],
            quality_map=quality_map,
            disease_classes=disease_classes,
            lens="safety",
            ctx=ctx,
            classification=classification,
            papers=papers,
        )
    unanswered = [rid for rid, r in results.items() if r.answer is None]
    if unanswered:
        raise SplitUnavailable(unanswered)

    liability_statuses, liability_notes = q.normalise_liabilities(
        results[q.LIABILITIES.id].answer, len(liabilities)
    )
    claims_of: dict[str, list[str]] = {}
    for c in relevant:
        if c.source_evidence_id:
            claims_of.setdefault(str(c.source_evidence_id), []).append(str(c.evidence_id))
    approved_labels = [
        {**lab, "claim_ids": claims_of.get(str(lab.get("evidence_id") or ""), [])}
        for lab in spec.get("approved_label_warnings") or []
    ]
    liability_statuses, label_notes = settle_by_labels(
        liabilities, liability_statuses, approved_labels
    )
    liability_notes = [*liability_notes, *label_notes]
    harm, harm_notes = q.normalise_harm(results[q.HUMAN_HARM.id].answer)
    tissue, tissue_notes = q.normalise_tissue(results[q.TISSUE.id].answer, named_tissues)
    normalised: dict[str, tuple[dict[str, Any], list[str]]] = {
        q.LIABILITIES.id: (
            {"liabilities": [s.model_dump() for s in liability_statuses]},
            liability_notes,
        ),
        q.HUMAN_HARM.id: (harm.model_dump() if harm else {}, harm_notes),
        q.TISSUE.id: (tissue.model_dump() if tissue else {}, tissue_notes),
    }

    axes, overall = safety_axes(
        liabilities,
        q.LiabilityAnswer(liabilities=liability_statuses),
        harm,
        tissue,
        human_exposure=human_exposure,
        direction_harms=_direction_harms(spec, liabilities),
        approved_here=precedent.get("status") == "validated",
        disease_tissue_rank=_disease_tissue_rank(spec),
        hpa_enriched=str(spec.get("hpa_specificity") or "").lower() in _HPA_ENRICHED,
    )

    rule_flags: list[ValidationFlag] = []
    if rules is not None:
        ruled = rules(
            LensVerdict(
                run_id=msg.run_id,
                trace_id=msg.trace_id,
                lens="safety",
                target_gene=target_gene,
                disease=disease,
                direction=_direction_enum(str(direction)),
                overall_verdict=overall,
                axes=axes,
            )
        )
        axes, overall = list(ruled.axes), ruled.overall_verdict
        rule_flags = list(ruled.validation_flags)

    table = _liability_table(liabilities, liability_statuses)
    cited = {cid for a in axes for cid in a.supporting_claim_ids}
    cited_claims = [c for c in relevant if str(c.evidence_id) in cited][:_NARRATIVE_CLAIMS]
    constraint = (spec.get("constraint_reading") or {}).get("summary_text")
    narrative_input = "\n".join(
        [
            f"Gene: {target_gene}",
            f"Disease: {disease}",
            f"Therapeutic direction: {direction}",
            "",
            f"Computed overall verdict (fixed): {overall}",
            *(
                f"Axis {a.axis} (fixed): "
                f"{ {True: 'favourable', False: 'unfavourable', None: 'uncertain'}[a.verdict] } — {a.rationale}"
                for a in axes
            ),
            "",
            "Candidate on-target liabilities and what human data showed:",
            table,
            "",
            "Harm in people in the requested direction: "
            + (
                "; ".join(e.description for e in harm.events)
                if harm is not None and harm.events
                else "none reported in the claims"
            ),
            "",
            "Expression facts:",
            facts[q.TISSUE.id],
            (
                f"Tissues where expression could cause on-target effects: "
                f"{', '.join(tissue.off_target_tissues_of_concern)}"
                if tissue is not None and tissue.off_target_tissues_of_concern
                else ""
            ),
            f"Constraint interpretation (pre-computed): {constraint}" if constraint else "",
            "",
            "Claims cited:",
            *(f"- {c.claim_text[:_CLAIM_CHARS]}" for c in cited_claims),
            "",
            "Return the JSON object with the keys rationale and narrative.",
        ]
    )
    prose = await _narrate(ctx, classification, narrative_input)
    if prose is None:
        logger.warning("safety_lens: narrative call unusable — using the templated prose")
        prose = _templated_prose(axes, overall, table, harm)
    rationale, narrative = prose

    verdict = LensVerdict(
        run_id=msg.run_id,
        trace_id=msg.trace_id,
        lens="safety",
        target_gene=target_gene,
        disease=disease,
        direction=_direction_enum(str(direction)),
        overall_verdict=overall,
        confidence=0.0,
        axes=axes,
        rationale=rationale,
        narrative=narrative,
        validation_flags=rule_flags,
        question_results=[
            {
                **results[question.id].model_dump(),
                "answer": normalised[question.id][0],
                "notes": normalised[question.id][1],
                "facts": facts[question.id],
            }
            for question in q.SAFETY_QUESTIONS
        ],
    )
    asked = {cid for r in results.values() for cid in r.shown_claim_ids}
    # What the checks and the scorer treat as shown: every claim a question displayed, plus
    # the strongest claims the single-call lens would have shown (structured records are
    # attached to an axis by config, so they count toward it as they always did).
    pool = {
        str(c.evidence_id): c
        for c in [
            *(c for c in relevant if str(c.evidence_id) in asked),
            *_shown_claims(relevant, quality_map, disease_classes, "safety", papers=papers),
        ]
    }
    verdict = await finish_verdict(
        verdict,
        list(pool.values()),
        spec=spec,
        extra_context=extra_context,
        ctx=ctx,
        classification=classification,
        lens="safety",
        guards=guards,
    )
    verdict = verdict.model_copy(
        update={"shown_inputs": shown_input_counts(list(pool.values()), "safety")}
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


__all__ = [
    "SplitUnavailable",
    "run_split_safety",
    "split_digest",
    "split_fallback_flag",
]
