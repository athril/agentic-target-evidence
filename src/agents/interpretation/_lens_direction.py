# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Post-LLM reconciliation of a lens with the direction-of-effect reading.

The genetics lens was told, in prose, that causality can hold while the direction is
wrong and that the verdict must then be "oppose"; a 7B model still returned "support"
for activating a target whose loss-of-function carriers are protected. When the
deterministic reading (services/evidence/direction_of_effect) clearly contradicts the
requested direction, a ``direction_of_effect`` axis is set by rule, the overall verdict
becomes "oppose", and a ValidationFlag records the override. A reading that supports
the direction, is conflicting or is too weak changes nothing but the summary line.
"""

from __future__ import annotations

import uuid

from agents.interpretation._lens_confidence import reaggregate
from schemas.messages import AgentMessage
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag
from services.evidence.direction_of_effect import DirectionReading

# Strength of contradicting evidence needed to overrule the lens: more than literature
# alone can reach in practice — a Mendelian mechanism, a label warning or an approved
# drug must be part of it, or literature must be near-unanimous.
OVERRIDE_MIN_AGAINST = 0.5
AXIS = "direction_of_effect"


def apply_direction(verdict: LensVerdict, reading: DirectionReading) -> LensVerdict:
    """Record the reading on the verdict; overrule it when the evidence contradicts it."""
    summary = reading.summary() if reading.observations else ""
    if not (reading.verdict == "contradicts" and reading.against >= OVERRIDE_MIN_AGAINST):
        return verdict.model_copy(update={"direction_of_effect": summary})

    against = sorted(reading.contradicting, key=lambda o: -o.weight)[:3]
    rationale = f"{summary} " + "; ".join(o.detail for o in against)
    axes = [a for a in verdict.axes if a.axis != AXIS]
    axes.append(
        AxisVerdict(
            axis=AXIS,
            verdict=False,
            confidence=reading.against,
            rationale=rationale.strip(),
            supporting_claim_ids=[],
            corroborative=True,
        )
    )
    flags = list(verdict.validation_flags)
    flags.append(
        ValidationFlag(
            lens=verdict.lens,
            severity="medium",
            rule_id="direction_of_effect.contradicted",
            claim_excerpt="",
            message=(
                f"The direction-of-effect evidence contradicts the requested direction "
                f"(against {reading.against:.2f}, for {reading.support:.2f}): axis {AXIS} set by "
                f"rule; verdict {verdict.overall_verdict} → oppose."
            ),
        )
    )
    return reaggregate(
        verdict.model_copy(
            update={
                "axes": axes,
                "overall_verdict": "oppose",
                "rationale": f"{summary} {verdict.rationale}".strip(),
                "validation_flags": flags,
                "direction_of_effect": summary,
            }
        )
    )


def apply_direction_to_result(result: AgentMessage, reading: DirectionReading) -> AgentMessage:
    """`apply_direction` over the verdict carried by a lens AgentMessage."""
    if not isinstance(result.payload, dict):
        return result
    verdicts = result.payload.get("lens_verdicts") or []
    if not verdicts:
        return result
    updated = apply_direction(LensVerdict.model_validate(verdicts[0]), reading)
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=result.run_id,
        from_agent=result.from_agent,
        to_agent=result.to_agent,
        intent=result.intent,
        payload={"lens_verdicts": [updated.model_dump(mode="json")]},
        trace_id=result.trace_id,
    )
