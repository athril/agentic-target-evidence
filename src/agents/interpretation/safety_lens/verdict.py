# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The safety lens: a verdict computed in code from the gene's findings, then prose.

    findings ──► statuses (where people saw them) ──► safety_axes (code) ─► verdict
    verdict and facts, fixed ──► narrative call (prose only) ──────────────────┤
    the same checks every lens meets (finish_verdict) ◄────────────────────────┘

The single call judged a 13k-token prompt over its budget, and its verdict could argue
the other direction or contradict its own axes; three question calls judged text-typed
claims over one-sided inputs. The verdict now comes from the structured findings alone
(``aggregate.py``), and one model call writes the prose around it. A lens never
disappears: when that call fails, the prose is templated from the facts.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
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
from agents.interpretation.safety_lens.aggregate import (
    AGGREGATION_VERSION,
    LiabilityStatus,
    finding_statuses,
    harm_events,
    safety_axes,
    status_label,
)
from core.json_utils import close_truncated, loads_recovering, strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from harness.context import RunContext
from schemas.evidence import DataClass, Evidence
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict, counted_by_gate_flag
from services.evidence.constraint_interpret import interpret_expression_context
from services.evidence.disease_class import DiseaseClass
from services.evidence.guard_rules import GuardFacts, GuardFamily
from services.evidence.liabilities import human_knockouts, is_genetic_harm
from services.evidence.site_of_action import site_of_action_note

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


def verdict_digest() -> str:
    """What the lens cache key adds for the computed verdict: the aggregation rules'
    version (the narrative skill is the key's skill prompt)."""
    return f"safety-aggregation:{AGGREGATION_VERSION}"


def _disease_tissue_rank(spec: dict[str, Any]) -> int | None:
    ranks: dict[str, int] = spec.get("gtex_tissue_ranks") or {}
    found = [ranks[t] for t in spec.get("disease_relevant_tissues") or [] if t in ranks]
    return min(found) if found else None


def finding_table(liabilities: list[dict[str, Any]], statuses: list[LiabilityStatus]) -> str:
    by_index = {s.index: s for s in statuses}
    lines = []
    for i, li in enumerate(liabilities, 1):
        s = by_index.get(i)
        status = status_label(s.status, s.note) if s else "unresolved"
        note = f" — {s.note}" if s and s.note else ""
        lines.append(
            f"{i}. {li.get('system')} [{li.get('severity')}; {li.get('source')}]: "
            f"{'; '.join(str(e) for e in li.get('evidence') or [])} — {status}{note}"
        )
    return "\n".join(lines) or "(no candidate liabilities)"


def tissue_facts(spec: dict[str, Any], caveat: str) -> str:
    """The expression facts the prose may state."""
    ranks: dict[str, int] = spec.get("gtex_tissue_ranks") or {}
    top = list(spec.get("top_tpm_tissues") or [])
    lines = [f"Disease tissue: {spec.get('disease_tissue') or 'not resolved'}"]
    cells = list(spec.get("disease_cell_types") or [])
    if cells:
        lines.append(f"Disease cell types: {', '.join(cells)}")
    if spec.get("bulk_tpm") is not None:
        rank = ranks.get(str(spec.get("disease_tissue") or ""))
        where = f", rank {rank} of {len(ranks)}" if rank and ranks else ""
        lines.append(f"Bulk TPM in the disease tissue: {float(spec['bulk_tpm']):.2f}{where}")
    if spec.get("hpa_specificity"):
        lines.append(f"HPA specificity: {spec['hpa_specificity']}")
    if top:
        lines.append(f"Tissues with the highest bulk TPM: {', '.join(top)}")
    if spec.get("disease_tissue_expression_note"):
        lines.append(str(spec["disease_tissue_expression_note"]))
    if caveat:
        lines.append(caveat)
    if spec.get("secreted"):
        lines.append(
            site_of_action_note(
                str(spec.get("target_gene") or "The target"),
                str(spec.get("disease_tissue") or ""),
                top,
                lens="safety",
            )
        )
    return "\n".join(lines)


def _templated_prose(
    axes: list[AxisVerdict], overall: str, table: str, events: list[str], elsewhere: list[str]
) -> tuple[str, str]:
    """The prose when the narrative call fails: the axis rationales and the facts, so the
    lens is never left without an explanation."""
    rationale = " ".join(a.rationale for a in axes)
    narrative = (
        f"Overall safety verdict: {overall}.\n\n"
        f"Candidate on-target liabilities and what human data showed:\n{table}\n\n"
        + (f"Harm in people in this indication: {'; '.join(events)}.\n\n" if events else "")
        + (
            f"Safety stops in other indications, listed and not counted: "
            f"{'; '.join(elsewhere)}.\n\n"
            if elsewhere
            else ""
        )
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


async def run_safety(
    msg: AgentMessage,
    ctx: RunContext,
    *,
    extra_context: str,
    guards: tuple[GuardFacts, tuple[GuardFamily, ...]],
) -> AgentMessage:
    """Compute the verdict from the findings, write the prose and run the checks."""
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
    approved_here = precedent.get("status") == "validated"
    profile = spec.get("dependency_profile") or {}

    claims_of: dict[str, list[str]] = {}
    for c in relevant:
        if c.source_evidence_id:
            claims_of.setdefault(str(c.source_evidence_id), []).append(str(c.evidence_id))
    approved_labels = [
        {**lab, "claim_ids": claims_of.get(str(lab.get("evidence_id") or ""), [])}
        for lab in spec.get("approved_label_warnings") or []
    ]
    statuses, notes = finding_statuses(
        liabilities,
        approved_labels,
        approved_here=approved_here,
        human_knockouts=human_knockouts(spec.get("constraint_reading"), str(direction)),
    )
    axes, overall, rule = safety_axes(
        liabilities,
        statuses,
        human_exposure=bool(spec.get("human_exposure")),
        approved_here=approved_here,
        assessed=bool(spec.get("liability_assessed", True)),
        direction=str(direction),
        common_essential=profile.get("kind") == "common_essential",
        oncology=DiseaseClass.ONCOLOGY.value in disease_classes,
        disease_tissue_rank=_disease_tissue_rank(spec),
        hpa_enriched=str(spec.get("hpa_specificity") or "").lower() in _HPA_ENRICHED,
    )
    logger.info("safety_lens: %s by rule %d (%s)", overall, rule, "; ".join(notes) or "-")

    events = harm_events(liabilities)
    # Opposed on human genetics alone (rule 1 with no other harm in people): the lens says
    # so, and the score counts the direction of effect once, through its gate.
    from_genetics = (
        overall == "oppose"
        and rule == 1
        and not harm_events([li for li in liabilities if not is_genetic_harm(li)])
    )
    elsewhere = [str(s) for s in spec.get("safety_stops_elsewhere") or []]
    table = finding_table(liabilities, statuses)
    disease_tissue = spec.get("disease_tissue") or spec.get("tissue") or "disease tissue"
    caveat = interpret_expression_context(
        spec.get("bulk_tpm"),
        spec.get("hpa_specificity") or "",
        disease_tissue,
        spec.get("disease_cell_types") or (),
    )
    cited = {cid for a in axes for cid in a.supporting_claim_ids}
    cited_claims = [c for c in relevant if str(c.evidence_id) in cited]
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
            "Harm in people in this indication: "
            + ("; ".join(events) if events else "none in the structured data"),
            (
                f"Safety stops in other indications (listed, not counted): {'; '.join(elsewhere)}"
                if elsewhere
                else ""
            ),
            "",
            "Expression facts:",
            tissue_facts(spec, caveat),
            f"Constraint interpretation (pre-computed): {constraint}" if constraint else "",
            "",
            "Claims cited:",
            *(f"- {c.claim_text[:_CLAIM_CHARS]}" for c in cited_claims[:_NARRATIVE_CLAIMS]),
            "",
            "Return the JSON object with the keys rationale and narrative.",
        ]
    )
    prose = await _narrate(ctx, classification, narrative_input)
    if prose is None:
        logger.warning("safety_lens: narrative call unusable — using the templated prose")
        prose = _templated_prose(axes, overall, table, events, elsewhere)
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
        validation_flags=[
            counted_by_gate_flag(
                "safety", "Oppose rests on human genetics against the requested change alone."
            )
        ]
        if from_genetics
        else [],
        liability_statuses=[
            {
                **s.model_dump(),
                "system": str(li.get("system") or ""),
                "source": str(li.get("source") or ""),
                "severity": str(li.get("severity") or ""),
                "seen": str(li.get("seen") or ""),
            }
            for s, li in zip(statuses, liabilities, strict=True)
        ],
    )
    # What the checks and the scorer treat as shown: the claims the verdict cites, plus
    # the strongest claims the lens would have shown (structured records are attached to
    # an axis by config, so they count toward it as they always did).
    pool = {
        str(c.evidence_id): c
        for c in [
            *cited_claims,
            *_shown_claims(relevant, quality_map, disease_classes, "safety", papers=papers),
        ]
    }
    verdict = await finish_verdict(
        verdict,
        list(pool.values()),
        spec=spec,
        extra_context=f"{extra_context}\n{table}",
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


__all__ = ["NARRATIVE_SKILL", "run_safety", "verdict_digest"]
