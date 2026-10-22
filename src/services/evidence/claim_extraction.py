# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Claim extraction service — governed model-op.

Decomposes each screened Evidence document into atomic, typed CoreClaims.
Each claim carries claim_text, confidence, its typed effect on the target (with the
direction derived from it, services/evidence/claim_effect.py), and propagated provenance.

A paper PubTator3 has annotated is shown as numbered sentences (services/evidence/
grounding.py); each of its claims names the sentence it rests on (a copy task), and its
effect on the target comes from that sentence's statement, read from grounded mentions
(services/evidence/statements.py), never from the claim's words.

Governance: routes via ctx.model, emits a Langfuse span, and counts against
step_budget — exactly like any other model-op in the system.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from core.batching import pack_batches
from core.evidence_text import (
    FDA_FAERS_PREFIX,
    FDA_LABEL_PREFIX,
    indicates_disease,
    is_on_target_patent,
    label_acts_on_target,
    label_indications,
    mentions_term,
    target_relation,
)
from core.evidence_text import screenable_text as _generic_screenable_text
from core.json_utils import strip_json_fence
from core.routing.classify import classify
from core.routing.providers.base import CompletionRequest
from core.telemetry.langfuse import span
from core.tokens import get_estimator
from harness.context import RunContext
from harness.contract import ServiceContract
from mcp_servers.clinicaltrials.tools import OutcomeResult
from schemas.evidence import (
    CoreClaim,
    Direction,
    Evidence,
    EvidenceType,
    LensTopic,
    Provenance,
)
from services.evidence.claim_effect import settle_effect
from services.evidence.constraint_interpret import interpret_constraint as _interpret_constraint
from services.evidence.grounding import GroundedPaper
from services.evidence.readings import build_dependency_reading

logger = logging.getLogger(__name__)

CONTRACT = ServiceContract(
    name="claim_extraction",
    consumes={"target_gene", "disease", "direction"},
    produces={"extracted_claims"},
    max_loops=1,
    skills=["claim_extraction"],
)

DropReason = Literal["number_not_in_source", "example_identifier"]


class DroppedClaim(BaseModel):
    """A claim left out of the run by the grounding checks, and why."""

    source: str  # the source record, e.g. "PMID:123"
    claim_text: str
    reason: DropReason
    quote: str = ""


def grounding_reason(reason: str) -> DropReason:
    """The ``DroppedClaim`` reason of an ``ungrounded_reason`` message."""
    return (
        "example_identifier" if reason.startswith("example identifier") else "number_not_in_source"
    )


# Hard ceiling on items per LLM call, regardless of how small each one is.
# Lower than screening's: claim_extraction's output is heavier per item (up to
# _MAX_CLAIMS_PER_DOC claims, each with text/confidence/effect/topics vs.
# screening's terse verdict+rationale), so output tokens are explicitly scaled
# by batch size below rather than relying on the provider's default max_tokens
# — see _OUTPUT_TOKENS_PER_DOC.
_BATCH_SIZE = 10

# Worst-case output budget per document: _MAX_CLAIMS_PER_DOC claims at ~100 tok
# each (claim_text + confidence + sentence/outcome + topics) plus
# per-doc JSON overhead. Real-world usage runs far below this — this is a ceiling,
# not an expected value, sized for the rare claim-dense document.
_OUTPUT_TOKENS_PER_DOC = 540

# Soft ceiling on estimated input tokens per LLM call (see core.batching.pack_batches).
# At _BATCH_SIZE=10 the output reservation is _BATCH_SIZE × _OUTPUT_TOKENS_PER_DOC
# = 5400 tok: 16384 - skill(~1500 w/ slack) - output(5400) - wrapper(~100) ≈ 9400,
# divided by the ~1.2x slack factor for the estimate's undercount bias ≈ 7800 — kept
# conservatively below that, out of the 16K claim_extraction num_ctx
# (config/routing.yaml).
_MAX_BATCH_INPUT_TOKENS = 7000
_MAX_CLAIMS_PER_DOC = 5

# Free-text literature types are the only ones that carry lens-routing topics; every
# other type routes deterministically by evidence_type, so topics are ignored there.
_LITERATURE_TYPES = frozenset(
    {
        EvidenceType.ARTICLE,
        EvidenceType.ABSTRACT,
        EvidenceType.BOOK,
        EvidenceType.CONFERENCE,
    }
)


def _parse_topics(raw: object) -> list[LensTopic]:
    """Validate an LLM-supplied topics list to known LensTopic values, deduped.

    Unknown strings are dropped rather than raising, so a sloppy extraction
    degrades to "no topic" (biology-only) instead of failing the whole claim.
    """
    if not isinstance(raw, list):
        return []
    seen: list[LensTopic] = []
    for item in raw:
        if isinstance(item, str) and item in LensTopic._value2member_map_:
            topic = LensTopic(item)
            if topic not in seen:
                seen.append(topic)
    return seen


_STRUCTURED_TYPES = frozenset(
    {
        EvidenceType.GENETICS,
        EvidenceType.CONSTRAINT,
        EvidenceType.OMICS,
        EvidenceType.EXPRESSION,
        EvidenceType.FUNCTIONAL_GENOMICS,
        EvidenceType.REGULATORY_ELEMENT,
        EvidenceType.DRUGGABILITY,
    }
)

# Types that always get a deterministic claim, even when the LLM extracts nothing.
# Superset of _STRUCTURED_TYPES; clinical trials, FDA records, patents and the
# indication-competition landscape are deterministic for claim generation but keep
# the generic free-text path for LLM extraction (see _evidence_summary).
_DETERMINISTIC_TYPES = _STRUCTURED_TYPES | frozenset(
    {
        EvidenceType.CLINICAL_TRIAL,
        EvidenceType.REGULATORY,
        EvidenceType.PATENT,
        EvidenceType.COMPETITION,
    }
)

# Types whose connectors pre-render each record as a one-fact ``extra["text"]`` summary
# (UniProt localization, ChEMBL clinical phase, DGIdb interactions, TTD stage, ENCODE
# coverage). structured_claims turns that summary into the record's claim; add a type
# here, not a new branch, when a connector follows the same convention.
_SUMMARY_CLAIM_TYPES = frozenset({EvidenceType.DRUGGABILITY, EvidenceType.REGULATORY_ELEMENT})

# Types whose claims come only from the deterministic builder; the LLM extracts from free
# text (literature, trial summaries, label sections) only. A structured record already
# states its facts: the model only paraphrased them, dropped numbers (a fold change
# rounded away, then the claim dropped as ungrounded), merged a knockout record into
# "knockout mice are viable" that the grounding check then rejected, and returned nothing
# for whole batches of them. A patent abstract restates biology ("a <gene> missense
# mutation causes <disease>"); the only lens that reads patents (commercial) needs the IP
# facts — owner, date, what it names.
_DETERMINISTIC_ONLY_TYPES = _STRUCTURED_TYPES | frozenset(
    {EvidenceType.PATENT, EvidenceType.COMPETITION}
)


def _structured_text(ev: Evidence) -> str:
    """Synthesise a screenable text representation from structured database evidence."""
    x = ev.extra
    if ev.evidence_type == EvidenceType.GENETICS:
        # GWAS row (internal DB)
        if "pvalue" in x:
            fields = ", ".join(
                f"{label}={x[key]}"
                for label, key in (
                    ("p", "pvalue"),
                    ("beta", "beta"),
                    ("OR", "odds_ratio"),
                    ("study", "study_id"),
                    ("lof_score", "lof_score"),
                    ("is_lof_intolerant", "is_lof_intolerant"),
                )
                if x.get(key) is not None
            )
            return f"GWAS hit: {ev.gene} associated with {ev.disease} ({fields})"
        # OpenTargets association
        if "overall_score" in x:
            parts = [
                f"Open Targets: {ev.gene}↔{ev.disease} overall_score={x.get('overall_score')}, "
                f"genetic_score={x.get('genetic_score')}, known_drugs_score={x.get('known_drugs_score')}, "
                f"tractability_sm={x.get('tractability_small_molecule')}, "
                f"tractability_ab={x.get('tractability_antibody')}",
            ]
            if x.get("assoc_text"):
                parts.append(str(x["assoc_text"])[:300])
            if x.get("tract_text"):
                parts.append(str(x["tract_text"])[:300])
            return " | ".join(parts)
        # Breadth-summary rows (GWAS/coloc): text stored in extra["summary"]
        if "summary" in x:
            return str(x["summary"])
    if ev.evidence_type == EvidenceType.CONSTRAINT:
        base = x.get("text") or ""

        # ClinVar bundle (discriminated by "pathogenic" list key)
        if "pathogenic" in x and isinstance(x.get("pathogenic"), list):
            pvars = (x.get("pathogenic") or [])[:3]
            lpvars = (x.get("likely_pathogenic") or [])[:3]
            details: list[str] = []
            for v in pvars:
                desc = v.get("hgvsp") or v.get("hgvsc") or v.get("variant_id", "")
                cons = v.get("major_consequence", "")
                stars = v.get("gold_stars")
                details.append(f"{desc} ({cons}, stars={stars})" if stars else f"{desc} ({cons})")
            for v in lpvars:
                desc = v.get("hgvsp") or v.get("hgvsc") or v.get("variant_id", "")
                cons = v.get("major_consequence", "")
                details.append(f"{desc} ({cons}, LP)")
            if details:
                return base + " Key variants: " + "; ".join(details)
            return base

        # LoF variant bundle (discriminated by "hc_lof_count" key)
        if "hc_lof_count" in x:
            rep = (x.get("reported_variants") or [])[:3]
            details = []
            for v in rep:
                desc = v.get("hgvsc") or v.get("variant_id", "")
                af = v.get("af")
                hom = v.get("homozygote_count") or 0
                af_str = f" AF={af:.1e}" if af is not None else ""
                hom_str = f" hom={hom}" if hom else ""
                details.append(f"{desc}{af_str}{hom_str}")
            extra = (" Top HC pLoF: " + "; ".join(details)) if details else ""
            return base + extra

        # Constraint bundle fallback
        return base or (
            f"gnomAD constraint: LOEUF={x.get('loeuf')}, pLI={x.get('pli')}, "
            f"pRec={x.get('p_rec')}, MOEUF={x.get('moeuf')}, mis_z={x.get('mis_z')}"
        )
    if ev.evidence_type in (
        EvidenceType.OMICS,
        EvidenceType.EXPRESSION,
        EvidenceType.FUNCTIONAL_GENOMICS,
        EvidenceType.REGULATORY_ELEMENT,
    ):
        return str(x.get("text") or x.get("description") or _readable_fields(ev))
    # Any other structured record (UniProt, ChEMBL, DGIdb, TTD, ...) carries a
    # pre-rendered summary.
    return str(x.get("text") or "")


# Bookkeeping fields of a record's ``extra``, never facts about the gene.
_META_FIELDS = frozenset(
    {
        "gene_id",
        "disease_id",
        "gene_symbol",
        "ensembl_id",
        "screening_verdict",
        "chunk_count",
        "source_link",
        "retrieval_version",
    }
)


def _readable_fields(ev: Evidence) -> str:
    """A record without a pre-rendered text, as "<source> for <gene>: <field>: <value>; …"
    (lists shortened), instead of a JSON dump a reader and the screener must parse."""
    parts: list[str] = []
    for key, value in (ev.extra or {}).items():
        if key in _META_FIELDS or value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            shown = ", ".join(str(v) for v in value[:12]) + (", …" if len(value) > 12 else "")
        elif isinstance(value, dict):
            continue
        else:
            shown = str(value)
        parts.append(f"{key.replace('_', ' ')}: {shown}")
    return f"{ev.source} for {ev.gene}: " + "; ".join(parts) if parts else ""


# A structured record's own rendered text, turned into claims when no dedicated builder
# applies: one claim per sentence, a long sentence of ";"-separated items split into one
# claim per item under its heading, so no fact sits past what a lens prompt shows of a
# claim (its first 200 characters).
_RECORD_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_CLAIM_CHARS = 200
_MAX_TEXT_CLAIMS = 8


def _split_items(body: str) -> list[str]:
    """``body`` split at "; " outside quotes (a quoted condition may hold a semicolon)."""
    items: list[str] = []
    start, quote = 0, ""
    for i, ch in enumerate(body):
        if ch in "'\"" and (quote == "" or quote == ch):
            quote = "" if quote else ch
        elif not quote and body.startswith("; ", i):
            items.append(body[start:i])
            start = i + 2
    items.append(body[start:])
    return [i.strip() for i in items if i.strip()]


def _text_claims(ev: Evidence) -> list[str]:
    """Claims stating a structured record's rendered text: the text itself when short,
    otherwise sentence by sentence, each naming the gene."""
    text = " ".join(_structured_text(ev).split())
    if len(text) <= _CLAIM_CHARS:
        return [text] if text else []
    out: list[str] = []
    for sentence in _RECORD_SENTENCE.split(text):
        head, sep, body = sentence.partition(": ")
        items = _split_items(body) if sep else []
        if len(sentence) <= _CLAIM_CHARS or len(items) < 2 or len(head) > 120:
            out.append(sentence)
            continue
        if ev.gene and not mentions_term(head, ev.gene):
            head = f"{head} ({ev.gene})"
        out.extend(f"{head}: {item}" for item in items)
    if ev.gene:
        out = [c if mentions_term(c, ev.gene) else f"{ev.gene} — {c}" for c in out]
    return [c for c in out if c][:_MAX_TEXT_CLAIMS]


def numbered_text(paper: GroundedPaper) -> str:
    """A paper's sentences as the extractor sees them: "[0] <title> [1] <sentence> …"."""
    return " ".join(f"[{s.index}] {s.text}" for s in paper.sentences)


def _evidence_summary(ev: Evidence, paper: GroundedPaper | None = None) -> str:
    if ev.evidence_type in _STRUCTURED_TYPES:
        text = _structured_text(ev)
    elif paper is not None and paper.sentences:
        text = numbered_text(paper)
    else:
        text = _generic_screenable_text(ev)
    title = ev.extra.get("title", ev.source)[:120]
    # Clinical trials get a larger window: gene name often appears only in
    # eligibility text that is concatenated after the brief_summary.
    # Literature abstracts get a window covering ~p99 of observed abstract
    # lengths (measured 2559 across archived papers): structured RCT abstracts
    # (BACKGROUND/METHODS/FINDINGS/INTERPRETATION) put the actual outcome data
    # in the back half, past where a tighter cap would cut off.
    cap = 5000 if ev.evidence_type == EvidenceType.CLINICAL_TRIAL else 2500
    return json.dumps(
        {
            "evidence_id": str(ev.evidence_id),
            "evidence_type": ev.evidence_type.value,
            "source": ev.source,
            "title": title,
            "text": text[:cap],
        },
        ensure_ascii=False,
    )


_CLAIM_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?(?![A-Za-z0-9_]|\.\d)")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
# A claim naming one of the prompt example's identifiers that its source lacks was
# copied from the example (TRPC6: a phase 2 FSGS abstract became "BI 764198 … showed a
# 37% objective response rate in previously treated NSCLC patients").
_EXAMPLE_CLAIM_RE = re.compile(r'"(?:claim_text|text|title)":\s*"([^"]+)"')


def _source_text(ev: Evidence) -> str:
    """Everything the extraction prompt showed for ``ev`` (title and full text)."""
    text = (
        _structured_text(ev)
        if ev.evidence_type in _STRUCTURED_TYPES
        else _generic_screenable_text(ev)
    )
    return f"{ev.extra.get('title', ev.source)}\n{text}"


def _numbers(text: str) -> list[str]:
    return [m.group(0) for m in _CLAIM_NUMBER_RE.finditer(_THOUSANDS_RE.sub("", text))]


_NUMBER_WORDS = {
    w: n
    for n, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
        ]
    )
}
_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)
_ROMAN_PHASE_RE = re.compile(r"\bphase\s*(iv|i{1,3})\b", re.IGNORECASE)
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}


def _source_numbers(source: str) -> list[float]:
    """Numbers the source states, including spelled-out ones and roman phase numerals."""
    known = [float(n) for n in _numbers(source)]
    known += [float(_NUMBER_WORDS[m.group(1).lower()]) for m in _NUMBER_WORD_RE.finditer(source)]
    known += [float(_ROMAN[m.group(1).lower()]) for m in _ROMAN_PHASE_RE.finditer(source)]
    return known


def _number_grounded(number: str, known: list[float]) -> bool:
    """``number`` equals a source number to the precision it is written with."""
    decimals = len(number.split(".")[1]) if "." in number else 0
    value, tolerance = float(number), 0.5 * 10**-decimals
    return any(abs(value - k) <= tolerance + 1e-12 for k in known)


def _is_identifier(word: str) -> bool:
    """An acronym or coded name (NSCLC, KRAS, G12C, AMG-510), not an ordinary word."""
    return any(ch.isdigit() for ch in word) or sum(ch.isupper() for ch in word) >= 2


def example_words(skill_text: str) -> frozenset[str]:
    """Lower-cased identifiers in the skill's worked example (its quoted text and claims)."""
    return frozenset(
        w.lower()
        for quoted in _EXAMPLE_CLAIM_RE.findall(skill_text)
        for w in _WORD_RE.findall(quoted)
        if _is_identifier(w)
    )


# A percentage written right after a number ("15%", "15 percent").
_PERCENT_AFTER_RE = re.compile(r"\s*(?:%|per\s?cent\b)", re.IGNORECASE)


def _derived_percentages(known: list[float]) -> list[float]:
    """Percentages a claim may legitimately derive from the source's ratios: a hazard,
    risk or odds ratio of 0.85 is a 15% reduction (and 85% of the comparator); a ratio
    of 1.29 is a 29% increase. Only ratios are used (0 < x < 3, written with decimals)."""
    out: list[float] = []
    for k in known:
        if 0 < k < 1:
            out += [round((1 - k) * 100, 6), round(k * 100, 6)]
        elif 1 < k < 3:
            out.append(round((k - 1) * 100, 6))
    return out


def _percent_numbers(text: str) -> set[str]:
    """Numbers in ``text`` that are written as percentages."""
    flat = _THOUSANDS_RE.sub("", text)
    return {
        m.group(0)
        for m in _CLAIM_NUMBER_RE.finditer(flat)
        if _PERCENT_AFTER_RE.match(flat, m.end())
    }


def ungrounded_reason(claim_text: str, source: str, example: frozenset[str] = frozenset()) -> str:
    """Why an LLM claim is not supported by its source text ("" when it is): a number the
    source does not carry, or wording copied from the prompt's example.

    A percentage counts as grounded when it is the relative effect of a ratio the source
    states (HR 0.75 → "25% lower risk"), since that is how trial results are usually
    paraphrased; any other number must appear in the source."""
    known = _source_numbers(source)
    derived = _derived_percentages([k for k in known if k != int(k)])
    percents = _percent_numbers(claim_text)
    for number in _numbers(claim_text):
        if "." not in number and 1900 <= int(number) <= 2100:
            continue
        if _number_grounded(number, known):
            continue
        if number in percents and _number_grounded(number, derived):
            continue
        return f"number {number} not in source"
    source_words = {w.lower() for w in _WORD_RE.findall(source)}
    leaked = sorted({w.lower() for w in _WORD_RE.findall(claim_text)} & example - source_words)
    if leaked:
        return f"example identifier not in source: {', '.join(leaked)}"
    return ""


def _parse_extraction(raw: str, source_evidences: list[Evidence]) -> list[dict[str, Any]]:
    """Parse LLM output into per-document claim lists; fall back on error."""
    try:
        data = json.loads(strip_json_fence(raw))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: return placeholder claims so the pipeline stays green.
    return [
        {
            "evidence_id": str(ev.evidence_id),
            "claims": [{"claim_text": "", "confidence": None}],
        }
        for ev in source_evidences
    ]


def _sentence(raw: object, paper: GroundedPaper | None) -> int | None:
    """The claim's sentence number when it is one of the paper's sentences."""
    if paper is None or isinstance(raw, bool):
        return None
    try:
        number = int(str(raw).strip().strip("[]"))
    except (TypeError, ValueError):
        return None
    return number if 0 <= number < len(paper.sentences) else None


def _build_core_claim(
    claim: dict[str, Any],
    source: Evidence,
    run_id: UUID,
    trace_id: str,
    *,
    target_drugs: Mapping[str, str] | None = None,
    paper: GroundedPaper | None = None,
) -> CoreClaim | None:
    text = (claim.get("claim_text") or "").strip()
    if not text:
        return None
    # The target's drug data gives the perturbation here; a literature claim's sentence
    # statement is applied afterwards (claim_effect.with_statement). The direction is
    # derived in code (services/evidence/claim_effect.py).
    effect = settle_effect(claim, text, target_drugs=target_drugs)
    confidence = claim.get("confidence")
    if confidence is not None:
        confidence = float(max(0.0, min(1.0, confidence)))
    # Topics only route literature claims; ignore any tags on structured types. An
    # untagged (or junk-tagged) literature claim defaults to BIOLOGY so it is never
    # silently dropped from every lens — biology is the home for generic mechanism
    # claims and the safety net for unreliable extraction.
    if source.evidence_type in _LITERATURE_TYPES:
        topics = _parse_topics(claim.get("topics")) or [LensTopic.BIOLOGY]
    else:
        topics = []
    prov = Provenance(
        agent_name="services/evidence/claim_extraction",
        tool_name="llm_extraction",
        timestamp=source.provenance.timestamp,
        trace_id=trace_id,
    )
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=source.evidence_id,  # keep the link back to the source document
        run_id=run_id,
        gene=source.gene,
        gene_id=source.gene_id,
        disease=source.disease,
        disease_id=source.disease_id,
        direction=effect.direction,
        perturbation=effect.perturbation,
        effect_basis=effect.effect_basis,
        outcome=effect.outcome,
        off_target=effect.off_target,
        effect_source=effect.effect_source,
        sentence=_sentence(claim.get("sentence"), paper),
        target_relation=target_relation(source),
        population=source.population,
        evidence_type=source.evidence_type,
        claim_text=text,
        confidence=confidence,
        topics=topics,
        availability_date=source.availability_date,
        provenance=prov,
        classification=source.classification,
    )


def _trial_confidence(phase: str, status: str) -> float:
    """Map trial phase/status to a confidence score; floor 0.5."""
    p = phase.upper()
    s = status.upper()
    if any(tok in p for tok in ("PHASE 3", "PHASE III", "PHASE 4", "PHASE IV")):
        base = 0.8
    elif any(tok in p for tok in ("PHASE 2", "PHASE II")):
        base = 0.65
    else:
        base = 0.5
    if "COMPLETED" in s:
        base = min(1.0, base + 0.1)
    return max(0.5, base)


def _has_builder(ev: Evidence) -> bool:
    """A dedicated builder in `structured_claims` covers this record's kind."""
    x = ev.extra or {}
    if ev.evidence_type == EvidenceType.GENETICS:
        return any(
            k in x for k in ("genetic_score", "overall_score", "gwas_pvalue", "diseases_score")
        ) or ("summary" in x)
    if ev.evidence_type == EvidenceType.CONSTRAINT:
        return any(k in x for k in ("loeuf", "pli", "pathogenic"))
    if ev.evidence_type == EvidenceType.FUNCTIONAL_GENOMICS:
        return str(ev.source or "").startswith(("depmap:", "project_score:"))
    return ev.evidence_type in _SUMMARY_CLAIM_TYPES


def structured_claims(ev: Evidence, run_id: UUID, trace_id: str) -> list[CoreClaim]:
    """Build CoreClaims deterministically from structured database evidence (B1).

    Covers OT association, ClinVar P/LP, gnomAD constraint, CRISPR dependency
    (DepMap / Project Score, banded with exact line counts), druggability records
    (UniProt / ChEMBL / DGIdb / TTD) and trials so that
    the genetics lens always has at least one claim to reason from when
    these structured records exist — independent of whether the LLM extraction
    succeeds. Free-text evidence types are left to the LLM pass.
    """
    x = ev.extra or {}
    prov = Provenance(
        agent_name="services/evidence/claim_extraction",
        tool_name="structured_extraction",
        timestamp=ev.provenance.timestamp,
        trace_id=trace_id,
    )

    def _make(text: str, confidence: float) -> CoreClaim:
        return CoreClaim(
            evidence_id=uuid.uuid4(),
            source_evidence_id=ev.evidence_id,
            run_id=run_id,
            gene=ev.gene,
            gene_id=ev.gene_id,
            disease=ev.disease,
            disease_id=ev.disease_id,
            direction=Direction.UNSPECIFIED,
            target_relation=target_relation(ev),
            population=ev.population,
            evidence_type=ev.evidence_type,
            claim_text=text,
            confidence=float(max(0.0, min(1.0, confidence))),
            availability_date=ev.availability_date,
            provenance=prov,
            classification=ev.classification,
        )

    claims: list[CoreClaim] = []

    if ev.evidence_type == EvidenceType.GENETICS:
        gs = x.get("genetic_score")
        if isinstance(gs, (int, float)) and gs >= 0.5:
            strength = "strong" if gs >= 0.7 else "moderate"
            text = (
                f"OpenTargets genetic association {ev.gene}↔{ev.disease_id} "
                f"score={gs:.3f} ({strength})"
            )
            claims.append(_make(text, float(gs)))
        elif "gwas_pvalue" in x or "diseases_score" in x:
            pvalue = x.get("gwas_pvalue")
            score = x.get("diseases_score")
            disease_name = x.get("disease_name") or ev.disease
            sources = ", ".join(x.get("edge_sources") or [])
            if pvalue is not None:
                confidence = 0.9 if pvalue < 5e-8 else 0.6
                text = (
                    f"SPOKE knowledge graph: {ev.gene}↔{disease_name} GWAS association "
                    f"(p={pvalue:.2e}, source={sources or 'GWAS'})"
                )
            else:
                confidence = float(max(0.0, min(1.0, (score or 0.0) / 10.0)))
                text = (
                    f"SPOKE knowledge graph: {ev.gene}↔{disease_name} association "
                    f"(DISEASES score={score}, source={sources or 'DISEASES'})"
                )
            claims.append(_make(text, confidence))
        elif "summary" in x:
            summary = str(x["summary"])[:400]
            if summary:
                claims.append(_make(summary, 0.5))

    elif ev.evidence_type == EvidenceType.CONSTRAINT:
        if ("loeuf" in x or "pli" in x) and "pathogenic" not in x:
            reading = _interpret_constraint(
                gene_symbol=ev.gene or "unknown",
                loeuf=x.get("loeuf"),
                pli=x.get("pli"),
                mis_z=x.get("mis_z"),
                moeuf=x.get("moeuf"),
            )
            if reading.summary_text:
                claims.append(
                    _make(
                        reading.summary_text,
                        0.9 if reading.is_lof_constrained else 0.75,
                    )
                )
        elif "pathogenic" in x:
            p_list = x.get("pathogenic") or []
            lp_list = x.get("likely_pathogenic") or []
            total = len(p_list) + len(lp_list)
            if total > 0:
                consequences: dict[str, int] = {}
                for v in [*p_list, *lp_list]:
                    kind = str(v.get("major_consequence") or "").strip()
                    if kind:
                        consequences[kind] = consequences.get(kind, 0) + 1
                kinds = ", ".join(
                    f"{n} {k}" for k, n in sorted(consequences.items(), key=lambda kv: -kv[1])
                )
                text = (
                    f"{total} Pathogenic/Likely-Pathogenic ClinVar variant(s) in {ev.gene} "
                    f"({len(p_list)} P, {len(lp_list)} LP" + (f"; {kinds}" if kinds else "") + ")"
                )
                high_star = any((v.get("gold_stars") or 0) >= 1 for v in p_list[:5])
                claims.append(_make(text, 0.85 if high_star else 0.6))

    elif ev.evidence_type == EvidenceType.FUNCTIONAL_GENOMICS:
        # CRISPR dependency: one banded claim carrying the exact line counts, so the
        # lens never sees only an LLM paraphrase ("a fitness gene in some lines").
        source = str(ev.source or "")
        dependency = None
        if source.startswith("depmap:"):
            dependency = build_dependency_reading(x, None)
        elif source.startswith("project_score:"):
            dependency = build_dependency_reading(None, x)
        if dependency is not None:
            claims.append(
                _make(
                    f"{ev.gene} CRISPR dependency ({dependency.source}): {dependency.label}.", 0.9
                )
            )

    elif ev.evidence_type == EvidenceType.REGULATORY:
        claims.extend(_make(t, c) for t, c in _regulatory_claim_texts(ev))

    elif ev.evidence_type == EvidenceType.PATENT:
        claims.append(_make(_patent_claim_text(ev), 0.9))

    elif ev.evidence_type == EvidenceType.COMPETITION:
        claims.extend(_make(t, 0.9) for t in _competition_claim_texts(ev))

    elif ev.evidence_type == EvidenceType.CLINICAL_TRIAL:
        phase = (x.get("phase") or "").strip()
        status = (x.get("status") or "").strip()
        interventions = ", ".join(x.get("interventions") or []) or "—"
        conditions = ", ".join(x.get("conditions") or []) or ev.disease
        enrollment = x.get("enrollment")
        text = (
            f"{phase or 'Trial'} {status} clinical trial {ev.source}: "
            f"{interventions} in {conditions}" + (f" (n={enrollment})" if enrollment else "")
        )
        conf = _trial_confidence(phase, status)
        claims.append(_make(text, conf))
        # Posted results: primary outcomes and hard-endpoint secondaries, the trial's
        # clinical-validation evidence.
        for raw in x.get("results") or []:
            with contextlib.suppress(ValueError, TypeError):
                outcome = OutcomeResult.model_validate(raw)
                claims.append(
                    _make(
                        f"{ev.source} ({phase or 'trial'} {status}) {outcome.kind} outcome — "
                        f"{outcome.summary()}"[:600],
                        0.9,
                    )
                )

    # Summary types have no dedicated builder: each record's pre-rendered ``text`` is
    # its one claim, so a new connector of such a type needs no branch here.
    if ev.evidence_type in _SUMMARY_CLAIM_TYPES:
        text = str(x.get("text") or "").strip()
        if text:
            claims.append(_make(text, 0.8))

    # Any other structured record states its facts in its rendered text; with no LLM pass
    # over structured records, that text is its claims. A record a builder above covers
    # keeps the builder's decision, including "no claim" (a weak association, a
    # dependency without line counts, a ClinVar bundle without P/LP variants).
    if not claims and ev.evidence_type in _STRUCTURED_TYPES and not _has_builder(ev):
        claims.extend(_make(t, 0.8) for t in _text_claims(ev))

    return claims


def _first_sentence(text: str, max_chars: int = 200) -> str:
    body = " ".join(str(text or "").split())
    # FDA sections open with their number and heading ("12.1 Mechanism of Action ...").
    body = re.sub(r"^\d+(?:\.\d+)?\s+Mechanism of Action\s*", "", body, flags=re.IGNORECASE)
    end = body.find(". ")
    return (body if end < 0 else body[: end + 1])[:max_chars]


# The phrases the label claims use to say whether a drug's mechanism names the target;
# the regulatory lens reads them back to decide whether any label is in-class.
# Phrases of the label claims that lens input selection pins as decisive.
LABEL_APPROVED_HERE = "is FDA-approved in this indication"
LABEL_BOXED_WARNING = "carries a boxed warning:"
LABEL_MOA_NAMES_TARGET = "labelled mechanism of action names"
LABEL_MOA_OFF_TARGET = "labelled mechanism of action does not name"


def _regulatory_claim_texts(ev: Evidence) -> list[tuple[str, float]]:
    """Claims for an openFDA label (approval, MoA, each indication, boxed warning) or a
    FAERS bundle (report counts and top reactions)."""
    x = ev.extra or {}
    drug = str(x.get("drug_name") or ev.source)
    if ev.source.startswith(FDA_FAERS_PREFIX):
        total = x.get("total_reports") or 0
        if not total:
            return []
        reactions = ", ".join(
            f"{r.get('reaction')} ({r.get('count')})" for r in (x.get("top_reactions") or [])[:5]
        )
        text = (
            f"{drug} FAERS: {total} adverse-event reports, {x.get('serious_reports') or 0} "
            f"serious, {x.get('death_reports') or 0} deaths"
            + (f"; top reactions: {reactions}" if reactions else "")
        )
        return [(text, 0.6)]
    if not ev.source.startswith(FDA_LABEL_PREFIX):
        return []
    out: list[tuple[str, float]] = []
    app = x.get("application_number") or ""
    moa = _first_sentence(x.get("mechanism_of_action") or "")
    out.append(
        (
            f"{drug} is FDA-approved" + (f" ({app})" if app else "") + (f"; {moa}" if moa else ""),
            0.95,
        )
    )
    # Whether this label is evidence about the target at all: a drug acting on other
    # proteins says nothing about the target's safety, only about its own effects.
    if ev.gene:
        if label_acts_on_target(ev):
            out.append((f"{drug}'s {LABEL_MOA_NAMES_TARGET} {ev.gene}", 0.95))
        else:
            out.append(
                (
                    f"{drug}'s {LABEL_MOA_OFF_TARGET} {ev.gene}; its label "
                    f"safety findings are drug effects, not {ev.gene} effects",
                    0.95,
                )
            )
    for indication in label_indications(x.get("indications_and_usage") or ""):
        if indicates_disease(indication, ev.disease):
            out.append((f"{drug} {LABEL_APPROVED_HERE} ({ev.disease}): {indication}", 0.95))
        else:
            out.append((f"{drug} is FDA-approved for another indication: {indication}", 0.95))
    boxed = " ".join(str(x.get("boxed_warning") or "").split())
    # Whose warning it is decides whether denying it is wrong: the regulatory lens said
    # "no boxed warning on in-class drugs", correctly, beside a competitor's boxed label,
    # was flagged, and its repair invented a boxed warning for an in-class drug.
    of_target = f", of a drug acting on {ev.gene}," if ev.gene and label_acts_on_target(ev) else ""
    if boxed:
        out.append((f"{drug} label{of_target} {LABEL_BOXED_WARNING} {boxed[:200]}", 0.95))
    else:
        out.append((f"{drug} label carries no boxed warning", 0.95))
    return out


def _patent_claim_text(ev: Evidence) -> str:
    """One IP fact per patent: what it is, who owns it, when filed, whether it names the target."""
    x = ev.extra or {}
    title = " ".join(str(x.get("title") or "").split()) or "untitled"
    meta = ", ".join(
        p
        for p in (
            str(x.get("assignee") or "").strip(),
            f"filed {x['filing_date']}" if x.get("filing_date") else "",
        )
        if p
    )
    scope = (
        f"names the target {ev.gene}"
        if is_on_target_patent(ev)
        else f"names the indication but not the target {ev.gene}"
    )
    return f"Patent {ev.source} '{title}'" + (f" ({meta})" if meta else "") + f" {scope}"


def _competition_claim_texts(ev: Evidence) -> list[str]:
    """The indication's drug and trial landscape, regardless of mechanism."""
    x = ev.extra or {}
    out: list[str] = []
    if "approved_drug_count" in x:
        out.append(
            f"{ev.disease} indication landscape: {x.get('approved_drug_count') or 0} "
            f"FDA-approved drug(s) in the indication"
        )
    if "total_trial_count" in x:
        out.append(
            f"{ev.disease} indication landscape: {x.get('active_trial_count') or 0} active "
            f"clinical trial(s), {x.get('phase3_trial_count') or 0} at phase 3, of "
            f"{x.get('total_trial_count') or 0} registered"
        )
    return out


async def extract_claims(
    evidences: list[Evidence],
    target_gene: str,
    disease: str,
    ctx: RunContext,
    *,
    gene_aliases: Iterable[str] = (),
    target_drugs: Mapping[str, str] | None = None,
    papers: Mapping[str, GroundedPaper] | None = None,
    dropped: list[DroppedClaim] | None = None,
) -> list[CoreClaim]:
    """Extract atomic CoreClaims from screened evidence via LLM.

    Each evidence document may yield 1-N atomic claims. Claims with empty
    claim_text are discarded. The returned list is empty if evidences is empty.

    The run's requested therapeutic direction is deliberately not given to the model:
    shown it, the model tagged claims with it (an activation run labelled "lower
    LDL-C at this locus lowers risk" as support for activation), so each claim's
    direction must be read from the claim alone. ``target_drugs`` ({upper-case drug
    name: decrease | increase}, the target's known drugs) types a claim naming them by
    the drug's action. ``papers`` ({evidence id: grounded paper}) number a paper's
    sentences; its claims carry the sentence they rest on, whose statement types them
    later (claim_effect.with_statement). ``gene_aliases`` is accepted for callers and
    unused.

    Every claim left out by the grounding checks is appended to ``dropped`` when given.
    """
    if not evidences:
        return []

    skill_text = ctx.load_skill("claim_extraction")
    example = example_words(skill_text)
    classification = classify(evidences)
    provider, _model = ctx.router.select(classification, "claim_extraction")
    grounded = papers or {}

    def summary(ev: Evidence) -> str:
        return _evidence_summary(ev, grounded.get(str(ev.evidence_id)))

    all_claims: list[CoreClaim] = []
    drops: list[DroppedClaim] = []
    run_id = evidences[0].run_id
    trace_id = ctx.trace_id

    for ev in evidences:
        if ev.evidence_type in _DETERMINISTIC_ONLY_TYPES:
            all_claims.extend(structured_claims(ev, run_id, trace_id))
    llm_evidences = [e for e in evidences if e.evidence_type not in _DETERMINISTIC_ONLY_TYPES]

    batches = pack_batches(
        llm_evidences,
        summary,
        _MAX_BATCH_INPUT_TOKENS,
        _BATCH_SIZE,
        get_estimator().ratio(task="claim_extraction"),
    )
    for batch_index, batch in enumerate(batches):
        batch_num = batch_index + 1

        # B1: Build deterministic claims for structured/deterministic types first so
        # the genetics and clinical lenses always have signal even when LLM extraction fails.
        det_claims: list[CoreClaim] = []
        for ev in batch:
            if ev.evidence_type in _DETERMINISTIC_TYPES:
                det_claims.extend(structured_claims(ev, run_id, trace_id))
        all_claims.extend(det_claims)

        docs_json = "[\n" + ",\n".join(summary(e) for e in batch) + "\n]"

        user_content = (
            f"Gene: {target_gene}\n"
            f"Disease: {disease}\n\n"
            f"Evidence documents:\n{docs_json}\n\n"
            f"Extract up to {_MAX_CLAIMS_PER_DOC} atomic claims per document."
        )

        struct_docs_in_batch = sum(1 for ev in batch if ev.evidence_type in _DETERMINISTIC_TYPES)

        async with span(
            "claim_extraction:batch",
            trace_id=trace_id,
            input_data=f"batch {batch_num}, {len(batch)} docs",
        ) as s:
            completion = await provider.complete(
                CompletionRequest(
                    messages=[{"role": "user", "content": user_content}],
                    system=skill_text,
                    classification=classification,
                    task="claim_extraction",
                    max_tokens=len(batch) * _OUTPUT_TOKENS_PER_DOC,
                    model_override=_model,
                )
            )
            extraction = _parse_extraction(completion.content, batch)
            llm_claim_count = sum(len(d.get("claims", [])) for d in extraction)

            # B3: Loss guard — log when LLM produces nothing for structured docs.
            if struct_docs_in_batch > 0 and llm_claim_count == 0:
                logger.warning(
                    "claim_extraction batch %d: LLM returned 0 claims for %d structured doc(s) "
                    "— deterministic fallback covers them",
                    batch_num,
                    struct_docs_in_batch,
                )
                s.set_attribute("extraction.dropped_structured", struct_docs_in_batch)

            s.set_attribute(
                "gen_ai.completion",
                f"{llm_claim_count} LLM + {len(det_claims)} structured claims from {len(batch)} docs",
            )

        evidence_by_id = {str(ev.evidence_id): ev for ev in batch}
        for doc in extraction:
            source_ev = evidence_by_id.get(doc.get("evidence_id", ""))
            if source_ev is None:
                continue
            source_text = _source_text(source_ev)
            for claim_dict in (doc.get("claims") or [])[:_MAX_CLAIMS_PER_DOC]:
                claim = _build_core_claim(
                    claim_dict,
                    source_ev,
                    run_id,
                    trace_id,
                    target_drugs=target_drugs,
                    paper=grounded.get(str(source_ev.evidence_id)),
                )
                if claim is None:
                    continue
                reason = ungrounded_reason(claim.claim_text, source_text, example)
                if reason:
                    logger.warning(
                        "claim_extraction: dropped claim from %s (%s): %s",
                        source_ev.source,
                        reason,
                        claim.claim_text[:200],
                    )
                    drops.append(
                        DroppedClaim(
                            source=source_ev.source,
                            claim_text=claim.claim_text,
                            reason=grounding_reason(reason),
                        )
                    )
                    continue
                all_claims.append(claim)
    if dropped is not None:
        dropped.extend(drops)
    return all_claims
