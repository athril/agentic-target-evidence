# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Post-LLM rules for the safety lens from deterministic readings.

The skill already states these rules; a 7B model did not apply them:

* Common essential (services/evidence/readings.dependency_profile). A gene every cell
  line needs is not selectively needed by the indication, and lowering its activity hits
  normal proliferating tissue. Unless a drug lowering it is approved in the indication
  (which settles the window clinically), the safety lens's toxicity axis is a concern.
  It is a therapeutic-window question, so it is weighed here and by the dependency gates,
  not by the biology lens (whose verdict is computed from its own axes).
* Unresolved liabilities without human exposure (safety skill Rule 20). With two or more
  candidate liabilities and no drug acting on the target in people, the toxicity axis
  cannot be favourable above 0.65.

Each override is recorded as a ValidationFlag; prose is left to the lens (and annotated
by its guards).
"""

from __future__ import annotations

from typing import Any, Literal

from agents.interpretation._lens_confidence import reaggregate
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag

UNEXPOSED_TOXICITY_CAP = 0.65
_ESSENTIAL_TOXICITY_CONFIDENCE = 0.6


def _flag(lens: Any, rule_id: str, message: str) -> ValidationFlag:
    return ValidationFlag(
        lens=lens,
        severity="medium",
        rule_id=rule_id,
        claim_excerpt="",
        message=message,
    )


def _set_axis(
    axes: list[AxisVerdict], name: str, verdict: bool | None, confidence: float, rationale: str
) -> list[AxisVerdict]:
    out = [a for a in axes if a.axis != name]
    old = next((a for a in axes if a.axis == name), None)
    if old is not None:
        out.append(
            old.model_copy(
                update={
                    "verdict": verdict,
                    "confidence": confidence,
                    "rationale": f"{rationale}\n{old.rationale}".strip(),
                }
            )
        )
    else:
        out.append(
            AxisVerdict(axis=name, verdict=verdict, confidence=confidence, rationale=rationale)
        )
    return out


def apply_common_essential(
    verdict: LensVerdict,
    profile: dict[str, Any] | None,
    *,
    lowers_activity: bool,
    validated: bool,
    is_oncology: bool,
) -> LensVerdict:
    """The common-essential rule for the safety lens (see module docstring)."""
    if (
        not profile
        or profile.get("kind") != "common_essential"
        or not lowers_activity
        or validated
        or verdict.lens != "safety"
    ):
        return verdict
    note = (
        "Common essential: every cell line needs it, so the dependency is not selective "
        "for the indication and lowering its activity reaches normal proliferating tissue."
    )
    axes = _set_axis(list(verdict.axes), "toxicity", False, _ESSENTIAL_TOXICITY_CONFIDENCE, note)
    overall: Literal["support", "oppose", "neutral", "insufficient_evidence"]
    if verdict.overall_verdict == "oppose":
        overall = "oppose"
    else:
        overall = "neutral" if is_oncology else "oppose"
    if axes == list(verdict.axes) and overall == verdict.overall_verdict:
        return verdict
    flag = _flag(
        verdict.lens,
        "dependency.common_essential",
        f"Common-essential target, activity lowered, no approved drug in the indication: "
        f"verdict {verdict.overall_verdict} → {overall}.",
    )
    return reaggregate(
        verdict.model_copy(
            update={
                "axes": axes,
                "overall_verdict": overall,
                "rationale": f"{note} {verdict.rationale}".strip(),
                "validation_flags": [*verdict.validation_flags, flag],
            }
        )
    )


def cap_unexposed_toxicity(
    verdict: LensVerdict, liabilities: list[dict[str, Any]], *, human_exposure: bool
) -> LensVerdict:
    """Safety Rule 20 in code (see module docstring)."""
    if verdict.lens != "safety" or human_exposure or len(liabilities) < 2:
        return verdict
    axes = list(verdict.axes)
    tox = next((a for a in axes if a.axis == "toxicity"), None)
    if tox is None or tox.verdict is not True or tox.confidence <= UNEXPOSED_TOXICITY_CAP:
        return verdict
    capped = [
        a.model_copy(update={"confidence": UNEXPOSED_TOXICITY_CAP}) if a.axis == "toxicity" else a
        for a in axes
    ]
    flag = _flag(
        "safety",
        "safety.unexposed_liabilities_cap",
        f"{len(liabilities)} candidate liabilities and no human exposure to drugs acting on "
        f"the target: toxicity confidence capped at {UNEXPOSED_TOXICITY_CAP}.",
    )
    return reaggregate(
        verdict.model_copy(
            update={"axes": capped, "validation_flags": [*verdict.validation_flags, flag]}
        )
    )
