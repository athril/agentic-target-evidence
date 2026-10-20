# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A claim's typed effect, and the therapeutic direction derived from it in code.

The extractor used to tag each claim with a therapeutic direction (inhibit / activate):
a sign product it had to work out in its head, which way the target changed times which
way the disease moved. It got the product wrong both ways ("carriers of the AA genotype
had lower risk" tagged activate, with no change in the target stated), and a wrong tag
could not be told from a right one. The extractor now reports the two factors, and the
words that state the first:

    perturbation — the target's activity or amount went down (decrease) or up (increase)
    effect_basis — the claim's own words that say so
    outcome      — what that did: benefit or harm to the disease, an adverse effect, none
    off_target   — the claim ascribes the outcome to something other than the target

and this module checks them against the claim (pure, no I/O):

1. The basis must be quoted from the claim, and must not be about another gene only;
   otherwise there is no perturbation.
2. A basis naming a drug known to act on the target takes the drug's action (an
   inhibitor decreases the target), whatever the model said.
3. Otherwise the basis must carry a perturbation cue: a variant or perturbation class
   ("loss-of-function", "knockout", "inhibitor", "gain-of-function", "agonist") or a level
   of the target itself ("lower <target> concentrations"). A level of anything else
   ("higher HDL-C") is a downstream marker, not the target. Cues of one polarity set the
   perturbation; cues of both keep the model's choice only if it is one of them; no cue
   means no perturbation.

The direction then follows: a decrease that helps, or an increase that harms, favours
lowering the target (inhibit); the reverse favours raising it (activate). An adverse
effect, an off-target outcome or a missing factor gives no direction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel

from core.evidence_text import normalize_typography
from schemas.evidence import ClaimOutcome, ClaimPerturbation, Direction
from services.evidence.direction_of_effect import about_other_genes, names_gene
from services.evidence.drug_precedent import drug_effect, drug_key

# A class of change that says which way the target went without naming a level.
_DECREASE_CLASS = re.compile(
    r"loss[- ]of[- ]function|\blof\b|truncat\w*|\bptvs?\b|nonsense|frameshift|"
    r"stop[- ]gain\w*|\bnull\b|knock[- ]?(?:out|down)s?|\bko\b|\bkd\b|"
    r"\bdelet(?:ed|ion|ions)\b|deficien\w*|ablat\w*|silenc\w*|haploinsufficien\w*|"
    r"inactivat\w*|\binhibitors?\b|\binhibition\b|\bantagonists?\b|\bblockers?\b|"
    r"\bblockade\b|\bdegraders?\b|\bdepletion\b|down-?regulat\w*",
    re.IGNORECASE,
)
_INCREASE_CLASS = re.compile(
    r"gain[- ]of[- ]function|\bgof\b|\bactivating\b|over-?express\w*|\bagonists?\b|"
    r"\bactivators?\b|\bpotentiators?\b|up-?regulat\w*|\bamplif\w*|\bduplicat\w*",
    re.IGNORECASE,
)
# A level of something: counts only when the basis names the target itself.
_LEVEL = (
    r"(?:levels?|activity|activities|expression|concentrations?|function|abundance|amounts?|mass)"
)
_DECREASE_LEVEL = re.compile(
    rf"\b(?:lower|low|lowered|lowering|reduced|reduces|reducing|decreased|decreases|"
    rf"diminished|less)\b(?:\s+[\w-]+){{0,4}}?\s+{_LEVEL}\b|"
    rf"\b{_LEVEL}\b(?:\s+[\w-]+){{0,3}}?\s+(?:was|were|is|are)?\s*(?:lower|reduced|decreased)\b|"
    rf"\b(?:lowers|reduces|decreases)\b",
    re.IGNORECASE,
)
_INCREASE_LEVEL = re.compile(
    rf"\b(?:higher|high|increased|increases|increasing|elevated|raised|greater|more)\b"
    rf"(?:\s+[\w-]+){{0,4}}?\s+{_LEVEL}\b|"
    rf"\b{_LEVEL}\b(?:\s+[\w-]+){{0,3}}?\s+(?:was|were|is|are)?\s*(?:higher|increased|elevated)\b|"
    rf"\b(?:raises|increases|elevates)\b",
    re.IGNORECASE,
)

_PERTURBATIONS: frozenset[str] = frozenset({"decrease", "increase"})
_OUTCOMES: frozenset[str] = frozenset({"benefit", "harm", "adverse"})


class ClaimEffect(BaseModel):
    perturbation: ClaimPerturbation = "none"
    effect_basis: str = ""
    outcome: ClaimOutcome = "none"
    off_target: bool = False
    direction: Direction = Direction.UNSPECIFIED


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalize_typography(text).lower()).split())


def quoted(basis: str, text: str) -> bool:
    """``basis`` appears in ``text``, ignoring case, punctuation and spacing."""
    b = _norm(basis)
    return bool(b) and f" {b} " in f" {_norm(text)} "


def _drug_effects(basis: str, target_drugs: Mapping[str, str]) -> set[str]:
    """Effects of the target's drugs the basis names (keys are upper-case drug names)."""
    upper = normalize_typography(basis).upper()
    return {
        effect
        for name, effect in target_drugs.items()
        if effect in _PERTURBATIONS
        and re.search(rf"(?<![A-Z0-9]){re.escape(name)}(?![A-Z0-9])", upper)
    }


def basis_perturbation(
    basis: str,
    *,
    target_symbols: Iterable[str] = (),
    target_drugs: Mapping[str, str] | None = None,
) -> set[str]:
    """The polarities the basis words state for the target: {"decrease"}, {"increase"},
    both, or none. A drug of the target decides alone; a level counts only for the
    target itself."""
    symbols = [s for s in target_symbols if s]
    drugs = _drug_effects(basis, target_drugs or {})
    if drugs:
        return drugs
    found: set[str] = set()
    if _DECREASE_CLASS.search(basis):
        found.add("decrease")
    if _INCREASE_CLASS.search(basis):
        found.add("increase")
    if symbols and names_gene(basis, symbols):
        if _DECREASE_LEVEL.search(basis):
            found.add("decrease")
        if _INCREASE_LEVEL.search(basis):
            found.add("increase")
    return found


def therapeutic_direction(
    perturbation: str, outcome: str, *, off_target: bool = False
) -> Direction:
    """The direction a typed effect favours: lowering the target (inhibit) when a decrease
    helps or an increase harms, raising it (activate) for the reverse."""
    if off_target or perturbation not in _PERTURBATIONS or outcome not in ("benefit", "harm"):
        return Direction.UNSPECIFIED
    lowers = (perturbation == "decrease") == (outcome == "benefit")
    return Direction.INHIBIT if lowers else Direction.ACTIVATE


def settle_effect(
    raw: Mapping[str, Any],
    claim_text: str,
    *,
    target_symbols: Iterable[str] = (),
    target_drugs: Mapping[str, str] | None = None,
) -> ClaimEffect:
    """The claim's effect after the rails (module docstring), with its direction."""
    symbols = [s for s in target_symbols if s]
    stated = str(raw.get("perturbation") or "none").strip().lower()
    basis = " ".join(str(raw.get("effect_basis") or "").split())
    outcome = str(raw.get("outcome") or "none").strip().lower()
    outcome = outcome if outcome in _OUTCOMES else "none"
    off_target = raw.get("off_target") is True

    perturbation = "none"
    if basis and quoted(basis, claim_text) and not (symbols and about_other_genes(basis, symbols)):
        polarities = basis_perturbation(basis, target_symbols=symbols, target_drugs=target_drugs)
        if len(polarities) == 1:
            perturbation = next(iter(polarities))
        elif stated in polarities:
            perturbation = stated
    return ClaimEffect(
        perturbation=perturbation,
        effect_basis=basis if perturbation != "none" else "",
        outcome=outcome,
        off_target=off_target,
        direction=therapeutic_direction(perturbation, outcome, off_target=off_target),
    )


def target_drug_effects(known_drugs: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """{upper-case drug name: decrease | increase} for the target's known drugs (Open
    Targets ``known_drugs`` rows); drugs with an unknown action are left out."""
    out: dict[str, str] = {}
    for row in known_drugs:
        name = drug_key(str(row.get("drug_name") or ""))
        effect = drug_effect(row.get("action_type"))
        if name and effect in _PERTURBATIONS:
            out.setdefault(name, effect)
    return out


def effect_label(claim: Any) -> str:
    """A short reading of a claim's effect for prompts and reports; "" when it has none."""
    if getattr(claim, "off_target", False):
        return "off-target effect"
    perturbation = getattr(claim, "perturbation", "none")
    outcome = getattr(claim, "outcome", "none")
    if perturbation not in _PERTURBATIONS:
        return ""
    change = "less target activity" if perturbation == "decrease" else "more target activity"
    result = {
        "benefit": "disease better",
        "harm": "disease worse",
        "adverse": "adverse effect",
    }.get(outcome)
    return f"{change} → {result}" if result else change
