# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The causality axis by rule: a curated record, or human genetics replicated in sources.

The genetics lens set causality in one long call. Its value disagreed with its own
rationale (null beside "strong evidence of causality"), and it counted variants in a
partner or substrate gene that the sentences named next to the target. Here the axis
follows the evidence hierarchy the skill states, with replication as the bar:

* true — a curated in-scope record establishes the gene for this disease (the Mendelian
  floor: ClinGen/GenCC, gold-star pathogenic variants, curated graph edges), or, for a
  cancer, a curated somatic driver record (Cancer Gene Census, IntOGen, cancer
  biomarkers); its direction is not used, as most driver annotations do not say whether
  lowering activity helps;
* true — human genetic association with this disease (or a subtype) comes from at least
  two independent sources: papers whose typed allele effects put the variant in the
  target and move the carriers' risk (genetics_lens/allele_effects.py), Open Targets
  datasources annotating its direction, and GWAS Catalog studies;
* null — otherwise. One sentence, or one paper, never sets causality, and absence is
  never false.

Computed only when the literature was read through the allele-effect question (or no
claim describes a genetic change); otherwise the lens's own axis stands.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel

from agents.interpretation._lens_confidence import reaggregate
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag
from services.evidence.direction_of_effect import DirectionObservation

AXIS = "causality"
MIN_SOURCES = 2
_CURATED_CONFIDENCE = 0.85
_REPLICATED_BASE = 0.6  # two sources
_REPLICATED_STEP = 0.05  # each further source
_REPLICATED_MAX = 0.8


class CausalityReading(BaseModel):
    available: bool = False  # the literature was read through typed allele effects
    curated: bool = False
    # Curated somatic driver records for this cancer (oncology indications only).
    somatic: list[str] = []
    sources: list[str] = []  # independent in-scope sources of human genetic association


def causality_reading(
    observations: Iterable[DirectionObservation],
    *,
    gwas_studies: Iterable[str],
    paper_of: Mapping[str, str],
    curated: bool,
    available: bool,
    somatic: Iterable[str] = (),
) -> CausalityReading:
    """The independent in-scope sources behind the human-genetics observations: one per
    paper for literature, one per Open Targets datasource, one per GWAS Catalog study."""
    sources: set[str] = set()
    for o in observations:
        if not o.in_scope:
            continue
        if o.evidence == "genetics_literature":
            sources.add(f"paper:{paper_of.get(o.source, o.source)}")
        elif o.evidence in ("genetics_structured", "mendelian"):
            sources.add(o.source)
    sources.update(f"gwas:{s}" for s in gwas_studies)
    return CausalityReading(
        available=available,
        curated=curated,
        somatic=sorted(set(somatic)),
        sources=sorted(sources),
    )


def causality_axis(raw: Mapping[str, Any] | None) -> AxisVerdict | None:
    """The computed axis; None when the reading is not available."""
    if not raw:
        return None
    reading = CausalityReading.model_validate(dict(raw))
    if not reading.available and not (reading.curated or reading.somatic):
        return None
    n = len(reading.sources)
    if reading.somatic and not reading.curated:
        return AxisVerdict(
            axis=AXIS,
            verdict=True,
            confidence=_CURATED_CONFIDENCE,
            rationale=(
                "Curated somatic driver evidence for this cancer "
                f"({', '.join(reading.somatic)}, Open Targets)."
            ),
        )
    if reading.curated:
        return AxisVerdict(
            axis=AXIS,
            verdict=True,
            confidence=_CURATED_CONFIDENCE,
            rationale="A curated record establishes the gene's role in this disease.",
        )
    if n >= MIN_SOURCES:
        conf = min(_REPLICATED_MAX, _REPLICATED_BASE + _REPLICATED_STEP * (n - MIN_SOURCES))
        return AxisVerdict(
            axis=AXIS,
            verdict=True,
            confidence=round(conf, 3),
            rationale=(
                f"Human genetic association with this disease replicated in {n} independent "
                "sources (typed allele effects in the target, Open Targets annotations, GWAS "
                "Catalog studies)."
            ),
        )
    return AxisVerdict(
        axis=AXIS,
        verdict=None,
        confidence=0.0,
        rationale=(
            "Human genetic association with this disease is not replicated: "
            f"{n} independent source(s), {MIN_SOURCES} needed."
        ),
    )


def render_causality_block(raw: Mapping[str, Any] | None) -> str:
    axis = causality_axis(raw)
    if axis is None:
        return ""
    word = {True: "true", False: "false", None: "null"}[axis.verdict]
    return (
        f"Causality axis, set by rule from the human genetics (fixed): {word} — "
        f"{axis.rationale} Explain this value; do not argue for another."
    )


def apply_causality(verdict: LensVerdict, raw: Mapping[str, Any] | None) -> LensVerdict:
    """Set the causality axis by rule; a flag records a change from the lens's value."""
    axis = causality_axis(raw)
    if axis is None or not verdict.axes:
        return verdict
    current = next((a for a in verdict.axes if a.axis == AXIS), None)
    if current is not None and current.verdict is axis.verdict:
        if axis.verdict is None or current.confidence >= axis.confidence:
            return verdict
        lifted = current.model_copy(update={"confidence": axis.confidence})
        axes = [lifted if a.axis == AXIS else a for a in verdict.axes]
        return reaggregate(verdict.model_copy(update={"axes": axes}))
    keep = (
        current.model_copy(
            update={
                "verdict": axis.verdict,
                "confidence": axis.confidence,
                "rationale": axis.rationale,
            }
        )
        if current is not None
        else axis
    )
    axes = [a for a in verdict.axes if a.axis != AXIS] + [keep]
    flag = ValidationFlag(
        lens="genetics",
        severity="medium",
        rule_id="genetics.causality_by_rule",
        claim_excerpt=axis.rationale[:300],
        message=(
            f"Causality set by rule (curated record or replicated human genetics): "
            f"{None if current is None else current.verdict} → {axis.verdict}."
        ),
    )
    return reaggregate(
        verdict.model_copy(
            update={"axes": axes, "validation_flags": [*verdict.validation_flags, flag]}
        )
    )
