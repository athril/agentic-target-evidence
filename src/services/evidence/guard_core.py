# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared primitives for the post-LLM lens-output guards.

Pure module — no I/O, no domain knowledge, fully unit-testable.

Every guard answers the same question — "does this prose assert something the
deterministic facts contradict?" — and the recurring false positives all came
from judging *words* instead of *claims*:

  * scope   — "underserved at the target level" was flagged as an indication claim
              because a scope noun sat within a fixed character window;
  * stance  — "the absence of GWAS signal is expected" was flagged as treating the
              absence as negative evidence, although it says the opposite;
  * syntax  — a negation in one clause ("not constrained; haploinsufficiency is
              likely") leaked into the next, and a comma broke a word-window match.

These helpers give every guard the same notion of sentence, clause, negation and
annotation, so a fix to one of them fixes it for all guards at once:

    text ──▶ strip_annotations() ──▶ split_sentences() ──▶ split_clauses()
                                                        └─▶ is_negated()
    violations ──▶ annotate()   (append-only, idempotent, de-duplicated)
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------

# contradiction — the claim is false given the facts (e.g. "haploinsufficient" at LOEUF 0.76)
# overstatement — directionally right but stronger than the facts allow ("underserved field")
# terminology   — the intended meaning matches the facts, the term is misused
#                 ("tolerance to haploinsufficiency" meaning LoF tolerance)
ViolationKind = Literal["contradiction", "overstatement", "terminology"]


class Violation(BaseModel):
    """One guard finding: which rule fired, on what excerpt, and the note to append."""

    model_config = ConfigDict(frozen=True)

    rule_id: str  # stable, dotted: "<family>.<check>", e.g. "constraint.haploinsufficiency"
    kind: ViolationKind
    excerpt: str = ""  # the clause/sentence that triggered the rule
    note: str  # the full "[⚠ … GUARD: …]" annotation appended to the text


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

# A guard annotation as appended by `annotate`: "[⚠ <MARKER>: …]". Annotation bodies
# never contain "]" — guards must keep it that way so this stays a flat match.
_ANNOTATION_RE = re.compile(r"\s*\[⚠ [^\]]*\]")


def strip_annotations(text: str) -> str:
    """Remove guard annotations so a guard never reacts to another guard's note.

    Annotations quote the very phrasing they warn about ("'underserved' applied to
    the indication"), so a guard running after another guard on the same field
    would otherwise flag that note. Checks run on the stripped text; notes are
    appended to the original.
    """
    return _ANNOTATION_RE.sub("", text or "").strip()


def annotate(text: str, violations: list[Violation]) -> str:
    """Append each distinct violation note to ``text`` (never rewrite the prose).

    Idempotent: a note already present in ``text`` is not appended again, so
    running the same guard twice leaves the text unchanged.
    """
    if not violations:
        return text
    notes: list[str] = []
    for v in violations:
        if v.note not in notes and v.note not in text:
            notes.append(v.note)
    if not notes:
        return text
    return "\n".join([text, *notes])


# ---------------------------------------------------------------------------
# Sentence and clause segmentation
# ---------------------------------------------------------------------------

# Sentence boundary: terminal punctuation followed by whitespace and an uppercase
# letter/digit/opening bracket, or a newline. Common abbreviations are protected.
_SENTENCE_SPLIT = re.compile(
    r"(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bvs\.)(?<!\bal\.)(?<!\bapprox\.)"
    r"(?<=[.!?])\s+(?=[A-Z0-9(\[\"'])"
    r"|\n+"
)

# Clause boundary inside a sentence: semicolons, dashes used as separators, and
# contrastive / consequential connectives that start a new predication. Each
# clause is the unit in which scope and negation are resolved.
_CLAUSE_SPLIT = re.compile(
    r"\s*;\s*"
    r"|\s+[—–]\s+|\s+-{2}\s+"
    r"|,?\s+\b(?:but|whereas|although|though|however)\b,?\s+"
    r"|,\s+(?:while|indicating|suggesting|implying|meaning|which|thereby)\b\s*",
    re.IGNORECASE,
)


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences (table-cell and multi-paragraph safe)."""
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s and s.strip()]


def split_clauses(sentence: str) -> list[str]:
    """Split one sentence into clauses at contrastive/consequential boundaries."""
    return [c.strip() for c in _CLAUSE_SPLIT.split(sentence or "") if c and c.strip()]


def iter_clauses(text: str) -> list[str]:
    """All clauses of ``text``, in order (sentences split, then clauses)."""
    return [c for s in split_sentences(text) for c in split_clauses(s)]


# ---------------------------------------------------------------------------
# Negation
# ---------------------------------------------------------------------------

NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|none|never|without|neither|nor|cannot|lacks?|lacking|absent|"
    r"absence of|non[- ]significant|insignificant|minimal|negligible|\w+n't)\b",
    re.IGNORECASE,
)
# "non-disease-relevant", "non-essential": a "non-" prefix glued to the match.
_NON_PREFIX = re.compile(r"\bnon[- ]?$", re.IGNORECASE)
NEGATION_LOOKBACK_CHARS = 40
# A negation scopes over an or/nor-coordinated list: in "does not indicate
# haploinsufficiency or severe missense constraint" the second item is negated too,
# even when it sits beyond the plain lookback window.
_COORDINATED_NEGATION = re.compile(
    r"\b(?:no|not|neither|without|cannot|\w+n't)\b[^,;.]{0,80}\b(?:or|nor)\s+(?:[\w-]+\s+){0,3}$",
    re.IGNORECASE,
)


def is_negated(segment: str, start: int, lookback: int = NEGATION_LOOKBACK_CHARS) -> bool:
    """True if the match at ``start`` in ``segment`` is negated.

    A negation cue within ``lookback`` characters *before* the match, or a "non-"
    prefix glued to it. Callers pass a single clause as ``segment`` so negation
    never leaks across a clause boundary ("not constrained; haploinsufficient").
    A negation also covers the later items of an or/nor list ("does not indicate X or
    Y"). A negation that *follows* the term ("haploinsufficiency is not an issue") does
    not count — it negates a predicate about the term, not the term.
    """
    preceding = segment[max(0, start - lookback) : start]
    if _NON_PREFIX.search(preceding):
        return True
    if NEGATION_PATTERN.search(preceding):
        return True
    return bool(_COORDINATED_NEGATION.search(segment[:start]))


# A predicate that denies or minimises its subject: "dependency across lines IS
# NEGLIGIBLE". Used where a following denial reverses a quantity claim; deliberately
# NOT used by `is_negated`, which only reads negation *before* a term.
_DENYING_PREDICATE = re.compile(
    r"^[^.;]{0,30}?\b(?:is|are|was|were|remains?|appears?|seems?)\s+(?:\w+\s+)?"
    r"(?:negligible|minimal|absent|rare|weak|uninformative|not|none|nil|insignificant)\b",
    re.IGNORECASE,
)


def predicate_denied(segment: str, end: int) -> bool:
    """True if the clause text right after ``end`` denies/minimises the matched subject."""
    return bool(_DENYING_PREDICATE.search(segment[end:]))


def unnegated_matches(pattern: re.Pattern[str], text: str) -> list[tuple[str, re.Match[str]]]:
    """Every (clause, match) of ``pattern`` in ``text`` that is not negated in its clause."""
    hits: list[tuple[str, re.Match[str]]] = []
    for clause in iter_clauses(text):
        for m in pattern.finditer(clause):
            if not is_negated(clause, m.start()):
                hits.append((clause, m))
    return hits


def has_unnegated_match(pattern: re.Pattern[str], text: str) -> bool:
    """True if ``pattern`` matches ``text`` somewhere it is not negated (clause-local)."""
    return bool(unnegated_matches(pattern, text))
