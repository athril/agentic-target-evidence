# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared helper for building LLM-facing text from Evidence objects.

Clinical trials are special: the gene/target name often appears only in
eligibility_criteria, not in brief_summary. This module builds a
target-first concatenation for CLINICAL_TRIAL evidence so screeners
and claim-extractors can see the gene reference even when it is absent
from the summary field.
"""

from __future__ import annotations

from schemas.evidence import Evidence, EvidenceType


def screenable_text(ev: Evidence) -> str:
    """Return the best available LLM-facing text for an Evidence item.

    For CLINICAL_TRIAL: target-first ordering (interventions → conditions →
    eligibility snippet → brief_summary → design_details) so gene mentions
    buried in eligibility criteria reach the model.

    For all other types: standard fallback chain identical to the previous
    _screenable_text helper in the screening agent.
    """
    if ev.evidence_type == EvidenceType.CLINICAL_TRIAL:
        return _clinical_text(ev)
    return (
        ev.extra.get("abstract")
        or ev.extra.get("brief_summary")
        or ev.extra.get("assoc_text")
        or ev.extra.get("tract_text")
        or ev.extra.get("description")
        or ""
    )


def _clinical_text(ev: Evidence) -> str:
    parts: list[str] = []

    interventions = ev.extra.get("interventions") or []
    if interventions:
        parts.append("Interventions: " + ", ".join(str(i) for i in interventions))

    conditions = ev.extra.get("conditions") or []
    if conditions:
        parts.append("Conditions: " + ", ".join(str(c) for c in conditions))

    # participation_criteria may be a nested dict (as stored by fetch_trials)
    pc = ev.extra.get("participation_criteria") or {}
    eligibility = pc.get("eligibility_criteria") if isinstance(pc, dict) else None
    if eligibility:
        parts.append("Eligibility: " + str(eligibility)[:600])

    brief = ev.extra.get("brief_summary") or ""
    if brief:
        parts.append(brief)

    design = ev.extra.get("design_details") or ""
    if design:
        parts.append(str(design))

    return "\n\n".join(parts)
