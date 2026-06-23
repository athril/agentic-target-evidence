# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""CriticAgent — three-pass challenge: source-QA + claim-QA + verdict-QA.

Pass 1 (source-QA): reads the per-source quality assessment precomputed by
SourceQualityAgent (task_spec["source_quality"]) for each kept Evidence and
re-emits it as a critique — scoring happens once, upstream of the lenses, so
this pass is a lookup, not an LLM call.

Pass 2 (claim-QA): reviews extracted_claims from task_spec for contradictions,
duplicate statements, and low-confidence claims.

Pass 3 (verdict-QA): reviews the full set of lens_verdicts for scientific
inconsistencies — conflicts between lenses, overconfident single-lens results,
underpowered key lenses, and commercial bias.

All three passes' results are merged into the single "critiques" output key.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

from agents.challenge.critic.contract import CONTRACT
from core.json_utils import strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import CoreClaim, DataClass, Evidence
from schemas.messages import AgentMessage
from schemas.verdicts import LensVerdict


def _claim_summary_batch(claims: list[CoreClaim]) -> str:
    items = [
        {
            "claim_id": str(c.evidence_id),
            "text": c.claim_text[:150],
            "direction": c.direction.value,
            "confidence": c.confidence,
            "evidence_type": c.evidence_type.value,
        }
        for c in claims
    ]
    return json.dumps(items, ensure_ascii=False)


def _parse_claim_qa(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(strip_json_fence(raw))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return [{"claim_qa": "unparseable LLM response", "issues": []}]


def _verdict_summary(verdicts: list[LensVerdict]) -> str:
    items = [
        {
            "lens": lv.lens,
            "overall_verdict": lv.overall_verdict,
            "confidence": lv.confidence,
            "axes": [
                {"axis": ax.axis, "verdict": ax.verdict, "confidence": ax.confidence}
                for ax in lv.axes
            ],
            "rationale": lv.rationale[:150],
        }
        for lv in verdicts
    ]
    return json.dumps(items, ensure_ascii=False)


def _parse_verdict_qa(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(strip_json_fence(raw))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return [{"verdict_qa": "unparseable LLM response", "issues": []}]


class CriticAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        all_critiques: list[dict[str, Any]] = []

        # ── Pass 1: source-quality assessment (precomputed upstream) ────────
        evidences = [e for e in (msg.payload or []) if isinstance(e, Evidence)]
        keep_evidences = [
            e for e in evidences if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
        ]
        quality_map: dict[str, Any] = spec.get("source_quality") or {}

        for ev in keep_evidences:
            q = quality_map.get(str(ev.evidence_id))
            if q is None:
                all_critiques.append(
                    {
                        "evidence_id": str(ev.evidence_id),
                        "sjr_score": None,
                        "impact_factor": None,
                        "novelty_flag": None,
                        "predatory_flag": None,
                        "preprint_flag": None,
                        "quality_challenge": "Could not assess — no precomputed source-quality entry.",
                    }
                )
                continue
            all_critiques.append(
                {
                    "evidence_id": str(ev.evidence_id),
                    "sjr_score": q.get("sjr_score"),
                    "impact_factor": q.get("impact_factor"),
                    "novelty_flag": q.get("novelty_flag"),
                    "predatory_flag": q.get("predatory_flag"),
                    "preprint_flag": q.get("preprint_flag"),
                    "quality_challenge": q.get("quality_note"),
                }
            )

        # ── Pass 2: claim-QA (runs when extracted_claims are present) ───────
        raw_claims = spec.get("extracted_claims") or []
        claims: list[CoreClaim] = []
        for c in raw_claims:
            if isinstance(c, CoreClaim):
                claims.append(c)
            elif isinstance(c, dict):
                with contextlib.suppress(Exception):
                    claims.append(CoreClaim.model_validate(c))

        if claims:
            classification = classify(keep_evidences) if keep_evidences else DataClass.NON_SENSITIVE
            provider, _model = ctx.router.select(classification, "critic")
            claims_json = _claim_summary_batch(claims[:50])  # cap to avoid context overflow
            user_content = (
                f"Target gene: {spec.get('target_gene', 'unknown')}\n"
                f"Disease: {spec.get('disease', 'unknown')}\n\n"
                f"Review these extracted claims for: contradictions between claims, "
                f"near-duplicate statements, claims with very low confidence (<0.3), "
                f"and claims that lack a clear direction when one is expected.\n\n"
                f"Claims:\n{claims_json}\n\n"
                f"Return a JSON list of issue objects: "
                f'[{{"claim_id": "...", "issue_type": "contradiction|duplicate|low_confidence|direction_missing", '
                f'"description": "...", "severity": "high|medium|low"}}]. '
                f"Return [] if no issues found."
            )
            completion = await provider.complete(
                CompletionRequest(
                    messages=[{"role": "user", "content": user_content}],
                    system="You are a rigorous biomedical claim auditor.",
                    classification=classification,
                    task="critic_claim_qa",
                    model_override=_model,
                )
            )
            claim_qa_issues = _parse_claim_qa(completion.content)
            all_critiques.append(
                {
                    "pass": "claim_qa",
                    "claim_count": len(claims),
                    "issues": claim_qa_issues,
                }
            )

        # ── Pass 3: verdict-QA (runs when lens_verdicts are present) ─────────
        raw_verdicts = spec.get("lens_verdicts") or []
        verdicts: list[LensVerdict] = []
        for v in raw_verdicts:
            if isinstance(v, LensVerdict):
                verdicts.append(v)
            elif isinstance(v, dict):
                with contextlib.suppress(Exception):
                    verdicts.append(LensVerdict.model_validate(v))

        if verdicts:
            skill_text = ctx.load_skill("verdict_qa")
            classification = DataClass.NON_SENSITIVE
            provider, _model = ctx.router.select(classification, "critic_verdict_qa")
            verdicts_json = _verdict_summary(verdicts)
            user_content = (
                f"Target gene: {spec.get('target_gene', 'unknown')}\n"
                f"Disease: {spec.get('disease', 'unknown')}\n\n"
                f"Lens verdicts ({len(verdicts)}):\n{verdicts_json}"
            )
            completion = await provider.complete(
                CompletionRequest(
                    messages=[{"role": "user", "content": user_content}],
                    system=skill_text,
                    classification=classification,
                    task="critic_verdict_qa",
                    model_override=_model,
                )
            )
            verdict_qa_issues = _parse_verdict_qa(completion.content)
            all_critiques.append(
                {
                    "pass": "verdict_qa",
                    "lens_count": len(verdicts),
                    "issues": verdict_qa_issues,
                }
            )

        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"critiques": all_critiques},
            trace_id=msg.trace_id,
        )
