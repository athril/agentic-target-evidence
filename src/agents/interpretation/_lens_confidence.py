# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Evidence-computed lens confidence and claim-grounding checks.

The lens LLM decides the categorical parts of a verdict — overall verdict, each
axis's verdict, which claims support each axis. The confidence numbers are computed
here from those citations and the deterministic evidence weights, so they are
reproducible, consistent between an axis and its lens, and traceable to claims. The
LLM's own numbers are kept as ``model_confidence`` for audit.

Axis evidence
    The axis's supporting claims are the lens LLM's citations, plus the structured
    records config/axis_evidence.yaml says the axis is about, plus the literature the
    lens citation step picked (_lens_citations.py). Additions are recorded in
    ``added_claim_ids``. Ids the lens was not shown score nothing.

Axis confidence
    Claims are grouped into independent sources — the source document, or a
    ``source_groups`` family such as one patent family — each counting once at its
    strongest claim's weight ``w``, and sources are grouped by kind of evidence (each
    structured evidence type; all literature together). A kind contributes

        s_kind = min(TYPE_CAP, 1 - prod(1 - ALPHA * w))

    and kinds combine as independent lines of evidence:

        confidence = 1 - prod(1 - s_kind)      capped at MAX_CONFIDENCE

    One top-weight source gives 0.5, two of the same kind 0.75 — the most one kind
    can give, however many papers repeat it — and two kinds at that level ~0.94. A
    favourable axis citing a definitive claim (evidence_hierarchy.yaml
    ``definitive``, e.g. a GenCC Strong classification or an FDA approval in the
    indication) scores at least that claim's floor.

Lens confidence (support / oppose)
    Over the axes that agree with the overall verdict: the mean, capped at the
    weakest agreeing axis + AXIS_SPREAD, so one well-evidenced axis cannot carry a
    poorly evidenced one. An axis that cites no evidence (no source, confidence 0) is a
    verdict without support and counts as uncertain here, so it neither caps nor
    discounts the lens. A corroborative axis (computed corroboration, e.g. the direction
    of effect) that agrees is left out of the mean and the cap — a weak, rule-set
    corroboration must not lower the lens it agrees with — unless no other axis agrees.
    Each axis pointing the other way, corroborative or not, scales it by
    (1 - CONTRARY_DISCOUNT * its confidence), except a favourable qualifier axis
    (QUALIFIER_AXES: "was it tested") under an oppose. No agreeing axis → 0, and when the
    scored axes all point the other way the verdict is flagged as contradicting them.

Lens confidence (neutral / insufficient_evidence)
    A non-directional verdict asserts no finding, and there is no claim set to weigh
    for "nothing found", so the LLM's value is kept but capped at
    NON_DIRECTIONAL_CAP.

Neutral verdict against its axes
    A "neutral" verdict whose axes are all scored and all point the same way
    contradicts the lens's own decomposition, so it is replaced by that direction
    (and scored as such), with a flag recording the model's verdict.

Grounding checks, each recording a ValidationFlag; all source- and fact-specific
knowledge lives in config, so they apply to any gene, disease or connector:

- citation repair (config/source_registry.yaml ``attach``): an axis rationale that
  names a source but cites none of its claims gets that source's structured claims
  attached — the LLM reasoned from them but left the ids out.
- numeric grounding (``check_numbers``): a number in a prose sentence that names a
  source must appear, to the precision written, in that source's claims.
- fact checks (config/fact_checks.yaml): prose that denies a fact a shown claim
  states ("no boxed warning" against a label that has one).

Prose is annotated, never rewritten.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from statistics import mean
from typing import Literal

from schemas.evidence import CoreClaim, EvidenceType
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag
from services.evidence.axis_evidence import AxisEvidenceMap, axis_evidence_map
from services.evidence.evidence_hierarchy import definitive_floor
from services.evidence.fact_checks import FactCheck, fact_checks
from services.evidence.guard_core import Violation, annotate, strip_annotations
from services.evidence.source_registry import NamedSource, named_sources

# Bump when the formula, its constants or the checks change: part of the lens cache
# key, so a cached verdict is never served with confidences from an older formula.
SCORING_VERSION = "9"

# Config the scores depend on; their contents are part of the lens cache key too, so
# a config edit is never masked by a cached verdict scored under the old config.
SCORING_CONFIGS = (
    "config/axis_evidence.yaml",
    "config/evidence_hierarchy.yaml",
    "config/source_registry.yaml",
    "config/fact_checks.yaml",
    "config/lens_inputs.yaml",
)

ALPHA = 0.5  # one source contributes at most half the remaining headroom
TYPE_CAP = 0.75  # one kind of evidence alone: at most two strong sources' worth
MAX_CONFIDENCE = 0.95  # never certain: the evidence pool is a sample
AXIS_SPREAD = 0.2  # lens score ≤ weakest agreeing axis + this
CONTRARY_DISCOUNT = 0.5  # a fully confident contrary axis halves the lens score
NON_DIRECTIONAL_CAP = 0.5

# Axes that say a question was asked, not how it was answered: favourable when the
# hypothesis was tested in the clinic, which a failed programme was. They agree with a
# support verdict but never count against an oppose (a failed phase 3 halved the
# clinical oppose through its own "tested in the clinic" axis).
QUALIFIER_AXES = frozenset({"clinical_precedent"})

# Literature is never auto-attached: a paper's claims are too varied to infer which
# one the rationale meant.
_LITERATURE_TYPES = frozenset(
    {EvidenceType.ARTICLE, EvidenceType.ABSTRACT, EvidenceType.BOOK, EvidenceType.CONFERENCE}
)

_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+")
# A number standing on its own: not part of an identifier ("NDA216403", "Q889K",
# "gnomAD v4", "11q"); sign and trailing "%" are ignored on both sides.
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?(?![A-Za-z0-9_]|\.\d)")
_YEAR_RANGE = range(1900, 2101)
_GROUNDING_MARKER = "NUMBER CHECK"
_FACT_MARKER = "FACT CHECK"


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def _noisy_or(weights: Iterable[float]) -> float:
    remaining = 1.0
    for w in weights:
        remaining *= 1.0 - ALPHA * max(0.0, min(1.0, w))
    return 1.0 - remaining


def _evidence_kind(claim: CoreClaim) -> str:
    return "literature" if claim.evidence_type in _LITERATURE_TYPES else claim.evidence_type.value


def score_axis(
    axis: AxisVerdict,
    pool_by_id: dict[str, CoreClaim],
    weight: Callable[[CoreClaim], float],
    source_groups: dict[str, str],
    axis_floor: Callable[[CoreClaim], float] | None = None,
) -> AxisVerdict:
    """Recompute one axis's confidence from its supporting claims (see module docstring)."""
    by_kind: dict[str, dict[str, float]] = {}
    floor = 0.0
    picks = set(axis.literature_picks)
    for cid in axis.supporting_claim_ids:
        claim = pool_by_id.get(cid)
        if claim is None:
            continue
        # Reviewed axis: literature counts only through the citation step's picks,
        # whose input is stable across runs; the lens model's own literature
        # citations vary with its wording (they stay listed, they just don't score).
        if (
            axis.literature_reviewed
            and claim.evidence_type in _LITERATURE_TYPES
            and cid not in picks
        ):
            continue
        src = str(claim.source_evidence_id or claim.evidence_id)
        key = source_groups.get(src, src)
        sources = by_kind.setdefault(_evidence_kind(claim), {})
        sources[key] = max(sources.get(key, 0.0), weight(claim))
        if axis.verdict is True:
            floor = max(floor, definitive_floor(claim.evidence_type, claim.claim_text) or 0.0)
            if axis_floor is not None:
                floor = max(floor, axis_floor(claim))
    remaining = 1.0
    for sources in by_kind.values():
        remaining *= 1.0 - min(TYPE_CAP, _noisy_or(sources.values()))
    confidence = min(MAX_CONFIDENCE, max(1.0 - remaining, floor))
    return axis.model_copy(
        update={
            "confidence": round(confidence, 3),
            "model_confidence": axis.confidence
            if axis.model_confidence is None
            else axis.model_confidence,
            "evidence_sources": sum(len(s) for s in by_kind.values()),
        }
    )


def _axis_floor(
    evidence_map: AxisEvidenceMap, lens: str, axis: str
) -> Callable[[CoreClaim], float]:
    def floor(claim: CoreClaim) -> float:
        return evidence_map.floor_for(lens, axis, claim.claim_text)

    return floor


def attach_axis_evidence(
    lens: str, axis: AxisVerdict, pool: list[CoreClaim], evidence_map: AxisEvidenceMap
) -> AxisVerdict:
    """Attach the structured records config says this axis is about."""
    if not evidence_map.types_for(lens, axis.axis):
        return axis
    new = [
        str(c.evidence_id)
        for c in pool
        if evidence_map.attaches(lens, axis.axis, c.evidence_type.value, c.claim_text)
        and str(c.evidence_id) not in axis.supporting_claim_ids
    ]
    if not new:
        return axis
    return axis.model_copy(
        update={
            "supporting_claim_ids": [*axis.supporting_claim_ids, *new],
            "added_claim_ids": [*axis.added_claim_ids, *new],
        }
    )


def lens_confidence(verdict: LensVerdict, axes: list[AxisVerdict], model_value: float) -> float:
    """Aggregate scored axes into the lens confidence (see module docstring)."""
    if verdict.overall_verdict not in ("support", "oppose"):
        return min(model_value, NON_DIRECTIONAL_CAP)
    agree = verdict.overall_verdict == "support"
    # PCSK9 commercial: a "market size: No" citing nothing capped the lens at 0 + 0.2.
    evidenced = [a for a in axes if a.evidence_sources or a.confidence]
    agreeing_axes = [a for a in evidenced if a.verdict is agree]
    primary = [a for a in agreeing_axes if not a.corroborative]
    agreeing = [a.confidence for a in primary or agreeing_axes]
    if not agreeing:
        return 0.0
    score = min(mean(agreeing), min(agreeing) + AXIS_SPREAD)
    for a in evidenced:
        if a.verdict is (not agree) and not (a.verdict and a.axis in QUALIFIER_AXES):
            score *= 1.0 - CONTRARY_DISCOUNT * a.confidence
    return round(score, 3)


def align_neutral_with_axes(verdict: LensVerdict) -> LensVerdict:
    """Replace a "neutral" verdict whose axes are all scored and agree (see module
    docstring); any other verdict is returned unchanged."""
    if verdict.overall_verdict != "neutral" or not verdict.axes:
        return verdict
    directions = {a.verdict for a in verdict.axes}
    if directions == {True}:
        aligned: Literal["support", "oppose"] = "support"
    elif directions == {False}:
        aligned = "oppose"
    else:
        return verdict
    word = "favourable" if aligned == "support" else "unfavourable"
    flag = ValidationFlag(
        lens=verdict.lens,
        severity="medium",
        rule_id="neutral_axis_conflict",
        claim_excerpt="",
        message=(
            f"The model's verdict was 'neutral' but all {len(verdict.axes)} axes are "
            f"{word}; the verdict follows the axes ('{aligned}')."
        ),
    )
    return verdict.model_copy(
        update={
            "overall_verdict": aligned,
            "validation_flags": [*verdict.validation_flags, flag],
        }
    )


def reaggregate(verdict: LensVerdict, *, align: bool = True) -> LensVerdict:
    """Recompute the lens confidence from its current axes.

    For deterministic post-scoring adjustments that change an axis (e.g. the genetics
    lens's Mendelian causality floor), so the lens number stays consistent with them.
    ``align=False`` keeps a "neutral" a rule set on purpose although every axis agrees
    (`align_neutral_with_axes` would turn it back).
    """
    if align:
        verdict = align_neutral_with_axes(verdict)
    model_value = (
        verdict.confidence if verdict.model_confidence is None else verdict.model_confidence
    )
    return verdict.model_copy(
        update={
            "confidence": lens_confidence(verdict, list(verdict.axes), model_value),
            "model_confidence": model_value,
        }
    )


def contradicts_axes(verdict: LensVerdict) -> bool:
    """A directional verdict whose scored axes all point the other way."""
    if verdict.overall_verdict not in ("support", "oppose"):
        return False
    agree = verdict.overall_verdict == "support"
    scored = [a.verdict for a in verdict.axes if a.verdict is not None]
    return bool(scored) and all(v is (not agree) for v in scored)


# ---------------------------------------------------------------------------
# Grounding checks
# ---------------------------------------------------------------------------


def _repair_citations(
    axis: AxisVerdict, pool: list[CoreClaim], sources: Iterable[NamedSource]
) -> tuple[list[str], list[str]]:
    """Ids to attach for registry sources the rationale names but does not cite.

    Returns (attached claim ids, repaired source names).
    """
    rationale = strip_annotations(axis.rationale)
    by_id = {str(c.evidence_id): c for c in pool}
    cited_text = " ".join(by_id[i].claim_text for i in axis.supporting_claim_ids if i in by_id)
    attached: list[str] = []
    repaired: list[str] = []
    for src in sources:
        if not src.attach or not src.mentioned_in(rationale) or src.mentioned_in(cited_text):
            continue
        hits = [
            str(c.evidence_id)
            for c in pool
            if c.evidence_type not in _LITERATURE_TYPES
            and src.mentioned_in(c.claim_text)
            and str(c.evidence_id) not in axis.supporting_claim_ids
            and str(c.evidence_id) not in attached
        ]
        if hits:
            attached.extend(hits)
            repaired.append(src.name)
    return attached, repaired


_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def _numbers(text: str) -> list[str]:
    return [m.group(0) for m in _NUMBER_RE.finditer(_THOUSANDS_RE.sub("", text or ""))]


def _grounded(number: str, known: list[float]) -> bool:
    """``number`` equals a known value to the precision it is written with."""
    value = float(number)
    decimals = len(number.split(".")[1]) if "." in number else 0
    tolerance = 0.5 * 10**-decimals
    return any(abs(value - k) <= tolerance + 1e-12 for k in known)


def numeric_grounding_violations(
    text: str,
    pool: list[CoreClaim],
    sources: Iterable[NamedSource],
    context: str = "",
) -> list[Violation]:
    """Numbers in sentences naming a ``check_numbers`` source that neither a claim nor a
    prompt-context line naming one of the sentence's sources carries.

    ``context`` is the structured text the lens prompt carried besides the claims
    (readings, FAERS rates, ClinVar breakdowns); a figure quoted from it is grounded.
    """
    checked = [s for s in sources if s.check_numbers]
    all_sources = list(sources)
    # Whole lines: a context record joins its fields with ". " ("FAERS — X: 5,866
    # reports. serious_rate=16.6%."), and only its first field names the source.
    context_lines = [line for line in (context or "").splitlines() if line.strip()]
    # Whole blocks: a pre-rendered table is a block headed by its source ("<Source>
    # CRISPR dependency: …" then one line per lineage), and its rows do not repeat the
    # name; a correct per-lineage figure was flagged, and its repair made it wrong.
    context_blocks = [
        [line for line in block.splitlines() if line.strip()]
        for block in re.split(r"\n\s*\n", context or "")
        if block.strip()
    ]
    found: list[Violation] = []
    for sentence in _SENTENCE_RE.split(strip_annotations(text)):
        named_checked = [s for s in checked if s.mentioned_in(sentence)]
        if not named_checked:
            continue
        named = [s for s in all_sources if s.mentioned_in(sentence)]
        source_texts = [
            c.claim_text for c in pool if any(s.mentioned_in(c.claim_text) for s in named)
        ]
        source_texts += [line for line in context_lines if any(s.mentioned_in(line) for s in named)]
        source_texts += [
            line
            for block in context_blocks
            if any(s.mentioned_in(block[0]) for s in named)
            for line in block[1:]
        ]
        names = ", ".join(s.name for s in named_checked)
        # Nothing from the source reached the lens: a figure attributed to it can only
        # come from the model's memory (IL17A × psoriasis: "LOEUF 0.28, pLI 0.95" in a
        # run with no gnomAD record; the gene's real LOEUF is 0.358).
        shown = any(s.mentioned_in(t) for s in named_checked for t in source_texts)
        known = [float(n) for t in source_texts for n in _numbers(t)] if shown else []
        for number in _numbers(sentence):
            if ("." not in number and int(number) in _YEAR_RANGE) or _grounded(number, known):
                continue
            note = (
                f"[⚠ {_GROUNDING_MARKER}: {number} does not appear in the {names} "
                f"claim(s) this lens was given — quote the source's figure.]"
                if shown
                else f"[⚠ {_GROUNDING_MARKER}: no {names} data reached this lens in this run, "
                f"so {number} cannot come from the evidence — remove it or name its source.]"
            )
            found.append(
                Violation(
                    rule_id="grounding.number" if shown else "grounding.number_unsourced",
                    kind="contradiction",
                    excerpt=sentence[:300],
                    note=note,
                )
            )
    return found


def fact_check_violations(
    text: str, pool: list[CoreClaim], checks: Iterable[FactCheck], gene: str, disease: str
) -> list[tuple[FactCheck, Violation]]:
    """Prose sentences that deny a fact some shown claim states."""
    claim_texts = [c.claim_text.lower() for c in pool]
    found: list[tuple[FactCheck, Violation]] = []
    body = strip_annotations(text)
    for check in checks:
        if not any(check.claim_matches(t, gene, disease) for t in claim_texts):
            continue
        denial = check.denial_re(gene, disease)
        for sentence in _SENTENCE_RE.split(body):
            if denial.search(sentence):
                found.append(
                    (
                        check,
                        Violation(
                            rule_id=f"fact.{check.id}",
                            kind="contradiction",
                            excerpt=sentence[:300],
                            note=f"[⚠ {_FACT_MARKER}: {check.message}]",
                        ),
                    )
                )
    return found


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def prose_violations(
    text: str, pool: list[CoreClaim], context: str, gene: str, disease: str
) -> list[Violation]:
    """Numeric-grounding and fact-check violations in one prose field (no side effects)."""
    if not text:
        return []
    found = numeric_grounding_violations(text, pool, named_sources(), context)
    found += [v for _, v in fact_check_violations(text, pool, fact_checks(), gene, disease)]
    return found


# Confidence multiplier for an axis whose rationale fails a fact check.
FACT_CHECK_AXIS_PENALTY = 0.6


def check_prose(verdict: LensVerdict, pool: list[CoreClaim], context: str = "") -> LensVerdict:
    """Run the grounding checks over every prose field: annotate and flag, never rewrite."""
    sources = named_sources()
    checks = fact_checks()
    grounding: list[Violation] = []
    facts: list[tuple[FactCheck, Violation]] = []

    def _check(text: str) -> str:
        if not text:
            return text
        g = numeric_grounding_violations(text, pool, sources, context)
        f = fact_check_violations(text, pool, checks, verdict.target_gene, verdict.disease)
        grounding.extend(g)
        facts.extend(f)
        return annotate(text, [*g, *(v for _, v in f)])

    rationale = _check(verdict.rationale)
    narrative = _check(verdict.narrative)
    axes = []
    for a in verdict.axes:
        before = len(facts)
        rationale_a = _check(a.rationale)
        update: dict[str, object] = {"rationale": rationale_a}
        # An axis whose own rationale contradicts a deterministic fact rests on a false
        # premise: keep its verdict (the model may be right for other reasons) but stop
        # it carrying full weight into the lens aggregate.
        if len(facts) > before and a.confidence is not None:
            update["confidence"] = round(a.confidence * FACT_CHECK_AXIS_PENALTY, 3)
        axes.append(a.model_copy(update=update))
    flags = list(verdict.validation_flags)
    if grounding:
        flags.append(
            ValidationFlag(
                lens=verdict.lens,
                severity="medium",
                rule_id="numeric_grounding",
                claim_excerpt=grounding[0].excerpt[:300],
                message=(
                    f"{len(grounding)} number(s) in the prose do not appear in the claims of "
                    f"the source they are attributed to; annotated rather than rewritten."
                ),
            )
        )
    for check_id in dict.fromkeys(c.id for c, _ in facts):
        check, violation = next((c, v) for c, v in facts if c.id == check_id)
        flags.append(
            ValidationFlag(
                lens=verdict.lens,
                severity="medium",
                rule_id=f"fact_check.{check.id}",
                claim_excerpt=violation.excerpt[:300],
                message=check.message,
            )
        )
    return verdict.model_copy(
        update={
            "rationale": rationale,
            "narrative": narrative,
            "axes": axes,
            "validation_flags": flags,
        }
    )


def score_verdict(
    verdict: LensVerdict,
    pool: list[CoreClaim],
    weight: Callable[[CoreClaim], float],
    *,
    source_groups: dict[str, str] | None = None,
    context: str = "",
    run_checks: bool = True,
) -> LensVerdict:
    """Replace the LLM's confidences with evidence-computed ones and, unless
    ``run_checks`` is False, run the prose grounding checks (`check_prose`).

    ``pool`` must be exactly the claims the lens LLM was shown; ``weight`` the
    lens's deterministic claim weight; ``context`` the rest of its prompt input.
    run_lens scores with ``run_checks=False`` so the checks see the prose only once,
    after the repair step.
    """
    groups = source_groups or {}
    sources = named_sources()
    evidence_map = axis_evidence_map()
    pool_by_id = {str(c.evidence_id): c for c in pool}
    flags = list(verdict.validation_flags)

    def _flag(
        severity: Literal["high", "medium", "low"], rule_id: str, excerpt: str, message: str
    ) -> None:
        flags.append(
            ValidationFlag(
                lens=verdict.lens,
                severity=severity,
                rule_id=rule_id,
                claim_excerpt=excerpt[:300],
                message=message,
            )
        )

    axes: list[AxisVerdict] = []
    for axis in verdict.axes:
        attached, repaired = _repair_citations(axis, pool, sources)
        if attached:
            axis = axis.model_copy(
                update={
                    "supporting_claim_ids": [*axis.supporting_claim_ids, *attached],
                    "added_claim_ids": [*axis.added_claim_ids, *attached],
                }
            )
            _flag(
                "low",
                "citation_repair",
                strip_annotations(axis.rationale),
                f"Axis '{axis.axis}' names {', '.join(repaired)} without citing its claims; "
                f"attached {len(attached)} claim(s) from the lens input.",
            )
        axis = attach_axis_evidence(verdict.lens, axis, pool, evidence_map)
        unknown = [i for i in axis.supporting_claim_ids if i not in pool_by_id]
        if unknown:
            _flag(
                "low",
                "unknown_claim_ids",
                ", ".join(unknown[:5]),
                f"Axis '{axis.axis}' cites {len(unknown)} claim id(s) the lens was not shown; "
                f"they do not count toward its confidence.",
            )
        axes.append(
            score_axis(
                axis, pool_by_id, weight, groups, _axis_floor(evidence_map, verdict.lens, axis.axis)
            )
        )

    model_value = (
        verdict.confidence if verdict.model_confidence is None else verdict.model_confidence
    )
    scored = verdict.model_copy(update={"axes": axes})
    aligned = align_neutral_with_axes(scored)
    if aligned is not scored:
        verdict = verdict.model_copy(update={"overall_verdict": aligned.overall_verdict})
        flags.append(aligned.validation_flags[-1])
    if contradicts_axes(scored):
        _flag(
            "medium",
            "verdict_axis_conflict",
            verdict.rationale,
            f"Overall verdict '{verdict.overall_verdict}' contradicts every scored axis; "
            f"its confidence is 0 because no axis supports it.",
        )
    result = verdict.model_copy(
        update={
            "axes": axes,
            "confidence": lens_confidence(verdict, axes, model_value),
            "model_confidence": model_value,
            "validation_flags": flags,
        }
    )
    return check_prose(result, pool, context) if run_checks else result
