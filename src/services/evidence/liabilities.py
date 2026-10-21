# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Candidate on-target liabilities, built deterministically from a gene's own data.

The safety lens was free to leave a liability out (LRRK2: lung and kidney knockout
phenotypes omitted, verdict "support 93%"), and the report had no place that listed
liabilities at all. This module derives the list from structured evidence — curated
Open Targets safety events, mouse knockout organ systems, gnomAD constraint against the
therapeutic direction, pan-essentiality, boxed warnings of drugs acting on the target,
and harms the literature reports when the target was lowered or raised — so the safety
prompt can require each one to be named, the report can print them, and the suitability
score can account for them. Pure module: no I/O.

Each liability carries the change of the target that causes it (``perturbation``):
mouse knockouts, haploinsufficiency and essentiality are harms of lowering it; Open
Targets events take their curated ``effects.direction``; drug warnings and trial stops
are of drugs acting in the requested direction. An activation run once read as safe on
knockout phenotypes and inhibitor exposure. ``split_by_direction`` keeps the items of the
requested change (or of either), and says whether any source covers that change at all:
data on the harms of raising a target are thin, and an activator must not look safer
only because less is recorded.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel

from services.evidence.drug_precedent import requested_effect
from services.evidence.organ_systems import group_by_system

Severity = Literal["high", "medium", "low"]
Perturbation = Literal["decrease", "increase", "either"]
# The liability that evidence of harm from the requested change itself becomes; the split
# safety lens recognises it by this name.
DIRECTION_HARM_SYSTEM = "the requested change worsens the disease"
_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


# Open Targets safety-liability datasources that record how the gene's variants change a
# drug's effect (pharmacogenomics: ClinPGx, formerly PharmGKB), not a toxicity of acting
# on the gene. Read as liabilities, a variant's drug-response association ("cardiovascular
# disease events") became an on-target cardiovascular liability of the target.
PHARMACOGENOMIC_SOURCES = frozenset({"clinpgx", "pharmgkb"})


def is_pharmacogenomic(liability: Mapping[str, Any]) -> bool:
    """An Open Targets safety-liability row from a pharmacogenomic source."""
    return str(liability.get("datasource") or "").strip().lower() in PHARMACOGENOMIC_SOURCES


# Open Targets ``effects.direction`` values, as the change of the target that causes the
# event.
_EFFECT_DIRECTIONS = {
    "activation": "increase",
    "increase": "increase",
    "upregulation": "increase",
    "inhibition": "decrease",
    "decrease": "decrease",
    "downregulation": "decrease",
}


def _event_direction(effects: Iterable[Any]) -> Perturbation:
    """The change of the target an event's curated effects name: one direction, or
    "either" (both, other values or none)."""
    found = {
        _EFFECT_DIRECTIONS.get(str(e.get("direction") or "").strip().lower(), "either")
        for e in effects
        if isinstance(e, Mapping)
    }
    if found == {"decrease"}:
        return "decrease"
    if found == {"increase"}:
        return "increase"
    return "either"


def target_safety(ot: Mapping[str, Any]) -> dict[str, Any]:
    """The Open Targets safety liabilities that are toxicities of acting on the target,
    from an Open Targets record's ``extra``: ``liabilities``, ``events``, ``count``,
    ``text`` (rewritten when rows were left out), the ``pharmacogenomic`` rows left out,
    and ``event_directions`` ({event: decrease | increase | either}, from the events'
    curated effects; an event of several rows takes "either" unless they agree).

    Rows cached before liabilities carried their datasource keep their events as they are.
    """
    rows = [li for li in ot.get("safety_liabilities") or [] if isinstance(li, Mapping)]
    if not rows:
        events = [str(e) for e in ot.get("safety_liability_events") or []]
        return {
            "liabilities": [],
            "events": events,
            "count": int(ot.get("safety_liability_count") or len(events)),
            "text": str(ot.get("safety_text") or ""),
            "pharmacogenomic": [],
            "event_directions": {e: "either" for e in events},
        }
    keep = [li for li in rows if not is_pharmacogenomic(li)]
    pgx = [li for li in rows if is_pharmacogenomic(li)]
    events = list(dict.fromkeys(str(li.get("event") or "") for li in keep if li.get("event")))
    text = str(ot.get("safety_text") or "")
    if pgx:
        named = f" Events include: {', '.join(events[:6])}." if events else ""
        text = (
            f"Open Targets safety: {len(keep)} liability event(s).{named} "
            f"{len(pgx)} pharmacogenomic record(s) (variants changing a drug's response, "
            f"not toxicities of acting on the target) left out."
        )
    directions: dict[str, set[str]] = {}
    for li in keep:
        if li.get("event"):
            directions.setdefault(str(li["event"]), set()).add(
                _event_direction(li.get("effects") or [])
            )
    return {
        "liabilities": keep,
        "events": events,
        "count": len(keep),
        "text": text,
        "pharmacogenomic": pgx,
        "event_directions": {
            e: (d.pop() if len(d) == 1 else "either") for e, d in directions.items()
        },
    }


class Liability(BaseModel):
    system: str  # organ system or mechanism ("lung", "dosage sensitivity")
    severity: Severity
    source: str  # where the evidence comes from
    evidence: list[str]  # short labels, quoted from the source
    # The change of the target that causes it; "either" when the source does not say.
    perturbation: Perturbation = "either"


def literature_harm_system(perturbation: str) -> str:
    """The liability name of harms the literature reports for a change of the target."""
    change = "lowered" if perturbation == "decrease" else "raised"
    return f"harm reported in the literature when the target was {change}"


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
    event_directions: Mapping[str, str] | None = None,
    literature_harms: Iterable[tuple[str, str, str]] = (),
) -> list[Liability]:
    """Every candidate on-target liability the structured data support, most severe first.

    ``boxed_warnings`` are (drug, warning text) pairs for drugs whose label names the
    target in its mechanism of action; a competitor's warning is not passed in.
    ``trial_stop_reasons`` are the stop reasons of trials, in this indication, of drugs
    acting in the requested direction; those naming harm become a liability.
    ``direction_harms`` are (source, detail) pairs of evidence that the requested change
    in the target's activity itself causes or worsens the disease
    (services/evidence/direction_of_effect).
    ``event_directions`` ({event: decrease | increase | either}, ``target_safety``) splits
    the Open Targets events of one organ system by the change that causes them.
    ``literature_harms`` are (perturbation, source, detail) triples of claims reporting
    harm when the target was lowered or raised, in any disease; they become at most one
    medium liability per change.
    """
    out: list[Liability] = []
    requested: Perturbation = (
        "either" if (eff := requested_effect(direction)) not in ("decrease", "increase") else eff
    )
    tags = event_directions or {}

    events = [str(e).strip() for e in safety_events if str(e).strip()]
    by_tag: dict[str, list[str]] = {}
    for e in events:
        by_tag.setdefault(tags.get(e, "either"), []).append(e)
    for tag, tagged in by_tag.items():
        grouped_events = group_by_system(tagged)
        for system, labels in grouped_events.items():
            out.append(
                Liability(
                    system=system,
                    severity="high",
                    source="Open Targets safety",
                    evidence=labels[:4],
                    perturbation=tag,
                )
            )
        unplaced = [e for e in tagged if not any(e in v for v in grouped_events.values())]
        if unplaced:
            out.append(
                Liability(
                    system="curated adverse effects",
                    severity="high",
                    source="Open Targets safety",
                    evidence=unplaced[:4],
                    perturbation=tag,
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
                perturbation="decrease",
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
                perturbation="decrease",
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
                perturbation="decrease",
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
                perturbation="decrease",
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
                    perturbation=requested,
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
                perturbation=requested,
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
                perturbation=requested,
            )
        )

    lit: dict[str, list[tuple[str, str]]] = {}
    for perturbation, src, detail in literature_harms:
        if perturbation in ("decrease", "increase"):
            lit.setdefault(perturbation, []).append((src, detail))
    for perturbation, found in lit.items():
        out.append(
            Liability(
                system=literature_harm_system(perturbation),
                severity="medium",
                source=", ".join(dict.fromkeys(src for src, _ in found[:4]))[:120],
                evidence=[" ".join(detail.split())[:160] for _, detail in found[:4]],
                perturbation=perturbation,
            )
        )

    return sorted(out, key=lambda li: _RANK[li.severity])


def split_by_direction(
    items: Iterable[Liability], direction: str
) -> tuple[list[Liability], list[Liability], bool]:
    """(counted, opposite, assessed) for the run's direction.

    Counted are the items caused by the requested change or by either; opposite the items
    of the other change. ``assessed`` says whether any counted item is tagged with the
    requested change itself; human exposure to same-direction drugs is the caller's to
    add. A run without a requested change counts everything and is assessed.
    """
    requested = requested_effect(direction)
    items = list(items)
    if requested not in ("decrease", "increase"):
        return items, [], True
    counted = [li for li in items if li.perturbation in (requested, "either")]
    opposite = [li for li in items if li.perturbation not in (requested, "either")]
    return counted, opposite, any(li.perturbation == requested for li in counted)


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
