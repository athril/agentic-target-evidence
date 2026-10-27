# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The clinical axes and verdict, computed from the programme reading instead of the model.

The clinical lens read trials for the target, which are the same whichever direction or
indication the run asks about. It called a failed phase 3 "neutral" beside a programme
table saying the class failed, and the precedent rule (``_lens_precedent.py``) only
stepped in when a failure reached a score gate.

Both axes are now set in code from the same-direction programmes in the indication
(services/evidence/programme_outcomes.py) and the precedent status
(services/evidence/drug_precedent.py):

``clinical_precedent`` — whether the hypothesis was tested in the clinic here.
    True when a same-direction programme reached phase 2 or later in the indication;
    false when only opposite-direction drugs got there (precedent ``opposed``); null
    otherwise (phase 1 only, or nothing recorded — absence is not a finding). It says
    the question was asked, not how it was answered, so it never counts against an
    oppose verdict (``_lens_confidence.QUALIFIER_AXES``).

``clinical_validation`` — whether the outcomes there show benefit, first rule that applies:
    1. a same-direction drug approved in the indication → true;
    2. opposite-direction drugs approved there (precedent ``opposed``) → false;
    3. the failures falsify the mechanism at a score gate → false;
    4. a phase 3 met its primary endpoint (not filed) → true when no programme failed at
       phase 2 or later, otherwise null (mixed);
    5. every programme that reached phase 2 or later failed and none is still running
       → false;
    6. otherwise null (early, still running, or nothing recorded).

The overall verdict follows validation: true → support, false → oppose, null → neutral
(or insufficient_evidence when the model found nothing and nothing was tested).

When there is no usable programme reading (Open Targets rows without stop reports) or
the direction has no effect class, nothing is computed and the precedent rule applies
as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel

from agents.interpretation._lens_base import align_verdict_statements
from agents.interpretation._lens_confidence import reaggregate
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag
from services.evidence.programme_outcomes import Programme, ProgrammeReading

Rule = Literal[
    "approved", "opposed", "falsified", "all_failed", "positive", "mixed", "pending", "none"
]

# Confidence of each validation rule; the precedent axis uses _TESTED.
_CONFIDENCE: dict[str, float] = {
    "approved": 0.85,
    "opposed": 0.85,
    "falsified": 0.75,
    "all_failed": 0.6,
    "positive": 0.6,
}
_TESTED = 0.85  # a phase 2+ programme in the indication (axis_evidence.yaml floor)


class ClinicalAxes(BaseModel):
    precedent: bool | None
    precedent_reason: str
    validation: bool | None
    validation_reason: str
    rule: Rule


def _names(programmes: list[Programme], n: int = 4) -> str:
    shown = ", ".join(p.drug.lower() for p in programmes[:n])
    return shown + (f" and {len(programmes) - n} more" if len(programmes) > n else "")


def _stage_word(stage: float) -> str:
    return "approval" if stage >= 4 else f"phase {stage:g}"


def _by_stage(programmes: list[Programme]) -> str:
    """Each drug under the stage it reached: one list under the best stage credited every
    drug with it (PLK1: "reached phase 3 (volasertib, bi-2536, onvansertib)", two of which
    reached phase 2)."""
    stages = sorted({p.stage for p in programmes}, reverse=True)
    return "; ".join(
        f"{_stage_word(st)}: {_names([p for p in programmes if p.stage == st])}" for st in stages
    )


def _precedent(status: str, reading: ProgrammeReading) -> tuple[bool | None, str]:
    tested = [p for p in reading.programmes if p.stage >= 2]
    if tested:
        best = max(p.stage for p in tested)
        return True, (
            f"Drugs acting on the target in the requested direction reached "
            f"{_stage_word(best)} in this indication ({_by_stage(tested)})."
        )
    if status == "opposed":
        return False, (
            "Only drugs with the opposite effect on the target reached late-stage trials "
            "or approval in this indication."
        )
    early = [p for p in reading.programmes if p.stage > 0]
    if early:
        return None, (
            f"Same-direction drugs in this indication are in phase 1 only ({_names(early)})."
        )
    return None, (
        "No drug acting on the target in the requested direction is recorded in trials in "
        "this indication (Open Targets known drugs and the registry)."
    )


def _validation(
    status: str, reading: ProgrammeReading, summary: str
) -> tuple[bool | None, str, Rule]:
    progs = reading.programmes
    approved = [p for p in progs if p.outcome == "approved"]
    positive = [p for p in progs if p.outcome == "positive_unfiled"]
    failed = [p for p in reading.failed if p.failure_stage >= 2]
    running = [p for p in progs if p.outcome == "ongoing" and p.stage >= 2]
    if status == "validated" or approved:
        if not approved:
            return True, summary or "Approved here in the requested direction.", "approved"
        return (
            True,
            f"Approved in this indication in the requested direction ({_names(approved)}).",
            "approved",
        )
    if status == "opposed":
        return False, summary or "Drugs with the opposite effect are approved here.", "opposed"
    if reading.gate is not None:
        return (
            False,
            f"The failed programmes falsify the mechanism in this indication (strength "
            f"{reading.falsification:.2f}: {reading.gate.reason}; {_names(reading.failed)}).",
            "falsified",
        )
    if positive and not failed:
        return (
            True,
            f"A phase 3 trial met its primary endpoint in this indication "
            f"({_names(positive)}; not approved) and no programme failed.",
            "positive",
        )
    if positive:
        return (
            None,
            f"Mixed outcomes in this indication: a phase 3 met its primary endpoint "
            f"({_names(positive)}) while others failed ({_names(failed)}).",
            "mixed",
        )
    if failed and not running:
        return (
            False,
            f"Every same-direction programme that reached phase 2 or later in this "
            f"indication failed ({_names(failed)}); none is approved, positive or still "
            "running.",
            "all_failed",
        )
    if failed:
        return (
            None,
            f"Programmes failed in this indication ({_names(failed)}) while others are "
            f"still running ({_names(running)}).",
            "pending",
        )
    return None, "No same-direction programme in this indication has a decisive outcome.", "none"


def compute_clinical_axes(
    precedent: Mapping[str, Any] | None, programme: Mapping[str, Any] | None
) -> ClinicalAxes | None:
    """The clinical axes by rule (see module doc); None when they cannot be computed."""
    if not precedent or not programme or not programme.get("available"):
        return None
    status = str(precedent.get("status") or "")
    if not status or status == "undetermined":
        return None
    reading = ProgrammeReading.model_validate(dict(programme))
    has_precedent, precedent_reason = _precedent(status, reading)
    validation, validation_reason, rule = _validation(
        status, reading, str(precedent.get("summary") or "")
    )
    return ClinicalAxes(
        precedent=has_precedent,
        precedent_reason=precedent_reason,
        validation=validation,
        validation_reason=validation_reason,
        rule=rule,
    )


def _word(value: bool | None) -> str:
    return {True: "true", False: "false", None: "null"}[value]


Verdict = Literal["support", "oppose", "neutral", "insufficient_evidence"]


def verdict_for(axes: ClinicalAxes) -> Verdict:
    """The overall verdict the computed validation implies (before the evidence-gap case)."""
    verdicts: dict[bool | None, Verdict] = {True: "support", False: "oppose", None: "neutral"}
    return verdicts[axes.validation]


def render_fixed_axes(axes: ClinicalAxes) -> str:
    """Prompt block: the axes set by rule, so the narrative explains them instead of
    arguing for other values."""
    return "\n".join(
        [
            "Clinical axes, set by rule from the programme table (fixed: the axes you "
            "return are replaced by these):",
            f"  clinical_precedent: {_word(axes.precedent)} — {axes.precedent_reason}",
            f"  clinical_validation: {_word(axes.validation)} — {axes.validation_reason}",
            f"  overall verdict: {verdict_for(axes)}",
            "  Write the rationale, the narrative and each axis rationale to explain these "
            "values from the programme table, citing its trial ids; do not argue for other "
            "values.",
        ]
    )


def _set_axis(
    ax: AxisVerdict | None, axis: str, value: bool | None, reason: str, conf: float
) -> AxisVerdict:
    if ax is None:
        return AxisVerdict(axis=axis, verdict=value, confidence=conf, rationale=reason)
    agreed = ax.verdict is value
    return ax.model_copy(
        update={
            "verdict": value,
            # An agreeing axis keeps its evidence-computed confidence when that is higher.
            "confidence": max(ax.confidence if agreed else 0.0, conf),
            "rationale": f"{ax.rationale}\n{reason}".strip() if agreed else reason,
        }
    )


def apply_clinical_axes(verdict: LensVerdict, axes: ClinicalAxes) -> LensVerdict:
    """Set both clinical axes and the overall verdict by rule, with a flag on any change."""
    by_name = {a.axis: a for a in verdict.axes}
    precedent = _set_axis(
        by_name.get("clinical_precedent"),
        "clinical_precedent",
        axes.precedent,
        axes.precedent_reason,
        _TESTED if axes.precedent is not None else 0.0,
    )
    validation = _set_axis(
        by_name.get("clinical_validation"),
        "clinical_validation",
        axes.validation,
        axes.validation_reason,
        _CONFIDENCE.get(axes.rule, 0.0) if axes.validation is not None else 0.0,
    )
    computed = {"clinical_precedent": precedent, "clinical_validation": validation}
    changed = [
        name
        for name, ax in computed.items()
        if name not in by_name or by_name[name].verdict is not ax.verdict
    ]
    new_axes = [computed.get(a.axis, a) for a in verdict.axes]
    new_axes += [ax for name, ax in computed.items() if name not in by_name]

    overall = verdict_for(axes)
    if (
        overall == "neutral"
        and verdict.overall_verdict == "insufficient_evidence"
        and axes.precedent is not True
    ):
        overall = "insufficient_evidence"

    updated = verdict.model_copy(update={"axes": new_axes, "overall_verdict": overall})
    if changed or overall != verdict.overall_verdict:
        message = (
            f"Clinical axes computed from the programmes in the indication ({axes.rule}): "
            + (f"axes {', '.join(changed)} set by rule" if changed else "axes agreed")
            + (
                f"; verdict {verdict.overall_verdict} → {overall}"
                if overall != verdict.overall_verdict
                else ""
            )
            + "."
        )
        rationale = verdict.rationale
        if overall != verdict.overall_verdict:
            rationale = f"{axes.validation_reason} {rationale}".strip()
        updated = updated.model_copy(
            update={
                "rationale": rationale,
                "validation_flags": [
                    *verdict.validation_flags,
                    ValidationFlag(
                        lens=verdict.lens,
                        severity="medium",
                        rule_id=f"clinical.computed_axes.{axes.rule}",
                        claim_excerpt=axes.validation_reason[:300],
                        message=message,
                    ),
                ],
            }
        )
    return align_verdict_statements(reaggregate(updated))
