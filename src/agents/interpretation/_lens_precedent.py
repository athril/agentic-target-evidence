# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Post-LLM reconciliation of the clinical and regulatory lenses with the
therapeutic-hypothesis precedent (services/evidence/drug_precedent.py).

The two lenses read trials and labels for the target, which are the same whichever
direction the run asks about, and they counted trials in other indications as
validation in this one. When the precedent is decisive — a same-direction drug approved
in the indication, opposite-direction drugs approved there, or same-direction trials
there stopped for futility or harm — the axes that ask exactly that question are set by
rule, the overall verdict follows, and a ValidationFlag records the override.

A failed or stalled precedent is only decisive when the programmes behind it falsify the
mechanism (services/evidence/programme_outcomes.py): one molecule's unexplained phase 3,
or a stop for business reasons, does not make the clinical-validation axis false, and a
programme that met its endpoint is never called a failure. Without a programme reading
(older callers) any failed or stalled status stays decisive.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from agents.interpretation._lens_confidence import reaggregate
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag

# (lens, status) → {axis: verdict}. Only decisive statuses set axes.
_RULES: dict[tuple[str, str], dict[str, bool]] = {
    ("clinical", "validated"): {"clinical_precedent": True, "clinical_validation": True},
    ("clinical", "opposed"): {"clinical_precedent": False, "clinical_validation": False},
    ("clinical", "failed"): {"clinical_validation": False},
    ("clinical", "stalled"): {"clinical_validation": False},
    ("regulatory", "validated"): {"approval_precedent": True},
    ("regulatory", "opposed"): {"approval_precedent": False, "regulatory_de_risking": False},
    # No drug acting on the target in the requested direction is approved here: no
    # approval precedent for this hypothesis, whatever other mechanisms are approved in
    # the indication (the lens cited an acetylcholinesterase inhibitor's approval as
    # precedent for a secretase inhibitor). Class experience elsewhere belongs on the
    # de-risking axis.
    **{
        ("regulatory", status): {"approval_precedent": False}
        for status in ("failed", "stalled", "late_stage", "in_clinic", "untested")
    },
}
# The overall verdict a decisive status implies for the lens.
_OVERALL: dict[tuple[str, str], Literal["support", "oppose"]] = {
    ("clinical", "validated"): "support",
    ("clinical", "opposed"): "oppose",
    ("clinical", "failed"): "oppose",
    # Phase 3 reached in this indication, nothing approved and nothing still running:
    # late-stage attrition is a negative clinical signal (the lens read it as neutral).
    ("clinical", "stalled"): "oppose",
    ("regulatory", "validated"): "support",
    ("regulatory", "opposed"): "oppose",
}
_RULE_CONFIDENCE = {"validated": 0.85, "opposed": 0.85, "failed": 0.75, "stalled": 0.6}


def _programmes_decide(status: str, programme: dict[str, Any] | None) -> bool:
    """Whether a failed/stalled precedent falsifies the mechanism strongly enough to act on.

    True when a score gate applies to the programme strength; always true for the other
    statuses, and for failed/stalled when there is no usable programme reading.
    """
    if status not in ("failed", "stalled") or not programme or not programme.get("available"):
        return True
    return bool(programme.get("gate"))


def apply_precedent(
    verdict: LensVerdict,
    precedent: dict[str, Any] | None,
    programme: dict[str, Any] | None = None,
) -> LensVerdict:
    """Set the precedent axes of a clinical/regulatory verdict by rule (see module doc)."""
    if not precedent:
        return verdict
    status = str(precedent.get("status") or "")
    rules = _RULES.get((verdict.lens, status))
    if not rules or (verdict.lens == "clinical" and not _programmes_decide(status, programme)):
        return verdict
    summary = str(precedent.get("summary") or "")
    conf = _RULE_CONFIDENCE.get(status, 0.6)
    note = f"Precedent rule ({status}): {summary}"

    axes: list[AxisVerdict] = []
    changed: list[str] = []
    seen: set[str] = set()
    for ax in verdict.axes:
        want = rules.get(ax.axis)
        seen.add(ax.axis)
        if want is None:
            axes.append(ax)
            continue
        if ax.verdict is not want:
            changed.append(ax.axis)
        axes.append(
            ax.model_copy(
                update={
                    "verdict": want,
                    "confidence": max(ax.confidence if ax.verdict is want else 0.0, conf),
                    "rationale": note
                    if ax.verdict is not want
                    else f"{ax.rationale}\n{note}".strip(),
                }
            )
        )
    for axis, want in rules.items():
        if axis not in seen:
            changed.append(axis)
            axes.append(AxisVerdict(axis=axis, verdict=want, confidence=conf, rationale=note))

    overall = _OVERALL.get((verdict.lens, status), verdict.overall_verdict)
    if not changed and overall == verdict.overall_verdict:
        return verdict
    flags = list(verdict.validation_flags)
    flags.append(
        ValidationFlag(
            lens=verdict.lens,
            severity="medium",
            rule_id=f"precedent.{status}",
            claim_excerpt=summary[:300],
            message=(
                f"Therapeutic-hypothesis precedent is '{status}': "
                + (f"axes {', '.join(changed)} set by rule" if changed else "axes agreed")
                + (
                    f"; verdict {verdict.overall_verdict} → {overall}"
                    if overall != verdict.overall_verdict
                    else ""
                )
                + "."
            ),
        )
    )
    rationale = verdict.rationale
    if overall != verdict.overall_verdict:
        rationale = f"{summary} {rationale}".strip()
    return reaggregate(
        verdict.model_copy(
            update={
                "axes": axes,
                "overall_verdict": overall,
                "rationale": rationale,
                "validation_flags": flags,
            }
        )
    )


def apply_precedent_to_result(
    result: AgentMessage,
    precedent: dict[str, Any] | None,
    programme: dict[str, Any] | None = None,
) -> AgentMessage:
    """`apply_precedent` over the verdict carried by a lens AgentMessage."""
    if not precedent or not isinstance(result.payload, dict):
        return result
    verdicts = result.payload.get("lens_verdicts") or []
    if not verdicts:
        return result
    verdict = LensVerdict.model_validate(verdicts[0])
    updated = apply_precedent(verdict, precedent, programme)
    if updated is verdict:
        return result
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=result.run_id,
        from_agent=result.from_agent,
        to_agent=result.to_agent,
        intent=result.intent,
        payload={"lens_verdicts": [updated.model_dump(mode="json")]},
        trace_id=result.trace_id,
    )
