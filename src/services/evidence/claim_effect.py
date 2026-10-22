# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A claim's typed effect, and the therapeutic direction derived from it in code.

The extractor used to tag each claim with a therapeutic direction (inhibit / activate):
a sign product it had to work out in its head, which way the target changed times which
way the disease moved. It got the product wrong both ways, and a wrong tag could not be
told from a right one. The direction's two factors are now found separately:

    perturbation — the target's activity or amount went down (decrease) or up (increase)
    outcome      — what that did: benefit or harm to the disease, an adverse effect, none
    off_target   — the claim ascribes the outcome to something other than the target

The perturbation is looked up, never judged from the claim's words (pure, no I/O):

1. Drug data. A claim naming the target's known drugs of one action (Open Targets known
   drugs / ChEMBL mechanism: an inhibitor decreases the target) takes that action.
2. Its sentence's statement. A literature claim names the sentence it rests on; the
   statement read from that sentence's grounded mentions (services/evidence/
   statements.py) gives the target's change, from a variant's consequence or QTL, a
   drug's action or a genetic perturbation of the target, and, for a disease outcome,
   which way the disease moved (``with_statement``).
3. Otherwise no perturbation.

The extractor's two fixed-choice fields (``acts_on_target``, ``target_level``) are gone:
judged on paraphrased claims, they typed a biomarker's level as the target's.

The direction then follows: a decrease that helps, or an increase that harms, favours
lowering the target (inhibit); the reverse favours raising it (activate). An adverse
effect, an off-target outcome or a missing factor gives no direction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel

from core.evidence_text import normalize_typography
from schemas.evidence import ClaimOutcome, ClaimPerturbation, Direction
from schemas.statement import TargetStatement
from services.evidence.drug_precedent import drug_effect, drug_key

# "claim" is kept so claims of runs before the statement route still load.
EffectSource = Literal["drug", "consequence", "curated", "qtl", "text", "claim", "none"]

_PERTURBATIONS: frozenset[str] = frozenset({"decrease", "increase"})
_OUTCOMES: frozenset[str] = frozenset({"benefit", "harm", "adverse"})


class ClaimEffect(BaseModel):
    perturbation: ClaimPerturbation = "none"
    effect_basis: str = ""
    outcome: ClaimOutcome = "none"
    off_target: bool = False
    direction: Direction = Direction.UNSPECIFIED
    # Where the perturbation came from: the target's drug data, the statement of the
    # claim's sentence (its effect source), or nowhere.
    effect_source: EffectSource = "none"


def _drug_effects(text: str, target_drugs: Mapping[str, str]) -> dict[str, str]:
    """{drug name: effect} for the target's drugs the text names (keys are upper-case
    drug names)."""
    upper = normalize_typography(text).upper()
    return {
        name: effect
        for name, effect in target_drugs.items()
        if effect in _PERTURBATIONS
        and re.search(rf"(?<![A-Z0-9]){re.escape(name)}(?![A-Z0-9])", upper)
    }


def _answer(raw: Mapping[str, Any], key: str) -> str:
    return str(raw.get(key) or "none").strip().lower()


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
    target_drugs: Mapping[str, str] | None = None,
) -> ClaimEffect:
    """The claim's effect from drug data, with the extractor's outcome, and its direction
    (module docstring). A literature claim's sentence statement is applied afterwards
    (``with_statement``)."""
    outcome = _answer(raw, "outcome")
    outcome = outcome if outcome in _OUTCOMES else "none"
    off_target = raw.get("off_target") is True

    perturbation, basis, source = "none", "", "none"
    drugs = _drug_effects(claim_text, target_drugs or {})
    polarities = set(drugs.values())
    if len(polarities) == 1:
        perturbation, source = next(iter(polarities)), "drug"
        basis = ", ".join(sorted(drugs))
    return ClaimEffect(
        perturbation=perturbation,
        effect_basis=basis,
        outcome=outcome,
        off_target=off_target,
        direction=therapeutic_direction(perturbation, outcome, off_target=off_target),
        effect_source=source,
    )


def statement_outcome(statement: TargetStatement) -> str:
    """A statement's disease outcome as a claim outcome: less disease is a benefit, more a
    harm; "none" for a biomarker or an unstated direction."""
    if statement.outcome_kind != "disease":
        return "none"
    return {"lower": "benefit", "higher": "harm"}.get(statement.outcome_direction, "none")


def with_statement(claim: Any, statement: TargetStatement | None) -> Any:
    """``claim`` (a CoreClaim) with the effect of its sentence's statement, when the claim
    has no drug-typed effect and the statement types the target's change. The statement's
    disease outcome replaces the extractor's; otherwise the extractor's outcome stays."""
    if (
        statement is None
        or getattr(claim, "effect_source", "none") == "drug"
        or statement.effect_on_target not in _PERTURBATIONS
    ):
        return claim
    outcome = statement_outcome(statement)
    if outcome == "none":
        outcome = getattr(claim, "outcome", "none")
    off_target = bool(getattr(claim, "off_target", False))
    return claim.model_copy(
        update={
            "perturbation": statement.effect_on_target,
            "effect_basis": statement.exposure_text,
            "outcome": outcome,
            "effect_source": statement.effect_source,
            "direction": therapeutic_direction(
                statement.effect_on_target, outcome, off_target=off_target
            ),
        }
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
