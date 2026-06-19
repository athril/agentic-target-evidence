# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""SourceQualityAgent — scores each kept literature Evidence for journal quality.

Runs once, before the interpretation lenses, so every lens (and the Critic)
can read the same precomputed assessment instead of each re-deriving it.

SJR score/quartile, novelty, and preprint status are resolved deterministically
(scimago lookup table + pub-date/source pattern matching) rather than recalled
by an LLM — journal rank is a lookup, not a judgment call, and LLM recall of
numeric SJR values was unreliable (mostly null). A journal that resolves a real
SJR/quartile is by construction Scopus-indexed, which already excludes known
predatory publishers, so `predatory_flag` is deterministically False for those.

The SJR data is non-commercial-licensed and off by default (`SCIMAGO_SJR_ENABLED`);
when it doesn't resolve, we fall back to OpenAlex (CC0, commercial-safe) for an
open journal-quality signal (2yr mean citedness mapped onto the same 0-1 score,
plus a DOAJ/h-index legitimacy check). The LLM is only consulted for sources
neither source can place, where predatory-journal judgment genuinely needs
reasoning (name-mimicry, publisher reputation) rather than a lookup.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date

import httpx

from agents.screening.source_quality.contract import CONTRACT
from core.json_utils import strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from harness.base_agent import BaseAgent
from harness.context import RunContext
from mcp_servers.openalex.tools import resolve_journal
from mcp_servers.scimago.tools import resolve_sjr
from schemas.evidence import Evidence, EvidenceType
from schemas.messages import AgentMessage

_HTTP_TIMEOUT = 15.0
_MAX_CONCURRENT_LOOKUPS = 8  # bound OpenAlex calls via the client's connection pool

_BATCH_SIZE = 50  # LLM pass now only judges predatory_flag for table-unmatched sources
_NOVELTY_WINDOW_YEARS = 2

# Journal rank only applies to published literature — patents, trial records,
# omics/genetics data sources etc. have no SJR-equivalent concept.
_LITERATURE_TYPES = {
    EvidenceType.ARTICLE,
    EvidenceType.ABSTRACT,
    EvidenceType.CONFERENCE,
    EvidenceType.BOOK,
}

_PREPRINT_PATTERNS = ("biorxiv", "medrxiv", "ssrn", "researchsquare", "preprints.org")


def _is_preprint(ev: Evidence) -> bool:
    journal = (ev.extra.get("full_journal") or ev.extra.get("journal") or "").lower()
    source = (ev.source or "").lower()
    return any(p in journal or p in source for p in _PREPRINT_PATTERNS)


def _is_novel(ev: Evidence) -> bool | None:
    pub_year = ev.extra.get("pub_year")
    if not pub_year:
        return None
    try:
        return (date.today().year - int(pub_year)) <= _NOVELTY_WINDOW_YEARS
    except (TypeError, ValueError):
        return None


def _unresolved(ev: Evidence) -> dict:
    return {
        "evidence_id": str(ev.evidence_id),
        "sjr_score": None,
        "impact_factor": None,
        "sjr_quartile": None,
        "novelty_flag": _is_novel(ev),
        "predatory_flag": None,
        "preprint_flag": _is_preprint(ev),
        "quality_note": None,
        "_matched": False,
    }


async def _resolve_quality(ev: Evidence, client: httpx.AsyncClient) -> dict:
    """Resolve a source's journal-quality assessment.

    SJR first (deterministic, non-commercial — gated by `SCIMAGO_SJR_ENABLED`),
    then OpenAlex (CC0, commercial-safe). `_matched` means predatory_flag is
    deterministically settled (skip the LLM pass); an OpenAlex match that isn't
    "established" still carries a quality score but is left to the LLM for the
    predatory call.
    """
    issn = ev.extra.get("issn", "")
    essn = ev.extra.get("essn", "")
    title = ev.extra.get("full_journal") or ev.extra.get("journal", "")

    sjr = resolve_sjr(issn=issn, essn=essn, journal_title=title)
    if sjr.matched:
        return {
            "evidence_id": str(ev.evidence_id),
            "sjr_score": sjr.sjr_score,
            "impact_factor": None,
            "sjr_quartile": sjr.sjr_quartile,
            "novelty_flag": _is_novel(ev),
            "predatory_flag": False,
            "preprint_flag": _is_preprint(ev),
            "quality_note": f"SJR {sjr.sjr_quartile} (score {sjr.sjr:.2f}) — {sjr.matched_title}",
            "_matched": True,
        }

    oa = await resolve_journal(issn=issn, essn=essn, journal_title=title, client=client)
    if oa.matched:
        citedness = oa.two_yr_mean_citedness
        note = (
            f"OpenAlex: {citedness:.1f} cites/paper (2yr), h-index {oa.h_index} — {oa.display_name}"
            if citedness is not None
            else f"OpenAlex: {oa.display_name} (no citation stats)"
        )
        return {
            "evidence_id": str(ev.evidence_id),
            "sjr_score": oa.quality_score,
            "impact_factor": citedness,
            "sjr_quartile": None,
            "novelty_flag": _is_novel(ev),
            "predatory_flag": False if oa.established else None,
            "preprint_flag": _is_preprint(ev),
            "quality_note": note,
            "_matched": bool(oa.established),
        }

    return _unresolved(ev)


def _source_summary(ev: Evidence) -> str:
    journal = ev.extra.get("full_journal") or ev.extra.get("journal", "")
    return f'{{"evidence_id": "{ev.evidence_id}", "journal": "{journal}"}}'


def _parse_predatory_assessments(raw: str, evidences: list[Evidence]) -> list[dict]:
    try:
        data = json.loads(strip_json_fence(raw))
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return [
        {
            "evidence_id": str(ev.evidence_id),
            "predatory_flag": None,
            "quality_challenge": "Could not assess — LLM response unparseable.",
        }
        for ev in evidences
    ]


class SourceQualityAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}
        evidences = [e for e in (msg.payload or []) if isinstance(e, Evidence)]
        keep_evidences = [
            e
            for e in evidences
            if e.extra.get("screening_verdict", {}).get("verdict") == "keep"
            and e.evidence_type in _LITERATURE_TYPES
        ]

        quality_map: dict[str, dict] = {}
        unmatched: list[Evidence] = []

        if keep_evidences:
            limits = httpx.Limits(max_connections=_MAX_CONCURRENT_LOOKUPS)
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT, follow_redirects=True, limits=limits
            ) as client:
                assessments = await asyncio.gather(
                    *(_resolve_quality(ev, client) for ev in keep_evidences)
                )
            for ev, assessment in zip(keep_evidences, assessments, strict=True):
                matched = assessment.pop("_matched")
                eid = assessment.pop("evidence_id")
                quality_map[eid] = assessment
                if not matched:
                    unmatched.append(ev)

        if unmatched:
            skill_text = ctx.load_skill("source_quality_sjr")
            classification = classify(unmatched)
            provider, _model = ctx.select_model(classification, "source_quality")

            for i in range(0, len(unmatched), _BATCH_SIZE):
                batch = unmatched[i : i + _BATCH_SIZE]
                sources_json = "[\n" + ",\n".join(_source_summary(e) for e in batch) + "\n]"
                user_content = (
                    f"Target gene: {spec.get('target_gene', 'unknown')}\n"
                    f"Disease: {spec.get('disease', 'unknown')}\n\n"
                    f"None of these sources matched a known SJR ranking (not "
                    f"Scopus-indexed, or an unresolvable journal name). Assess only "
                    f"whether each is likely a predatory journal.\n\n"
                    f"Sources to assess:\n{sources_json}\n\n"
                    f"Return one object per source in the same order: "
                    f'[{{"evidence_id": "...", "predatory_flag": true|false, '
                    f'"quality_challenge": "..."}}]'
                )
                completion = await provider.complete(
                    CompletionRequest(
                        messages=[{"role": "user", "content": user_content}],
                        system=skill_text,
                        classification=classification,
                        task="source_quality",
                        model_override=_model,
                    )
                )
                for assessment in _parse_predatory_assessments(completion.content, batch):
                    eid = str(assessment.get("evidence_id", ""))
                    if eid not in quality_map:
                        continue
                    quality_map[eid]["predatory_flag"] = assessment.get("predatory_flag")
                    quality_map[eid]["quality_note"] = assessment.get("quality_challenge")

        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"source_quality": quality_map},
            trace_id=msg.trace_id,
        )
