# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""ReviewerAgent.

Generates per-stage gap reports for each pipeline stage (literature, genetics,
clinical, screening, extraction, lenses, experiment). Each report:
{stage, missing_aspects, completeness_score}.

Does NOT assess source quality — that is the Critic's domain.
Receives stage_counts (dict of stage → item count) via task_spec.
"""

from __future__ import annotations

import json
import uuid

from agents.challenge.reviewer.contract import CONTRACT
from core.json_utils import strip_json_fence
from core.routing.providers.base import CompletionRequest
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import DataClass
from schemas.messages import AgentMessage

_STAGES = ["literature", "genetics", "clinical", "screening", "extraction", "lenses", "experiment"]

_SYSTEM_PROMPT = """You are a scientific pipeline reviewer identifying evidence gaps.

For each pipeline stage provided, generate a gap report:
[{"stage": "<name>", "missing_aspects": ["<what is missing>", ...], "completeness_score": <0-100>}]

Focus on:
- Missing evidence types (e.g., no GWAS data, no clinical trials for this gene)
- Sparse coverage (too few studies for reliable conclusions)
- Missing disease subtypes or populations
- Incomplete mechanistic understanding

Do NOT comment on source quality — focus only on gaps and completeness.
Output ONLY the JSON array. No prose or markdown fences."""


def _parse_gaps(raw: str) -> list[dict]:
    try:
        data = json.loads(strip_json_fence(raw))
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return [
        {
            "stage": s,
            "missing_aspects": ["Could not assess — LLM response unparseable."],
            "completeness_score": 0,
        }
        for s in _STAGES
    ]


class ReviewerAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        target_gene = spec.get("target_gene", "unknown")
        disease = spec.get("disease", "unknown")
        stage_counts = spec.get("stage_counts", {})

        provider, _model = ctx.router.select(DataClass.NON_SENSITIVE, "reviewer")

        counts_text = json.dumps(stage_counts, indent=2)
        user_content = (
            f"Target gene: {target_gene}\n"
            f"Disease: {disease}\n\n"
            f"Pipeline stage item counts:\n{counts_text}\n\n"
            f"Generate gap reports for stages: {_STAGES}"
        )

        completion = await provider.complete(
            CompletionRequest(
                messages=[{"role": "user", "content": user_content}],
                system=_SYSTEM_PROMPT,
                classification=DataClass.NON_SENSITIVE,
                task="reviewer",
                model_override=_model,
            )
        )

        gaps = _parse_gaps(completion.content)

        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"review_gaps": gaps},
            trace_id=msg.trace_id,
        )
