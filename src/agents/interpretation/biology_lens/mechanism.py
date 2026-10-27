# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Rules on the biology lens's mechanism-of-action axis, from records the lens was given.

The axis took animal efficacy at face value: BACE1 "reducing BACE1 activity … improves
Alzheimer's disease phenotypes" was favourable at 95% beside five failed phase 2/3
programmes, and HSD17B13 "reducing its activity improves hepatic steatosis in mouse
models" beside its own knockout table listing hepatic steatosis. A favourable axis
becomes uncertain when

* same-direction programmes failed in the indication at the strongest programme gate
  (services/evidence/programme_outcomes.class_failed): the mechanism did not translate
  in patients, whatever the models showed; or
* the rationale says lowering the target improves a phenotype that the target's mouse
  knockout itself shows: its premise contradicts the knockout data.

Developability is corroborative: it restated druggability ("drugs reached the clinic")
and the two together carried biology to 92–94%.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from agents.interpretation._lens_confidence import reaggregate
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag
from services.evidence.drug_precedent import requested_effect
from services.evidence.guard_core import iter_clauses, strip_annotations
from services.evidence.programme_outcomes import class_failed

AXIS = "mechanism_of_action"
_LOWERING = re.compile(
    r"\b(?:reduc\w*|lower\w*|knock[- ]?(?:out|down)\w*|delet\w*|inhibit\w*|loss|deficien\w*"
    r"|silenc\w*|ablat\w*)\b",
    re.IGNORECASE,
)
_IMPROVES = re.compile(
    r"\b(?:improv\w*|attenuat\w*|ameliorat\w*|protect\w*|alleviat\w*|reduc\w*|lessen\w*"
    r"|prevent\w*|rescu\w*)\b",
    re.IGNORECASE,
)
_MIN_LABEL_CHARS = 9  # a one-word label shorter than this ("ataxia") is too generic


def knockout_contradiction(rationale: str, knockout_phenotypes: Iterable[str]) -> str:
    """A knockout phenotype that a clause of ``rationale`` says lowering the target
    improves; "" when there is none."""
    labels = [
        str(p).strip().lower()
        for p in knockout_phenotypes
        if str(p).strip() and (" " in str(p).strip() or len(str(p).strip()) >= _MIN_LABEL_CHARS)
    ]
    for clause in iter_clauses(strip_annotations(rationale or "")):
        low = clause.lower()
        lowering = list(_LOWERING.finditer(clause))
        if not lowering:
            continue
        starts = {m.start() for m in lowering}
        if not any(m.start() not in starts for m in _IMPROVES.finditer(clause)):
            continue
        for label in labels:
            if label in low:
                return label
    return ""


def _uncertain(axis: AxisVerdict, why: str) -> AxisVerdict:
    return axis.model_copy(
        update={"verdict": None, "confidence": 0.0, "rationale": f"{axis.rationale}\n{why}"}
    )


def apply_mechanism_rules(
    verdict: LensVerdict,
    *,
    direction: str,
    programme_reading: Mapping[str, Any] | None,
    knockout_phenotypes: Iterable[str],
) -> LensVerdict:
    """The mechanism and developability rules (module docstring)."""
    if not verdict.axes:
        return verdict
    flags = list(verdict.validation_flags)
    axes: list[AxisVerdict] = []
    changed = False
    for axis in verdict.axes:
        if axis.axis == "developability" and not axis.corroborative:
            axis = axis.model_copy(update={"corroborative": True})
            changed = True
        elif axis.axis == AXIS and axis.verdict is True:
            if class_failed(programme_reading):
                axis = _uncertain(
                    axis,
                    "Same-direction drugs failed in several programmes in this indication: "
                    "the mechanism did not translate in patients.",
                )
                flags.append(_flag("biology.mechanism_untranslated", axis.rationale))
                changed = True
            elif requested_effect(direction) == "decrease" and (
                label := knockout_contradiction(axis.rationale, knockout_phenotypes)
            ):
                axis = _uncertain(
                    axis,
                    f"The target's mouse knockout shows {label}, which this rationale says "
                    "lowering the target improves.",
                )
                flags.append(_flag("biology.mechanism_contradicts_knockout", label))
                changed = True
        axes.append(axis)
    if not changed:
        return verdict
    return reaggregate(
        verdict.model_copy(update={"axes": axes, "validation_flags": flags}), align=False
    )


def _flag(rule_id: str, excerpt: str) -> ValidationFlag:
    return ValidationFlag(
        lens="biology",
        severity="medium",
        rule_id=rule_id,
        claim_excerpt=excerpt[:300],
        message="Mechanism of action set uncertain by rule: "
        + rule_id.split(".", 1)[1].replace("_", " ")
        + ".",
    )
