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
FUNCTIONAL_GENOMICS, REGULATORY_ELEMENT, EPIDEMIOLOGY) is auto-kept — these are
pre-curated records that don't require LLM relevance filtering, and so are phased
drug trials whose record names the target gene, and FDA labels whose MoA names the
target or whose indication names the disease (each FAERS bundle inherits its
label's verdict).  Other clinical trials use a target-first text
(interventions → conditions → eligibility → brief_summary) via the shared
core.evidence_text.screenable_text helper so gene mentions in eligibility
criteria reach the screener.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from langfuse import LangfuseOtelSpanAttributes

from agents.screening.screening.contract import CONTRACT
from core.batching import pack_batches
from core.evidence_text import FDA_FAERS_PREFIX, is_on_target_fda_label, is_target_drug_trial
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
        EvidenceType.EPIDEMIOLOGY,
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


def _fulltext_max_chars() -> int:
    """Per-item cap on full-text excerpt length fed to the second-pass screener.

    Full papers can exceed the whole batch token budget on their own, so the
    body text injected for an uncertain item is truncated to this many chars
    (~SCREEN_FULLTEXT_MAX_CHARS/4 tokens). pack_batches then packs fewer items
    per second-pass call automatically. Set to 0 to disable full-text injection.
    """
    raw = os.environ.get("SCREEN_FULLTEXT_MAX_CHARS", "8000")
    try:
        return max(0, int(raw))
    except ValueError:
        return 8000


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
    full_text_block = ""
    if ev.scope == "full_text":
        cap = _fulltext_max_chars()
        body = ev.extra.get("full_text") or ""
        if cap and body:
            excerpt = body[:cap]
            if len(body) > cap:
                excerpt += " …[truncated]"
            full_text_block = f"  <full_text>{excerpt}</full_text>\n"
    return (
        f'<document id="{ev.source}"{pmid_attr}>\n'
        f"  <title>{title}</title>\n"
        f"  <first_author>{first_author}</first_author>\n"
        f"  <year>{pub_year}</year>\n"
        f"  <abstract>{abstract}</abstract>\n"
        f"{full_text_block}"
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


def _parse_verdicts(raw: str, batch: list[Evidence]) -> list[dict[str, Any]]:
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


def _apply_verdict(ev: Evidence, verdict: dict[str, Any]) -> Evidence:
    updated_extra = {**ev.extra, "screening_verdict": verdict}
    return ev.model_copy(update={"extra": updated_extra})


_NO_TEXT_RATIONALE = "No screenable text available"


def _rule_verdict(verdict: str, rationale: str) -> dict[str, Any]:
    """A verdict decided by a code rule, not the LLM.

    Flagged ``deterministic`` so the workflow never caches it: it costs nothing to
    recompute, and a cached copy would outlive a change to the rule that produced it.
    """
    return {"verdict": verdict, "rationale": rationale, "deterministic": True}


def is_reusable_cached_verdict(verdict: dict[str, Any]) -> bool:
    """Whether a cached screening verdict may stand in for screening this run.

    Rule verdicts are never reused (see `_rule_verdict`). Verdicts cached before that
    flag existed are reused except the "no screenable text" drop, which depends on
    text extraction rather than the item and silently dropped every openFDA row
    until REGULATORY text was wired into `screenable_text`.
    """
    return not verdict.get("deterministic") and verdict.get("rationale") != _NO_TEXT_RATIONALE


def _is_faers(ev: Evidence) -> bool:
    return ev.evidence_type == EvidenceType.REGULATORY and ev.source.startswith(FDA_FAERS_PREFIX)


def _drug_key(ev: Evidence) -> str:
    return str(ev.extra.get("drug_name") or "").strip().lower()


def inherit_faers_verdicts(screened: list[Evidence]) -> list[Evidence]:
    """Give each FAERS bundle in ``screened`` its parent FDA label's verdict.

    A FAERS bundle is fetched only because its drug's label was retrieved, and its
    adverse-event counts are only meaningful if that drug is. Screening it on its own
    text (counts and reaction names) cannot judge relevance, so it follows the label.
    The workflow re-applies this after merging cached and fresh verdicts, so a bundle
    whose label was a cache hit still finds its parent.
    """
    label_verdicts = {
        _drug_key(e): e.extra.get("screening_verdict") or {}
        for e in screened
        if e.evidence_type == EvidenceType.REGULATORY and not _is_faers(e)
    }
    out: list[Evidence] = []
    for ev in screened:
        if not _is_faers(ev):
            out.append(ev)
            continue
        parent = label_verdicts.get(_drug_key(ev))
        if parent:
            verdict = _rule_verdict(
                parent.get("verdict", "drop"),
                f"FAERS bundle inherits its FDA label's verdict: {parent.get('rationale', '')}",
            )
        else:
            verdict = _rule_verdict("drop", "No screened FDA label for this drug")
        out.append(_apply_verdict(ev, verdict))
    return out


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
            _apply_verdict(e, _rule_verdict("keep", "Pre-curated structured database record"))
            for e in auto_kept
        ]
        # Phased drug trials naming the target are kept deterministically (see
        # is_target_drug_trial): the LLM can drop a drug-code-only trial whose gene mention is
        # buried in the eligibility criteria.
        target_trials = [e for e in evidences if is_target_drug_trial(e)]
        evidences = [e for e in evidences if not is_target_drug_trial(e)]
        pre_kept += [
            _apply_verdict(
                e,
                _rule_verdict(
                    "keep", "Phased interventional trial whose record names the target gene"
                ),
            )
            for e in target_trials
        ]
        # FDA labels naming the target (MoA) or the disease (indication) are kept
        # deterministically (see is_on_target_fda_label); FAERS bundles are set aside
        # and resolved from their label's verdict once screening finishes.
        faers = [e for e in evidences if _is_faers(e)]
        on_target_labels = [e for e in evidences if is_on_target_fda_label(e)]
        evidences = [e for e in evidences if not _is_faers(e) and not is_on_target_fda_label(e)]
        pre_kept += [
            _apply_verdict(
                e,
                _rule_verdict(
                    "keep", "FDA label names the target in its MoA or the disease in its indication"
                ),
            )
            for e in on_target_labels
        ]

        # Pre-filter: drop items with no screenable text without calling the LLM
        no_abstract = [e for e in evidences if not _screenable_text(e).strip()]
        evidences = [e for e in evidences if _screenable_text(e).strip()]
        pre_dropped: list[Evidence] = [
            _apply_verdict(e, _rule_verdict("drop", _NO_TEXT_RATIONALE)) for e in no_abstract
        ]

        classification = classify(evidences)
        provider, _model = ctx.router.select(classification, "screening")
        target_gene = spec.get("target_gene", "unknown")
        disease = spec.get("disease", "unknown")

        tracer = get_tracer()
        screened: list[Evidence] = list(pre_kept) + list(pre_dropped)
        batches = pack_batches(evidences, _evidence_to_xml, _MAX_BATCH_INPUT_TOKENS, _SCREEN_BATCH)
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

        screened = inherit_faers_verdicts(screened + faers)

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
