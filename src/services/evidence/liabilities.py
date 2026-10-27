# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Candidate on-target liabilities, built deterministically from a gene's own data.

The safety lens was free to leave a liability out (LRRK2: lung and kidney knockout
phenotypes omitted, verdict "support 93%"), and the report had no place that listed
liabilities at all. This module derives the list from structured evidence — curated
Open Targets safety events, mouse knockout phenotypes, gnomAD constraint against the
therapeutic direction, pan-essentiality, boxed warnings and safety stops of drugs acting
on the target, and label warnings that such a drug worsens the disease — so the safety
lens computes its verdict from them, the report prints them and the suitability score
accounts for them. Pure module: no I/O.

Each liability is one finding and carries the change of the target that causes it
(``perturbation``): mouse knockouts, haploinsufficiency and essentiality are harms of
lowering it; Open Targets events take their curated ``effects.direction``; drug warnings
and trial stops are of drugs acting in the requested direction. An activation run once
read as safe on knockout phenotypes and inhibitor exposure. ``split_by_direction`` keeps
the items of the requested change (or of either), and says whether data on that change
were examined at all: data on the harms of raising a target are thin, and an activator
must not look safer only because less is recorded.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel

from services.evidence.drug_precedent import drug_key, names_drug, requested_effect
from services.evidence.organ_systems import group_by_system

Severity = Literal["high", "medium", "low"]
Perturbation = Literal["decrease", "increase", "either"]
# The liability a label warning that a requested-direction drug causes or worsens the
# disease becomes: a harm event in the safety verdict, efficacy evidence for the score.
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


# Open Targets safety-liability datasources whose rows are not harms of acting on the
# target in people: a ToxCast row names an in vitro assay's biological process ("receptor
# binding", "regulation of catalytic activity"), an AOP-Wiki row an adverse-outcome
# pathway built for chemical hazard assessment, some ending at population endpoints
# ("Decrease, Population growth rate"). Each unplaced row became a high liability: IL6R,
# FGFR3, EGFR and JAK2 each got one from a single assay.
NON_HARM_SOURCES = frozenset({"toxcast", "aop-wiki"})

# The findings the curated Open Targets events, and the mouse phenotypes, of one change of
# the target become; the boxed warning of one drug; one compound's trials stopped for
# safety in this indication.
CURATED_EVENTS = "curated adverse events"
MOUSE_PHENOTYPES = "mouse knockout phenotypes"
BOXED_WARNING = "labelled boxed warning"
SAFETY_STOP = "trials stopped for safety"


# Open Targets ``effects.direction`` words, as the change of the target that causes the
# event. Open Targets writes them joined ("Inhibition/Decrease/Downregulation"); read one
# word at a time, every event once came out "either".
_EFFECT_DIRECTIONS: dict[str, Perturbation] = {
    "activation": "increase",
    "increase": "increase",
    "upregulation": "increase",
    "inhibition": "decrease",
    "decrease": "decrease",
    "downregulation": "decrease",
}


def _one_change(found: set[Perturbation]) -> Perturbation | None:
    if not found:
        return None
    return found.pop() if len(found) == 1 else "either"


def _direction_of(value: Any) -> Perturbation | None:
    """The change of the target one ``effects.direction`` value names, reading each part
    of "Inhibition/Decrease/Downregulation"; None when no part names a change."""
    found: set[Perturbation] = set()
    for part in str(value or "").split("/"):
        change = _EFFECT_DIRECTIONS.get(part.strip().lower())
        if change is not None:
            found.add(change)
    return _one_change(found)


def _event_direction(effects: Iterable[Any]) -> Perturbation | None:
    """The change of the target a row's curated effects name: one direction, "either"
    when they name both, None when they name none."""
    found: set[Perturbation] = set()
    for e in effects:
        if isinstance(e, Mapping) and (change := _direction_of(e.get("direction"))):
            found.add(change)
    return _one_change(found)


def _not_counted(row: Mapping[str, Any]) -> str:
    """Why an Open Targets safety row is listed but not counted ("" when it counts)."""
    if is_pharmacogenomic(row):
        return "pharmacogenomic"
    if str(row.get("datasource") or "").strip().lower() in NON_HARM_SOURCES:
        return "not a harm"
    if _event_direction(row.get("effects") or []) is None:
        return "no direction"
    return ""


def target_safety(ot: Mapping[str, Any]) -> dict[str, Any]:
    """The Open Targets safety liabilities that are toxicities of acting on the target,
    from an Open Targets record's ``extra``: ``liabilities``, ``events``, ``count``,
    ``text`` (rewritten when rows were left out), the rows left out — ``pharmacogenomic``,
    and ``listed`` (rows of ``NON_HARM_SOURCES`` and rows whose effects name no change of
    the target, which would otherwise count against both changes) — and
    ``event_directions`` ({event: decrease | increase | either}, from the events'
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
            "listed": [],
            "event_directions": {e: "either" for e in events},
        }
    why = [_not_counted(li) for li in rows]
    keep = [li for li, w in zip(rows, why, strict=True) if not w]
    pgx = [li for li, w in zip(rows, why, strict=True) if w == "pharmacogenomic"]
    listed = [li for li, w in zip(rows, why, strict=True) if w in ("not a harm", "no direction")]
    events = list(dict.fromkeys(str(li.get("event") or "") for li in keep if li.get("event")))
    text = str(ot.get("safety_text") or "")
    if pgx or listed:
        named = f" Events include: {', '.join(events[:6])}." if events else ""
        left_out = [
            f"{len(pgx)} pharmacogenomic record(s) (variants changing a drug's response, "
            "not toxicities of acting on the target)"
            if pgx
            else "",
            f"{len(listed)} record(s) that are not harms in people (in vitro assays, "
            "adverse-outcome pathways) or name no change of the target"
            if listed
            else "",
        ]
        text = (
            f"Open Targets safety: {len(keep)} liability event(s).{named} "
            f"{'; '.join(x for x in left_out if x)} left out."
        )
    directions: dict[str, set[Perturbation]] = {}
    for li in keep:
        change = _event_direction(li.get("effects") or [])
        if li.get("event") and change is not None:
            directions.setdefault(str(li["event"]), set()).add(change)
    return {
        "liabilities": keep,
        "events": events,
        "count": len(keep),
        "text": text,
        "pharmacogenomic": pgx,
        "listed": listed,
        "event_directions": {e: _one_change(d) or "either" for e, d in directions.items()},
    }


class Liability(BaseModel):
    system: str  # organ system or mechanism ("lung", "dosage sensitivity")
    severity: Severity
    source: str  # where the evidence comes from
    evidence: list[str]  # short labels, quoted from the source
    # The change of the target that causes it; "either" when the source does not say.
    perturbation: Perturbation = "either"
    # The organ systems a liability touches when it is one finding over several of them
    # (the curated events or the mouse phenotypes of one change); empty when ``system`` is
    # the organ system.
    systems: list[str] = []
    # Where human evidence of it was seen: a drug's label or trials in this indication or
    # in other indications only; "" when no person was exposed to it.
    seen: Literal["", "this indication", "other indications"] = ""


def _metric(value: Any) -> str:
    """A constraint metric as written in prose: three decimals (PLK1's LOEUF reached the
    safety text as 0.20879900735526438)."""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def _named(name: str, systems: list[str]) -> str:
    return f"{name} ({', '.join(systems)})" if systems else name


def candidate_liabilities(
    *,
    safety_events: Iterable[str] = (),
    mouse_phenotypes: Iterable[str] = (),
    constraint: dict[str, Any] | None = None,
    direction: str = "",
    common_essential: bool = False,
    boxed_warnings: Iterable[tuple[str, str]] = (),
    approved_here: Iterable[str] = (),
    safety_stops: Mapping[str, Iterable[str]] | None = None,
    direction_harms: Iterable[tuple[str, str]] = (),
    event_directions: Mapping[str, str] | None = None,
) -> list[Liability]:
    """Every candidate on-target liability the structured data support, most severe first.

    Each is one finding: one drug's boxed warning, one compound's safety stops in this
    indication, the curated events or the mouse phenotypes of one change of the target
    (listing their organ systems), constraint, essentiality. A finding over several
    organ systems counts once.

    ``boxed_warnings`` are (drug, warning text) pairs for drugs whose label names the
    target in its mechanism of action; a competitor's warning is not passed in.
    ``approved_here`` names those approved in this indication (``drug_key``).
    ``safety_stops`` maps a requested-direction drug to its trials in this indication
    stopped for safety (Open Targets stop category ``Safety_Sideeffects``).
    ``direction_harms`` are (source, detail) pairs of label warnings that a
    requested-direction drug causes or worsens the disease itself.
    ``event_directions`` ({event: decrease | increase | either}, ``target_safety``) splits
    the Open Targets events by the change that causes them; the events of one change are
    one finding, listing the organ systems they name.
    Every mouse phenotype is read as a harm of lowering the target, one medium finding: a
    phenotype names a system to monitor, not a harm a drug will cause.
    """
    out: list[Liability] = []
    requested: Perturbation = (
        "either" if (eff := requested_effect(direction)) not in ("decrease", "increase") else eff
    )
    tags = event_directions or {}
    here = {drug_key(d) for d in approved_here}

    # The events of one change are one finding: one review lists many effects of one
    # mechanism (Brennan et al. 13 for EGFR, Lynch et al. 10 for PPARG), and counted per
    # organ system they reached "two open high liabilities" alone.
    events = list(dict.fromkeys(str(e).strip() for e in safety_events if str(e).strip()))
    by_tag: dict[str, list[str]] = {}
    for e in events:
        by_tag.setdefault(tags.get(e, "either"), []).append(e)
    for tag, tagged in by_tag.items():
        systems = list(group_by_system(tagged))
        out.append(
            Liability(
                system=_named(CURATED_EVENTS, systems),
                severity="high",
                source="Open Targets safety",
                evidence=tagged[:6],
                perturbation=tag,
                systems=systems,
            )
        )

    grouped = group_by_system(mouse_phenotypes)
    if grouped:
        out.append(
            Liability(
                system=_named(MOUSE_PHENOTYPES, list(grouped)),
                severity="medium",
                source="mouse knockout (MGI/IMPC)",
                # One label per system, so every system is named.
                evidence=[labels[0] for labels in grouped.values()][:12],
                perturbation="decrease",
                systems=list(grouped),
            )
        )

    c = constraint or {}
    loeuf, pli = _metric(c.get("loeuf")), _metric(c.get("pli"))
    # Inhibition and degradation both lower the target's dosage.
    lowers = requested_effect(direction) == "decrease"
    if c.get("claims_haploinsufficiency_ok") and lowers:
        out.append(
            Liability(
                system="dosage sensitivity",
                severity="high",
                source="gnomAD constraint",
                evidence=[f"LOEUF {loeuf} — haploinsufficient; inhibition may mimic it"],
                perturbation="decrease",
            )
        )
    elif c.get("is_lof_constrained") and lowers:
        out.append(
            Liability(
                system="dosage sensitivity",
                severity="medium",
                source="gnomAD constraint",
                evidence=[f"loss-of-function constrained (LOEUF {loeuf}, pLI {pli})"],
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
                    system=BOXED_WARNING,
                    severity="high",
                    source=f"FDA label ({drug})",
                    evidence=[" ".join(text.split())[:160]],
                    perturbation=requested,
                    seen="this indication" if names_drug(drug, here) else "other indications",
                )
            )

    for drug, reports in (safety_stops or {}).items():
        stops = [" ".join(str(r).split())[:160] for r in reports if str(r).strip()]
        if stops:
            out.append(
                Liability(
                    system=SAFETY_STOP,
                    severity="high",
                    source=str(drug).lower(),
                    evidence=stops[:4],
                    perturbation=requested,
                    seen="this indication",
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
                seen="this indication",
            )
        )

    return sorted(out, key=lambda li: _RANK[li.severity])


def split_by_direction(
    items: Iterable[Liability], direction: str, *, examined: Iterable[str] = ()
) -> tuple[list[Liability], list[Liability], bool]:
    """(counted, opposite, covered) for the run's direction.

    Counted are the items caused by the requested change or by either; opposite the items
    of the other change. ``covered`` says whether data on the requested change were
    examined: an item tagged with it, or a change in ``examined`` (data that showed no
    harm, such as homozygous loss-of-function carriers for lowering). A change is covered
    whatever the data showed: finding no harm must not leave it "not assessed". Human
    exposure to same-direction drugs is the caller's to add. A run without a requested
    change counts everything and is covered.
    """
    requested = requested_effect(direction)
    items = list(items)
    if requested not in ("decrease", "increase"):
        return items, [], True
    counted = [li for li in items if li.perturbation in (requested, "either")]
    opposite = [li for li in items if li.perturbation not in (requested, "either")]
    covered = requested in set(examined) or any(li.perturbation == requested for li in counted)
    return counted, opposite, covered
