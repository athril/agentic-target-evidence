# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ExperimentAgent.

Receives lens verdicts and candidate liabilities (via task_spec) and screened evidence
(payload). Produces a target-suitability result: score 0-100, recommendation band,
score breakdown, rationale and supporting evidence IDs.

The score is computed deterministically (services/decision/suitability.py); the LLM
writes only the rationale explaining it, so the same lens verdicts always give the same
score and liabilities always count.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from agents.synthesis.experiment.contract import CONTRACT
from core.json_utils import loads_recovering, strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import DataClass, Evidence
from schemas.messages import AgentMessage
from services.decision.suitability import SuitabilityScore, compute_suitability
from services.evidence.constraint_interpret import (
    mendelian_grade_from_signals,
)

_SYSTEM_PROMPT = """You are a drug target validation scientist explaining a target-suitability score.

You are given interpretation verdicts from six lenses (genetics, biology, safety, clinical, commercial,
regulatory), the candidate on-target liabilities, and a suitability score that has ALREADY been
computed deterministically from them. Do not change it or state a different score.

Write a 2-4 sentence rationale that explains the score: name the lenses that raise it, the lenses or
liabilities that lower it, and the single most important open question for this gene in this disease.
Refer only to evidence in the lens verdicts and the liability list.

Output a JSON array with one object:
[{"target": "<gene_name>", "rationale": "<2-4 sentences>", "supporting_evidence_ids": ["<uuid>", ...]}]
Output ONLY the JSON array. No prose or markdown fences."""

logger = logging.getLogger(__name__)


def _lens_summary(lens_summaries: list[dict[str, Any]]) -> str:
    if not lens_summaries:
        return "No lens analysis available."
    lines = []
    for lv in lens_summaries:
        lens = lv.get("lens", "?")
        ov = lv.get("overall_verdict", "?")
        conf = lv.get("confidence", 0.0)
        rationale = lv.get("rationale", "")
        narrative = lv.get("narrative", "")
        # Include full narrative if present, otherwise just the rationale
        detail = narrative if narrative else rationale
        lines.append(f"[{lens}] {ov} (conf={conf:.2f}): {detail}")
    return "\n\n".join(lines)


def _score_text(s: SuitabilityScore) -> str:
    rows = "\n".join(
        f"  {c.lens}: {c.verdict} (confidence {c.confidence:.2f}, weight {c.weight:.2f}) → "
        f"{c.points:+.1f}"
        for c in s.contributions
    )
    notes = "".join(f"\n  note: {n}" for n in s.notes)
    return (
        f"Computed suitability score: {s.score}/100 ({s.band}); evidence score "
        f"{s.evidence_score:.1f}, liability penalty −{s.liability_penalty:.1f}.\n"
        f"Contributions:\n{rows}{notes}"
    )


def _liability_text(liabilities: list[dict[str, Any]]) -> str:
    if not liabilities:
        return "Candidate on-target liabilities: none found in the structured data."
    return "Candidate on-target liabilities:\n" + "\n".join(
        f"  - {li.get('system')} [{li.get('severity')}; {li.get('source')}]: "
        f"{'; '.join(li.get('evidence') or [])}"
        for li in liabilities
    )


def _fallback_rationale(s: SuitabilityScore) -> str:
    """Deterministic rationale when the LLM returns nothing usable."""
    up = [c.lens for c in s.contributions if c.points > 0]
    down = [c.lens for c in s.contributions if c.points < 0]
    parts = [f"Score {s.score}/100 ({s.band})."]
    if up:
        parts.append(f"Raised by {', '.join(up)}.")
    if down:
        parts.append(f"Lowered by {', '.join(down)}.")
    parts.extend(f"{n[0].upper()}{n[1:]}." for n in s.notes)
    return " ".join(parts)


def _parse_results(raw: str) -> list[dict[str, Any]]:
    """The model's result list; a bare object (the usual small-model slip) or JSON wrapped
    in prose is accepted too."""
    text = strip_json_fence(raw)
    try:
        data = loads_recovering(text)
    except (json.JSONDecodeError, ValueError):
        start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
        end = max(text.rfind("]"), text.rfind("}"))
        if start < 0 or end <= start:
            return []
        try:
            data = loads_recovering(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(data, dict):
        data = data.get("experiment_results") or data.get("results") or [data]
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


class ExperimentAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        target_gene = spec.get("target_gene", "unknown")
        disease = spec.get("disease", "unknown")
        lens_summaries = spec.get("lens_summaries") or spec.get("lens_verdicts") or []
        genetics_floor_signals = spec.get("genetics_floor_signals") or {}
        liabilities: list[dict[str, Any]] = list(spec.get("candidate_liabilities") or [])

        evidences = [e for e in (msg.payload or []) if isinstance(e, Evidence)]
        classification = classify(evidences) if evidences else DataClass.NON_SENSITIVE
        provider, _model = ctx.router.select(classification, "experiment")

        ev_ids = [str(e.evidence_id) for e in evidences[:20]]
        score = compute_suitability(
            lens_summaries,
            liabilities=liabilities,
            human_exposure=bool(spec.get("human_exposure")),
            mendelian_grade=mendelian_grade_from_signals(genetics_floor_signals),
        )

        user_content = (
            f"Target gene: {target_gene}\n"
            f"Disease: {disease}\n\n"
            f"Lens verdicts:\n{_lens_summary(lens_summaries)}\n\n"
            f"{_liability_text(liabilities)}\n\n"
            f"{_score_text(score)}\n\n"
            f"Available evidence IDs for citation: {ev_ids}\n"
            f"Explain the computed score."
        )

        completion = await provider.complete(
            CompletionRequest(
                messages=[{"role": "user", "content": user_content}],
                system=_SYSTEM_PROMPT,
                classification=classification,
                task="experiment",
                model_override=_model,
            )
        )

        parsed = _parse_results(completion.content)
        first = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
        model_rationale = str(first.get("rationale") or "").strip()
        if not model_rationale:
            logger.warning(
                "experiment: no usable rationale in the model output (%d chars: %r…) — "
                "using the deterministic summary",
                len(completion.content or ""),
                (completion.content or "")[:120],
            )
        results = [
            {
                "target": target_gene,
                "score": score.score,
                "band": score.band,
                "rationale": model_rationale or _fallback_rationale(score),
                # Lets the report say when the explanation is the deterministic fallback.
                "rationale_source": "model" if model_rationale else "deterministic",
                "supporting_evidence_ids": [
                    i for i in first.get("supporting_evidence_ids") or [] if i in ev_ids
                ],
                "lens_summary": {c.lens: c.verdict for c in score.contributions},
                "score_breakdown": score.model_dump(mode="json"),
            }
        ]

        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"experiment_results": results},
            trace_id=msg.trace_id,
        )
