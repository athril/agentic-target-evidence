# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ExperimentAgent.

Receives lens verdicts (via task_spec) and screened evidence (payload).
Produces a target-suitability ranking: list of ExperimentResult dicts, each with
a score 0-100, rationale, and supporting evidence IDs.

Lens verdicts replace raw hypotheses as the synthesis input.
"""

from __future__ import annotations

import json
import uuid

from agents.synthesis.experiment.contract import CONTRACT
from core.json_utils import strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import DataClass, Evidence
from schemas.messages import AgentMessage
from services.decision.suitability import apply_mendelian_score_floor
from services.evidence.constraint_interpret import compute_mendelian_grade

_SYSTEM_PROMPT = """You are a drug target validation scientist producing a suitability ranking.

You are given interpretation verdicts from six lenses (genetics, biology, safety, clinical, commercial, regulatory)
and available evidence. Synthesise these into a target-suitability assessment.

Output a JSON array of ExperimentResult objects:
[{"target": "<gene_name>", "score": <0-100>, "rationale": "<2-3 sentences>", "supporting_evidence_ids": ["<uuid>", ...], "lens_summary": {"genetics": "<verdict>", "biology": "<verdict>", "safety": "<verdict>", "clinical": "<verdict>", "commercial": "<verdict>", "regulatory": "<verdict>"}}]

Score guide: 0=no-go, 25=weak, 50=uncertain, 75=promising, 100=strong go.
Weight genetics and safety most heavily; commercial is a secondary consideration.
Output ONLY the JSON array. No prose or markdown fences."""

_MENDELIAN_CONTEXT = (
    "\nMendelian context (pre-computed, deterministic): this gene-disease pair has "
    "Mendelian-grade genetic validation with a clear therapeutic direction. Treat this "
    "as a dominant positive that sets a floor on the suitability score — clinical "
    "efficacy/safety uncertainty may cap the upside, but must not be used to score "
    "below that floor.\n"
)


def _lens_summary(lens_summaries: list[dict]) -> str:
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


def _parse_results(raw: str) -> list[dict]:
    try:
        data = json.loads(strip_json_fence(raw))
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return []


class ExperimentAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        target_gene = spec.get("target_gene", "unknown")
        disease = spec.get("disease", "unknown")
        lens_summaries = spec.get("lens_summaries") or spec.get("lens_verdicts") or []
        genetics_floor_signals = spec.get("genetics_floor_signals") or {}

        evidences = [e for e in (msg.payload or []) if isinstance(e, Evidence)]
        classification = classify(evidences) if evidences else DataClass.NON_SENSITIVE
        provider, _model = ctx.router.select(classification, "experiment")

        verdicts_text = _lens_summary(lens_summaries)
        ev_ids = [str(e.evidence_id) for e in evidences[:20]]

        mendelian_grade = compute_mendelian_grade(
            high_star_plp=genetics_floor_signals.get("high_star_plp") or 0,
            plp_count=genetics_floor_signals.get("plp_count") or 0,
            clingen_classification=genetics_floor_signals.get("clingen_classification"),
            graph_association=genetics_floor_signals.get("graph_association"),
        )

        user_content = (
            f"Target gene: {target_gene}\n"
            f"Disease: {disease}\n\n"
            f"Lens verdicts:\n{verdicts_text}\n\n"
            f"Available evidence IDs for citation: {ev_ids}\n"
            f"{_MENDELIAN_CONTEXT if mendelian_grade else ''}\n"
            f"Produce a target-suitability ranking."
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

        results = _parse_results(completion.content)
        results = apply_mendelian_score_floor(results, mendelian_grade)

        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"experiment_results": results},
            trace_id=msg.trace_id,
        )
