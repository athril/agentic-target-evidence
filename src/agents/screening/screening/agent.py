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
FUNCTIONAL_GENOMICS, REGULATORY_ELEMENT, EPIDEMIOLOGY, DRUGGABILITY) is auto-kept —
these are pre-curated records looked up by the target's identifiers that don't
require LLM relevance filtering, and so are phased drug trials whose record names
the target gene, FDA labels whose MoA names the target or whose indication names
the disease (each FAERS bundle inherits its label's verdict), and patents whose
title or abstract names the target gene.

Items the LLM leaves without a verdict (response unparseable, or an id missing from
the returned array) are re-screened once in smaller batches before they fall back
to ``uncertain``.  Other clinical trials use a target-first text
(interventions → conditions → eligibility → brief_summary) via the shared
core.evidence_text.screenable_text helper so gene mentions in eligibility
criteria reach the screener.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from functools import partial
from typing import Any

from langfuse import LangfuseOtelSpanAttributes

from agents.screening.screening.contract import CONTRACT
from agents.screening.screening.excerpt import build_excerpt
from core.batching import estimate_tokens, pack_batches
from core.evidence_text import (
    FDA_FAERS_PREFIX,
    is_on_target_fda_label,
    is_on_target_patent,
    is_target_drug_trial,
)
from core.evidence_text import screenable_text as _screenable_text
from core.json_utils import parse_array_prefix, strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from core.telemetry.setup import get_tracer
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import Evidence, EvidenceType
from schemas.messages import AgentMessage
from services.evidence.disease_scope import DiseaseScope
from services.evidence.literature_selection import drug_pattern, symbol_pattern

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
        EvidenceType.DRUGGABILITY,
    }
)

_SYSTEM_PROMPT = """You are a rigorous scientific screener for a drug-target validation pipeline.
Classify each piece of evidence as ONE of: keep / drop / uncertain.
- keep: directly relevant to the target gene's role in the disease
- keep also: evidence about what modulating the target gene does in humans or
  animals, in ANY indication — human genetics (variants, Mendelian randomisation,
  loss-of-function carriers), trials of drugs acting on the gene, and safety or
  adverse-effect findings. Such evidence is relevant even when it never mentions the
  disease. Items marked "[Retrieved as …]" were fetched for exactly this reason
- drop: not relevant, methodologically unsound, or clearly off-topic (including a
  marked item that turns out not to concern the gene or its drugs)
- uncertain: relevance depends on full-text content not visible in the abstract

Respond with a JSON array — one object per evidence item, in the same order.
Echo back the document's id attribute exactly as given:
[{"id": "<document id>", "verdict": "keep"|"drop"|"uncertain", "rationale": "<at most 12 words>"}]
Output ONLY the JSON array. No prose, no markdown fences."""

# Hard ceiling on items per LLM call, regardless of how small each one is.
_SCREEN_BATCH = 25

# Reply budget: each verdict entry (id, verdict, a rationale of at most 12 words) runs
# about 46 tokens on average and 50 in the worst batch measured; 80 per item leaves room
# for a wordier model, and the base covers the brackets. A reply cut off anyway keeps
# its complete entries (`_parse_verdicts`).
_REPLY_TOKENS_PER_ITEM = 80
_REPLY_BASE_TOKENS = 100

# Batches are packed by estimated input tokens, since evidence text length varies a lot
# by source (abstracts ~350 tokens; patent, trial and FDA text much longer). The input
# budget is what the screening context window leaves after the largest reply, the system
# prompt and a margin for the estimate being approximate (`_input_budget`). Tokens are
# counted at the model's measured characters per token (core.tokens).
_BUDGET_MARGIN_TOKENS = 512
# Window assumed for a provider that does not report one (config/routing.yaml's
# screening num_ctx).
_DEFAULT_NUM_CTX = 16384

# Items left without a verdict by a batch call are re-screened once, this many per call.
# A smaller batch is the cheapest way out of a truncated or partial JSON array.
_RETRY_BATCH = 5

_UNPARSED_RATIONALE = "LLM response could not be parsed"
_MISSING_RATIONALE = "verdict missing from LLM response"
_NO_VERDICT_RATIONALES = frozenset({_UNPARSED_RATIONALE, _MISSING_RATIONALE})


def _reply_cap(batch_size: int) -> int:
    """``max_tokens`` for a screening reply covering ``batch_size`` items."""
    return batch_size * _REPLY_TOKENS_PER_ITEM + _REPLY_BASE_TOKENS


def _input_budget(provider: Any) -> int:
    """Input tokens a screening batch may use: the context window less the largest
    reply, the system prompt and a safety margin."""
    window_of = getattr(provider, "context_window", None)
    window = window_of("screening") if callable(window_of) else None
    if not isinstance(window, int) or window <= 0:
        window = _DEFAULT_NUM_CTX
    system_tokens = estimate_tokens(_SYSTEM_PROMPT)
    return window - _reply_cap(_SCREEN_BATCH) - system_tokens - _BUDGET_MARGIN_TOKENS


def _fulltext_max_chars() -> int:
    """Per-item cap on the full-text excerpt fed to the second-pass screener.

    Full papers can exceed the whole batch token budget on their own, so the
    excerpt for an uncertain item is at most this many chars
    (~SCREEN_FULLTEXT_MAX_CHARS/3 tokens at the default ratio). pack_batches then packs fewer items
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


Excerpter = Callable[[Evidence], str]


def excerpter_for(spec: dict[str, Any]) -> Excerpter:
    """The pass-2 full-text excerpt for this run (see excerpt.build_excerpt).

    The names come from the task spec: ``target_names`` (gene symbol and aliases, matched in
    exact case), ``drug_names`` (drugs acting on the target, any case) and
    ``disease_scope`` (a ``DiseaseScope`` dump). Without names the target gene is used.
    """
    target = symbol_pattern(list(spec.get("target_names") or [spec.get("target_gene") or ""]))
    drugs = drug_pattern(list(spec.get("drug_names") or []))
    raw_scope = spec.get("disease_scope")
    scope = DiseaseScope.model_validate(raw_scope) if raw_scope else None

    def mentions(text: str) -> int:
        return sum(len(p.findall(text)) for p in (target, drugs) if p is not None)

    def names_disease(text: str) -> bool:
        return scope is not None and scope.match(text) != "none"

    def excerpt(ev: Evidence) -> str:
        return build_excerpt(
            ev.extra.get("full_text_sections") or [],
            mentions=mentions,
            names_disease=names_disease,
            max_chars=_fulltext_max_chars(),
            flat_text=ev.extra.get("full_text") or "",
        )

    return excerpt


def _evidence_to_xml(ev: Evidence, excerpt: Excerpter | None = None) -> str:
    title = ev.extra.get("title", "")
    abstract = _screenable_text(ev)
    pmid = ev.extra.get("pmid")
    pub_year = ev.extra.get("pub_year", "")
    first_author = _first_author(ev)
    pmid_attr = f' pmid="{pmid}"' if pmid else ""
    full_text_block = ""
    if ev.scope == "full_text":
        text = (excerpt or excerpter_for({"target_gene": ev.gene}))(ev)
        if text:
            full_text_block = f"  <full_text>\n{text}\n  </full_text>\n"
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
    """Parse LLM verdicts and align them to the batch by 'id', falling back to position.

    A reply cut off mid-array keeps its complete entries (`parse_array_prefix`); only the
    items missing from it are marked, so the retry asks for those alone.
    """
    fallback = [{"verdict": "uncertain", "rationale": _UNPARSED_RATIONALE}] * len(batch)
    try:
        items = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError:
        items = parse_array_prefix(raw)
    if not isinstance(items, list) or not items:
        return fallback

    id_to_verdict = {
        str(item["id"]): item for item in items if isinstance(item, dict) and "id" in item
    }
    if id_to_verdict:
        return [
            id_to_verdict.get(
                str(ev.source),
                {"verdict": "uncertain", "rationale": _MISSING_RATIONALE},
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
    until REGULATORY text was wired into `screenable_text` (and every DRUGGABILITY
    row until it was too). An LLM non-answer — unparseable response or missing id —
    is not a judgment of the item, so it is never reused either.
    """
    rationale = verdict.get("rationale")
    return (
        not verdict.get("deterministic")
        and rationale != _NO_TEXT_RATIONALE
        and rationale not in _NO_VERDICT_RATIONALES
    )


def is_no_verdict(verdict: dict[str, Any] | None) -> bool:
    """Whether a screening verdict is an LLM non-answer (unparseable reply or missing
    id) rather than a judgment of the item."""
    return (verdict or {}).get("rationale") in _NO_VERDICT_RATIONALES


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

        # Patents naming the target are IP on the target — commercial evidence the
        # "role in the disease" screening question cannot judge (see is_on_target_patent).
        target_patents = [e for e in evidences if is_on_target_patent(e)]
        evidences = [e for e in evidences if not is_on_target_patent(e)]
        pre_kept += [
            _apply_verdict(
                e, _rule_verdict("keep", "Patent whose title or abstract names the target gene")
            )
            for e in target_patents
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
        to_xml = partial(_evidence_to_xml, excerpt=excerpter_for(spec))

        async def _screen(batch: list[Evidence], batch_index: int) -> list[dict[str, Any]]:
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

                documents = "\n\n".join(to_xml(e) for e in batch)
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
                        max_tokens=_reply_cap(len(batch)),
                        model_override=_model,
                    )
                )
                verdicts = _parse_verdicts(completion.content, batch)
                batch_span.set_attribute(
                    LangfuseOtelSpanAttributes.OBSERVATION_OUTPUT,
                    json.dumps(verdicts, ensure_ascii=False),
                )
                return verdicts

        verdict_by_id: dict[uuid.UUID, dict[str, Any]] = {}
        input_budget = _input_budget(provider)
        batches = pack_batches(evidences, to_xml, input_budget, _SCREEN_BATCH)
        for batch_index, batch in enumerate(batches):
            verdicts = await _screen(batch, batch_index)
            verdict_by_id.update((ev.evidence_id, v) for ev, v in zip(batch, verdicts, strict=True))

        # One retry, in smaller batches, for items the LLM left without a verdict.
        unresolved = [ev for ev in evidences if is_no_verdict(verdict_by_id[ev.evidence_id])]
        retry_batches = pack_batches(unresolved, to_xml, input_budget, _RETRY_BATCH)
        for retry_index, batch in enumerate(retry_batches, start=len(batches)):
            verdicts = await _screen(batch, retry_index)
            for ev, v in zip(batch, verdicts, strict=True):
                if not is_no_verdict(v):
                    verdict_by_id[ev.evidence_id] = v

        screened.extend(_apply_verdict(ev, verdict_by_id[ev.evidence_id]) for ev in evidences)

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
