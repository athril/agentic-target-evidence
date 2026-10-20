# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lens prose that says which way of changing the target human genetics favours.

Every lens reads one direction-of-effect reading (services/evidence/direction_of_effect.py),
but a lens could still write its own: a safety lens stated "genetic proxies indicate that
lowering the target's activity increases disease risk" when the run's human genetics read
the other way (protein-truncating carriers protected), then set its toxicity axis on that
sentence. Asking the prompt to leave direction alone did not hold, so the sentence is
checked against the reading instead (pure, no I/O):

- `genetic_direction_assertions` finds clauses that frame a genetic finding (variants,
  carriers, genetic proxies, Mendelian randomisation…) and say that lowering or raising
  the target (named by its symbol) raised or lowered the disease's risk;
- `check_genetic_direction` flags each one whose side the reading weighs less than the
  other side, with both strengths in the note;
- `asserts_genetic_direction` lets the confidence checks require that an axis making such
  a statement cites a claim with a typed effect.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from services.evidence.guard_core import Violation, is_negated, iter_clauses

Side = Literal["decrease", "increase"]

_GENETIC_FRAME = re.compile(
    r"\b(?:genetic\w*|genomic|variants?|alleles?|carriers?|polymorphisms?|genotypes?|"
    r"mutations?|proxies|proxy|proxied|mendelian|GWAS|loss[- ]of[- ]function|"
    r"gain[- ]of[- ]function|protein[- ]truncating|heritab\w*)\b",
    re.IGNORECASE,
)
_DECREASE = (
    r"(?:lower(?:ing|ed)?|reduc(?:e|es|ed|ing|tion)|decreas(?:e|es|ed|ing)|inhibit(?:s|ed|ing|ion)?|"
    r"block(?:s|ed|ing|ade)?|less|loss[- ]of[- ]function(?:\s+of)?|deficiency\s+of|knock[- ]?down\s+of)"
)
_INCREASE = (
    r"(?:rais(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|higher|elevat(?:e|es|ed|ing)|"
    r"activat(?:e|es|ed|ing|ion)|more|gain[- ]of[- ]function(?:\s+of)?|overexpression\s+of)"
)
_TARGET_NOUN = r"(?:\s+(?:activity|levels?|function|expression|concentrations?|protein))?"
_HARM = re.compile(
    r"\b(?:increas(?:e|es|ed|ing)|rais(?:e|es|ed|ing)|elevat(?:e|es|ed|ing)|worsen\w*|"
    r"exacerbat\w*|promot\w*|higher|greater)\b(?:\s+[\w-]+){0,4}?\s+"
    r"(?:risks?|odds|incidence|susceptibility|disease|it)\b|\bworsens?\b|\bexacerbates?\b|"
    r"\bis\s+(?:a\s+)?(?:risk\s+factor|associated\s+with\s+(?:an?\s+)?(?:increased|higher|greater)\s+risk)",
    re.IGNORECASE,
)
_BENEFIT = re.compile(
    r"\b(?:reduc(?:e|es|ed|ing)|lower(?:s|ed|ing)?|decreas(?:e|es|ed|ing)|protect\w*|prevent\w*)"
    r"\b(?:\s+[\w-]+){0,4}?\s+(?:risks?|odds|incidence|susceptibility|disease|it|against)\b|"
    r"\bprotective\b|\bis\s+associated\s+with\s+(?:a\s+)?(?:reduced|lower|decreased)\s+risk",
    re.IGNORECASE,
)


def _target_change(symbols: list[str]) -> re.Pattern[str]:
    names = "|".join(re.escape(s) for s in symbols)
    gene = rf"(?<![A-Za-z0-9])(?:{names})(?![A-Za-z0-9])"
    return re.compile(
        rf"\b(?P<dec>{_DECREASE})\s+(?:(?:the|its|of|in)\s+){{0,2}}{gene}{_TARGET_NOUN}|"
        rf"\b(?P<inc>{_INCREASE})\s+(?:(?:the|its|of|in)\s+){{0,2}}{gene}{_TARGET_NOUN}|"
        rf"{gene}(?:\s+(?:activity|levels?|function|expression))?\s+"
        rf"(?:(?P<dec2>inhibition|lowering|deficiency|knockdown|loss)|"
        rf"(?P<inc2>activation|overexpression|gain))\b",
        re.IGNORECASE,
    )


def genetic_direction_assertions(text: str, symbols: Iterable[str]) -> list[tuple[str, Side]]:
    """(clause, side the clause says helps) for each clause stating, in genetic terms,
    what lowering or raising the target did to the disease."""
    names = [s for s in symbols if s]
    if not names or not text:
        return []
    change = _target_change(names)
    out: list[tuple[str, Side]] = []
    for clause in iter_clauses(text):
        if not _GENETIC_FRAME.search(clause):
            continue
        for m in change.finditer(clause):
            perturbation: Side = "decrease" if (m.group("dec") or m.group("dec2")) else "increase"
            rest = clause[m.end() :]
            harm, benefit = _HARM.search(rest), _BENEFIT.search(rest)
            hit = min((h for h in (harm, benefit) if h), key=lambda h: h.start(), default=None)
            if hit is None or is_negated(rest, hit.start()):
                continue
            helps = (hit is benefit) == (perturbation == "decrease")
            out.append((clause, "decrease" if helps else "increase"))
            break
    return out


def asserts_genetic_direction(text: str, symbols: Iterable[str]) -> bool:
    return bool(genetic_direction_assertions(text, symbols))


def _verb(side: Side) -> str:
    return "lowering" if side == "decrease" else "raising"


def check_genetic_direction(
    text: str,
    *,
    gene: str,
    favours_decrease: float,
    favours_increase: float,
) -> list[Violation]:
    """Clauses asserting a genetic direction the run's human-genetics reading weighs less
    than the opposite one. ``favours_*`` are the reading's strengths for each side."""
    strength = {"decrease": favours_decrease, "increase": favours_increase}
    out: list[Violation] = []
    for clause, side in genetic_direction_assertions(text, [gene]):
        other: Side = "increase" if side == "decrease" else "decrease"
        if strength[other] <= strength[side]:
            continue
        out.append(
            Violation(
                rule_id="direction.genetic_reading",
                kind="contradiction",
                excerpt=clause[:300],
                note=(
                    f"[⚠ DIRECTION GUARD: this run's human-genetics evidence favours "
                    f"{_verb(other)} {gene} (for {_verb(other)} {strength[other]:.2f}, for "
                    f"{_verb(side)} {strength[side]:.2f}); the sentence says the opposite "
                    f"— cite the finding it rests on or drop it.]"
                ),
            )
        )
    return out
