# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Screening agent.

First pass  — abstract-level: LLM classifies each Evidence as keep/drop/uncertain.
Second pass — full-text re-screen: re-evaluates only "uncertain" items whose
              scope has been upgraded to "full_text" by KnowledgeExtractionAgent.

Verdict and rationale are stored in Evidence.extra["screening_verdict"] as:
  {"verdict": "keep"|"drop"|"uncertain", "rationale": "<one sentence>"}

Evidence is frozen so we use model_copy(update=...) to attach the verdict.

Structured database evidence (GENETICS, CONSTRAINT, OMICS, EXPRESSION,
FUNCTIONAL_GENOMICS, REGULATORY_ELEMENT) is auto-kept — these are pre-curated
records that don't require LLM relevance filtering.  Clinical trials use a target-first text
(interventions → conditions → eligibility → brief_summary) via the shared
core.evidence_text.screenable_text helper so gene mentions in eligibility
criteria reach the screener.
"""

from __future__ import annotations

import json
import uuid

from langfuse import LangfuseOtelSpanAttributes

from agents.screening.screening.contract import CONTRACT
from core.batching import pack_batches
from core.evidence_text import screenable_text as _screenable_text
from core.json_utils import strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from core.telemetry.setup import get_tracer
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import Evidence, EvidenceType
from schemas.messages import AgentMessage

# Structured / pre-curated evidence types bypass LLM screening entirely.
_AUTO_KEEP_TYPES = frozenset(
    {
        EvidenceType.GENETICS,
        EvidenceType.CONSTRAINT,
        EvidenceType.OMICS,
        EvidenceType.EXPRESSION,
        EvidenceType.FUNCTIONAL_GENOMICS,
        EvidenceType.REGULATORY_ELEMENT,
    }
)

_SYSTEM_PROMPT = """You are a rigorous scientific screener for a drug-target validation pipeline.
Classify each piece of evidence as ONE of: keep / drop / uncertain.
- keep: directly relevant to the target gene's role in the disease
- drop: not relevant, methodologically unsound, or clearly off-topic
- uncertain: relevance depends on full-text content not visible in the abstract

Respond with a JSON array — one object per evidence item, in the same order.
Echo back the document's id attribute exactly as given:
[{"id": "<document id>", "verdict": "keep"|"drop"|"uncertain", "rationale": "<one sentence>"}]
Output ONLY the JSON array. No prose, no markdown fences."""

# Hard ceiling on items per LLM call, regardless of how small each one is.
# Output already scales with batch size (max_tokens=len(batch)*60 below), so
# this ceiling never bounds output — only _MAX_BATCH_INPUT_TOKENS does.
_SCREEN_BATCH = 25

# Soft ceiling on estimated input tokens per LLM call. Evidence text length
# varies a lot by source (PubMed abstracts ~350 tokens, but patent/clinical
# trial/FDA text can run much longer), so batches are packed by running token
# estimate rather than a fixed item count — see core.batching.pack_batches.
# Budget leaves headroom in the 16K screening num_ctx (config/routing.yaml)
# for the system prompt (~150 tok), per-batch wrapper text, JSON output
# (batch_size × 60 tok), and slack for the chars/4 estimate being approximate.
_MAX_BATCH_INPUT_TOKENS = 11000


def _first_author(ev: Evidence) -> str:
    authors = ev.extra.get("authors") or []
    return authors[0] if authors else ""


def _evidence_to_xml(ev: Evidence) -> str:
    title = ev.extra.get("title", "")
    abstract = _screenable_text(ev)
    pmid = ev.extra.get("pmid")
    pub_year = ev.extra.get("pub_year", "")
    first_author = _first_author(ev)
    pmid_attr = f' pmid="{pmid}"' if pmid else ""
    return (
        f'<document id="{ev.source}"{pmid_attr}>\n'
        f"  <title>{title}</title>\n"
        f"  <first_author>{first_author}</first_author>\n"
        f"  <year>{pub_year}</year>\n"
        f"  <abstract>{abstract}</abstract>\n"
        f"</document>"
    )


def _batch_meta(batch: list[Evidence]) -> str:
    """Structured document list for Langfuse span visibility."""
    return json.dumps(
        [
            {
                "id": ev.source,
                "pmid": ev.extra.get("pmid"),
                "title": ev.extra.get("title", ""),
                "pub_year": ev.extra.get("pub_year"),
                "first_author": _first_author(ev),
            }
            for ev in batch
        ],
        ensure_ascii=False,
    )


def _parse_verdicts(raw: str, batch: list[Evidence]) -> list[dict]:
    """Parse LLM verdicts and align them to the batch by 'id', falling back to position."""
    fallback = [{"verdict": "uncertain", "rationale": "LLM response could not be parsed"}] * len(
        batch
    )
    try:
        items = json.loads(strip_json_fence(raw))
        if not isinstance(items, list):
            return fallback
    except json.JSONDecodeError:
        return fallback

    id_to_verdict = {
        str(item["id"]): item for item in items if isinstance(item, dict) and "id" in item
    }
    if id_to_verdict:
        return [
            id_to_verdict.get(
                str(ev.source),
                {"verdict": "uncertain", "rationale": "verdict missing from LLM response"},
            )
            for ev in batch
        ]
    # LLM omitted ids — fall back to positional alignment if count matches
    if len(items) == len(batch):
        return items
    return fallback


def _apply_verdict(ev: Evidence, verdict: dict) -> Evidence:
    updated_extra = {**ev.extra, "screening_verdict": verdict}
    return ev.model_copy(update={"extra": updated_extra})


class ScreeningAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        pass_type = spec.get("pass_type", "first")

        if not isinstance(msg.payload, list) or not msg.payload:
            return AgentMessage(
                message_id=uuid.uuid4(),
                run_id=msg.run_id,
                from_agent=msg.to_agent,
                to_agent=msg.from_agent,
                intent="result",
                payload=[],
                trace_id=msg.trace_id,
            )

        evidences: list[Evidence] = [e for e in msg.payload if isinstance(e, Evidence)]

        # Second pass: only re-screen "uncertain" full-text items
        if pass_type == "second":
            evidences = [
                e
                for e in evidences
                if e.extra.get("screening_verdict", {}).get("verdict") == "uncertain"
                and e.scope == "full_text"
            ]
            if not evidences:
                return AgentMessage(
                    message_id=uuid.uuid4(),
                    run_id=msg.run_id,
                    from_agent=msg.to_agent,
                    to_agent=msg.from_agent,
                    intent="result",
                    payload=list(msg.payload),
                    trace_id=msg.trace_id,
                )

        # Auto-keep structured/pre-curated evidence types — no LLM screening needed.
        auto_kept = [e for e in evidences if e.evidence_type in _AUTO_KEEP_TYPES]
        evidences = [e for e in evidences if e.evidence_type not in _AUTO_KEEP_TYPES]
        pre_kept: list[Evidence] = [
            _apply_verdict(
                e, {"verdict": "keep", "rationale": "Pre-curated structured database record"}
            )
            for e in auto_kept
        ]

        # Pre-filter: drop items with no screenable text without calling the LLM
        no_abstract = [e for e in evidences if not _screenable_text(e).strip()]
        evidences = [e for e in evidences if _screenable_text(e).strip()]
        pre_dropped: list[Evidence] = [
            _apply_verdict(e, {"verdict": "drop", "rationale": "No screenable text available"})
            for e in no_abstract
        ]

        classification = classify(evidences)
        provider, _model = ctx.router.select(classification, "screening")
        target_gene = spec.get("target_gene", "unknown")
        disease = spec.get("disease", "unknown")

        tracer = get_tracer()
        screened: list[Evidence] = list(pre_kept) + list(pre_dropped)
        batches = pack_batches(
            evidences, _evidence_to_xml, _MAX_BATCH_INPUT_TOKENS, _SCREEN_BATCH
        )
        for batch_index, batch in enumerate(batches):
            with tracer.start_as_current_span("screening.batch") as batch_span:
                batch_span.set_attribute(LangfuseOtelSpanAttributes.OBSERVATION_TYPE, "span")
                batch_span.set_attribute(
                    LangfuseOtelSpanAttributes.OBSERVATION_INPUT, _batch_meta(batch)
                )
                batch_span.set_attribute(
                    f"{LangfuseOtelSpanAttributes.OBSERVATION_METADATA}.batch_size",
                    str(len(batch)),
                )
                batch_span.set_attribute(
                    f"{LangfuseOtelSpanAttributes.OBSERVATION_METADATA}.batch_index",
                    str(batch_index),
                )

                documents = "\n\n".join(_evidence_to_xml(e) for e in batch)
                user_msg = (
                    f"Target gene: {target_gene}\n"
                    f"Disease: {disease}\n\n"
                    f"Evidence to screen:\n<documents>\n{documents}\n</documents>"
                )
                completion = await provider.complete(
                    CompletionRequest(
                        messages=[{"role": "user", "content": user_msg}],
                        system=_SYSTEM_PROMPT,
                        classification=classification,
                        task="screening",
                        # JSON array output: ~60 tokens per item is a safe upper bound.
                        max_tokens=len(batch) * 60,
                        model_override=_model,
                    )
                )
                verdicts = _parse_verdicts(completion.content, batch)
                screened.extend(
                    _apply_verdict(ev, v) for ev, v in zip(batch, verdicts, strict=True)
                )
                batch_span.set_attribute(
                    LangfuseOtelSpanAttributes.OBSERVATION_OUTPUT,
                    json.dumps(verdicts, ensure_ascii=False),
                )

        # For second pass: merge screened items back into original payload
        if pass_type == "second":
            screened_ids = {e.evidence_id for e in screened}
            original_rest = [
                e
                for e in msg.payload
                if isinstance(e, Evidence) and e.evidence_id not in screened_ids
            ]
            screened = original_rest + screened

        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload=screened,
            trace_id=msg.trace_id,
        )
