# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Candidate on-target liabilities, built deterministically from a gene's own data.

The safety lens was free to leave a liability out (LRRK2: lung and kidney knockout
phenotypes omitted, verdict "support 93%"), and the report had no place that listed
liabilities at all. This module derives the list from structured evidence — curated
Open Targets safety events, mouse knockout organ systems, gnomAD constraint against the
therapeutic direction, pan-essentiality, and boxed warnings of drugs acting on the
target — so the safety prompt can require each one to be named, the report can print
them, and the suitability score can account for them. Pure module: no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel

from services.evidence.drug_precedent import requested_effect
from services.evidence.organ_systems import group_by_system

Severity = Literal["high", "medium", "low"]
# The liability that evidence of harm from the requested change itself becomes; the split
# safety lens recognises it by this name.
DIRECTION_HARM_SYSTEM = "the requested change worsens the disease"
_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


class Liability(BaseModel):
    system: str  # organ system or mechanism ("lung", "dosage sensitivity")
    severity: Severity
    source: str  # where the evidence comes from
    evidence: list[str]  # short labels, quoted from the source


def candidate_liabilities(
    *,
    safety_events: Iterable[str] = (),
    mouse_phenotypes: Iterable[str] = (),
    constraint: dict[str, Any] | None = None,
    direction: str = "",
    common_essential: bool = False,
    boxed_warnings: Iterable[tuple[str, str]] = (),
    trial_stop_reasons: Iterable[str] = (),
    direction_harms: Iterable[tuple[str, str]] = (),
) -> list[Liability]:
    """Every candidate on-target liability the structured data support, most severe first.

    ``boxed_warnings`` are (drug, warning text) pairs for drugs whose label names the
    target in its mechanism of action; a competitor's warning is not passed in.
    ``trial_stop_reasons`` are the stop reasons of trials, in this indication, of drugs
    acting in the requested direction; those naming harm become a liability.
    ``direction_harms`` are (source, detail) pairs of evidence that the requested change
    in the target's activity itself causes or worsens the disease
    (services/evidence/direction_of_effect).
    """
    out: list[Liability] = []

    events = [str(e).strip() for e in safety_events if str(e).strip()]
    grouped_events = group_by_system(events)
    for system, labels in grouped_events.items():
        out.append(
            Liability(
                system=system, severity="high", source="Open Targets safety", evidence=labels[:4]
            )
        )
    unplaced = [e for e in events if not any(e in v for v in grouped_events.values())]
    if unplaced:
        out.append(
            Liability(
                system="curated adverse effects",
                severity="high",
                source="Open Targets safety",
                evidence=unplaced[:4],
            )
        )

    for system, labels in group_by_system(mouse_phenotypes).items():
        # Lethality is severe unless every lethality label says only some knockouts die,
        # or only that they die under an experimental challenge ("increased susceptibility
        # to <agent> induced morbidity/mortality"): a challenge phenotype is not the
        # knockout's own lethality, and it kept an approved class at neutral.
        severe = system.startswith("lethality") and not all(
            "incomplete penetrance" in label.lower() or _CHALLENGE_PHENOTYPE.search(label)
            for label in labels
        )
        out.append(
            Liability(
                system=system,
                severity="high" if severe else "medium",
                source="mouse knockout (MGI/IMPC)",
                evidence=labels[:4],
            )
        )

    c = constraint or {}
    # Inhibition and degradation both lower the target's dosage.
    lowers = requested_effect(direction) == "decrease"
    if c.get("claims_haploinsufficiency_ok") and lowers:
        out.append(
            Liability(
                system="dosage sensitivity",
                severity="high",
                source="gnomAD constraint",
                evidence=[f"LOEUF {c.get('loeuf')} — haploinsufficient; inhibition may mimic it"],
            )
        )
    elif c.get("is_lof_constrained") and lowers:
        out.append(
            Liability(
                system="dosage sensitivity",
                severity="medium",
                source="gnomAD constraint",
                evidence=[
                    f"loss-of-function constrained (LOEUF {c.get('loeuf')}, pLI {c.get('pli')})"
                ],
            )
        )

    # Essentiality is a liability for lowering the target, not for raising it.
    if common_essential and requested_effect(direction) != "increase":
        out.append(
            Liability(
                system="cell fitness (common essential)",
                severity="high",
                source="DepMap",
                evidence=["required for proliferation across nearly all cancer cell lines"],
            )
        )

    for drug, text in boxed_warnings:
        if text.strip():
            out.append(
                Liability(
                    system="labelled boxed warning",
                    severity="high",
                    source=f"FDA label ({drug})",
                    evidence=[" ".join(text.split())[:160]],
                )
            )

    safety_stops = [r for r in trial_stop_reasons if _SAFETY_STOP.search(r)]
    if safety_stops:
        out.append(
            Liability(
                system="trials stopped for safety",
                severity="high",
                source="ClinicalTrials.gov (this indication, same direction)",
                evidence=[" ".join(r.split())[:160] for r in safety_stops[:4]],
            )
        )

    harms = list(direction_harms)
    if harms:
        out.append(
            Liability(
                system=DIRECTION_HARM_SYSTEM,
                severity="high",
                source="; ".join(sorted({src for src, _ in harms}))[:120],
                evidence=[detail[:160] for _, detail in harms[:4]],
            )
        )

    return sorted(out, key=lambda li: _RANK[li.severity])


# A mouse phenotype observed only under an experimental challenge.
_CHALLENGE_PHENOTYPE = re.compile(
    r"\binduced\s+(?:morbidity|mortality)|\bsusceptibility\s+to\b", re.IGNORECASE
)
# Stop reasons that name harm to participants, not futility or business decisions.
_SAFETY_STOP = re.compile(
    r"adverse|safety|toxic|hepat|liver|death|died|fatal|tolerab|side[- ]effect|harm|"
    r"worse than placebo|worsen",
    re.IGNORECASE,
)


def render_liabilities_block(items: list[Liability]) -> str:
    """The deterministic block the safety lens must cover item by item (Rule 18)."""
    if not items:
        return (
            "Candidate on-target liabilities (pre-computed from this gene's data): none found "
            "in curated safety events, mouse knockout phenotypes, constraint, essentiality or "
            "on-target drug labels. This is absence of evidence, not evidence of safety."
        )
    lines = [
        "Candidate on-target liabilities (pre-computed from this gene's data — name every "
        "item in the narrative, then weigh it):"
    ]
    for li in items:
        lines.append(f"  • {li.system} [{li.severity}; {li.source}]: {'; '.join(li.evidence)}")
    return "\n".join(lines)
