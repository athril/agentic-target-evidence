# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Druggability and the modality of the target's drugs, from the records instead of memory.

The druggability axis was true in almost every run whatever the evidence, and the lens
wrote the modality of a target's drugs from memory (an antibody called a small molecule),
which the modality guard then had to catch. Both are read from Open Targets here: the
modality of each known drug (ChEMBL drug type) is given to the lens as a fixed block, and
the axis is set by rule:

* true — a drug acting on the target is approved or in clinical trials (any indication),
  or Open Targets marks a small-molecule, antibody or other modality as tractable;
* false — Open Targets returned the target's tractability, it marks no modality, and no
  drug acting on the target is known;
* null — nothing about tractability was retrieved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from agents.interpretation._lens_confidence import reaggregate
from schemas.verdicts import AxisVerdict, LensVerdict, ValidationFlag

AXIS = "druggability"
_CONFIDENCE = {"clinical": 0.85, "tractable": 0.6, "none": 0.5}
_MAX_NAMES = 4


def _drugs(known_drugs: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """One entry per drug name: its type, and whether it is approved or clinical."""
    out: dict[str, dict[str, Any]] = {}
    for row in known_drugs:
        name = str(row.get("drug_name") or "").strip()
        if not name:
            continue
        entry = out.setdefault(
            name.lower(),
            {"name": name, "type": str(row.get("drug_type") or "").strip(), "clinical": False},
        )
        entry["clinical"] = entry["clinical"] or bool(
            row.get("is_approved") or float(row.get("max_phase") or 0.0) >= 1
        )
    return out


def render_modality_block(known_drugs: Iterable[Mapping[str, Any]]) -> str:
    """Prompt block: the modality of each drug acting on the target (ChEMBL drug type)."""
    drugs = _drugs(known_drugs)
    if not drugs:
        return ""
    by_type: dict[str, list[str]] = {}
    for d in drugs.values():
        by_type.setdefault(d["type"].lower() or "type not recorded", []).append(d["name"].lower())
    lines = [
        "Modalities of the drugs acting on this target (ChEMBL drug type, pre-computed):",
        *(
            f"  {kind}: {', '.join(sorted(names)[:_MAX_NAMES])}"
            + (f" and {len(names) - _MAX_NAMES} more" if len(names) > _MAX_NAMES else "")
            for kind, names in sorted(by_type.items())
        ),
        "  State a drug's modality only as listed here.",
    ]
    return "\n".join(lines)


def druggability_axis(
    known_drugs: Iterable[Mapping[str, Any]], tractability: Mapping[str, Any] | None
) -> AxisVerdict | None:
    """The druggability axis by rule (see module doc); None when nothing was retrieved."""
    drugs = _drugs(known_drugs)
    clinical = sorted(d["name"].lower() for d in drugs.values() if d["clinical"])
    tract = tractability or {}
    modalities = [
        *(["small molecule"] if tract.get("small_molecule") else []),
        *(["antibody"] if tract.get("antibody") else []),
        *[str(m) for m in tract.get("other") or []],
    ]
    if clinical:
        names = ", ".join(clinical[:_MAX_NAMES])
        return AxisVerdict(
            axis=AXIS,
            verdict=True,
            confidence=_CONFIDENCE["clinical"],
            rationale=f"Drugs acting on the target have reached the clinic ({names}).",
        )
    if modalities:
        return AxisVerdict(
            axis=AXIS,
            verdict=True,
            confidence=_CONFIDENCE["tractable"],
            rationale=f"Open Targets marks the target tractable by {', '.join(modalities)}.",
        )
    if tract.get("available") and not drugs:
        return AxisVerdict(
            axis=AXIS,
            verdict=False,
            confidence=_CONFIDENCE["none"],
            rationale=(
                "Open Targets marks no tractable modality for the target and no drug acting "
                "on it is known."
            ),
        )
    return None


def apply_druggability(verdict: LensVerdict, axis: AxisVerdict | None) -> LensVerdict:
    """Set the druggability axis by rule, keeping the lens's own rationale when they agree;
    a flag records a change."""
    if axis is None or not verdict.axes:
        return verdict
    current = next((a for a in verdict.axes if a.axis == AXIS), None)
    if current is not None and current.verdict is axis.verdict:
        merged = current.model_copy(
            update={
                "confidence": max(current.confidence, axis.confidence),
                "rationale": f"{current.rationale}\n{axis.rationale}".strip(),
            }
        )
        if merged == current:
            return verdict
        axes = [merged if a.axis == AXIS else a for a in verdict.axes]
        return reaggregate(verdict.model_copy(update={"axes": axes}))
    axes = [a for a in verdict.axes if a.axis != AXIS] + [axis]
    flag = ValidationFlag(
        lens=verdict.lens,
        severity="low",
        rule_id="biology.druggability_by_rule",
        claim_excerpt=axis.rationale[:300],
        message=(
            f"Druggability set by rule from Open Targets: "
            f"{None if current is None else current.verdict} → {axis.verdict}."
        ),
    )
    return reaggregate(
        verdict.model_copy(
            update={"axes": axes, "validation_flags": [*verdict.validation_flags, flag]}
        )
    )
