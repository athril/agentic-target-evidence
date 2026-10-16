# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Direction of effect: which way of changing the target's activity helps the disease.

A run tests (gene, disease, direction). Most evidence about a gene reads the same for
opposite directions, so the lenses supported activating a target whose loss of function
protects against the disease as readily as inhibiting it. This module records each
piece of evidence that does say which way helps, as one typed observation:

    perturbation  — the change in the target's activity observed (decrease / increase)
    outcome       — what it did to the disease (benefit / harm)
    evidence      — the kind of evidence (see ``EVIDENCE_WEIGHTS``)
    in_scope      — whether it concerns the run's disease (or a subtype), not a related one

and aggregates them into one reading for the requested direction: supports, contradicts,
conflicting or unknown. Every lens and the decision gates read the same reading, instead
of each re-deriving direction from prose. An observation favours the effect it shows
helping: a benefit favours its perturbation, a harm the opposite one.

Sources (all deterministic): drugs approved in the indication, the Mendelian mechanism
inferred from the pathogenic-variant spectrum (constraint_interpret), human-genetics
claims whose direction was extracted without the run's direction in view, and drug
labels warning that a drug acting on the target can cause or worsen the indication.

Pure module: no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel

from services.evidence.drug_precedent import (
    Effect,
    PrecedentReading,
    drug_effect,
    requested_effect,
)

Perturbation = Literal["decrease", "increase"]
Outcome = Literal["benefit", "harm"]
EvidenceKind = Literal["approved_drug", "label_warning", "mendelian", "genetics_literature"]
Verdict = Literal["supports", "contradicts", "conflicting", "unknown"]

# Strength of one observation of each kind, before scope. A drug approved in the
# indication is the clinic's verdict on the direction; a labelled warning that the drug
# can cause or worsen the disease is nearly as direct; the Mendelian mechanism of the
# disease (or of a related disorder sharing its biology) is causal human evidence; one
# literature claim is weak and literature as a whole is capped, so it never decides alone.
EVIDENCE_WEIGHTS: dict[EvidenceKind, float] = {
    "approved_drug": 0.9,
    "label_warning": 0.7,
    "mendelian": 0.8,
    "genetics_literature": 0.25,
}
_KIND_CAP: dict[EvidenceKind, float] = {"genetics_literature": 0.6}
_OUT_OF_SCOPE_FACTOR = 0.5  # evidence about a related disorder, not this disease
_MIN_STRENGTH = 0.3  # below this, a side has said nothing
_CONFLICT_RATIO = 0.5  # weaker side ≥ half the stronger: conflicting
HUMAN_GENETICS: frozenset[EvidenceKind] = frozenset({"mendelian", "genetics_literature"})


class DirectionObservation(BaseModel):
    perturbation: Perturbation
    outcome: Outcome
    evidence: EvidenceKind
    in_scope: bool = True
    weight: float  # strength of this observation, scope and confidence applied
    source: str  # what to cite: a drug, a curation, a claim id
    detail: str

    @property
    def favours(self) -> Perturbation:
        if self.outcome == "benefit":
            return self.perturbation
        return "increase" if self.perturbation == "decrease" else "decrease"


class DirectionReading(BaseModel):
    requested: Effect
    verdict: Verdict
    support: float  # combined strength of observations favouring the requested effect
    against: float  # … favouring the opposite effect
    observations: list[DirectionObservation] = []

    @property
    def supporting(self) -> list[DirectionObservation]:
        return [o for o in self.observations if o.favours == self.requested]

    @property
    def contradicting(self) -> list[DirectionObservation]:
        return [o for o in self.observations if o.favours != self.requested]

    def restricted_to(self, kinds: Iterable[EvidenceKind]) -> DirectionReading:
        """The reading over only these kinds of evidence (e.g. human genetics)."""
        keep = set(kinds)
        return assess_direction(
            self.requested, [o for o in self.observations if o.evidence in keep]
        )

    def summary(self) -> str:
        if self.requested == "unknown":
            return "The requested direction has no defined effect on the target."
        verb = "reducing" if self.requested == "decrease" else "increasing"
        if self.verdict == "unknown":
            return f"No evidence in this run says whether {verb} the target's activity helps."
        word = {
            "supports": "supports",
            "contradicts": "contradicts",
            "conflicting": "is conflicting on",
        }[self.verdict]
        return (
            f"The direction-of-effect evidence {word} {verb} the target's activity "
            f"(for {self.support:.2f}, against {self.against:.2f})."
        )

    def render(self, max_items: int = 8) -> str:
        """Prompt / report block: the reading and its strongest observations."""
        if self.requested == "unknown" or not self.observations:
            return ""
        ranked = sorted(self.observations, key=lambda o: -o.weight)[:max_items]
        lines = [f"Direction of effect (deterministic): {self.summary()}"]
        for o in ranked:
            side = "FOR" if o.favours == self.requested else "AGAINST"
            scope = "" if o.in_scope else " [related disorder]"
            lines.append(f"  - {side} ({o.evidence}{scope}): {o.detail}")
        return "\n".join(lines)


def _combine(weights: Iterable[float]) -> float:
    remaining = 1.0
    for w in weights:
        remaining *= 1.0 - max(0.0, min(1.0, w))
    return 1.0 - remaining


def _strength(observations: list[DirectionObservation]) -> float:
    by_kind: dict[EvidenceKind, list[float]] = {}
    for o in observations:
        by_kind.setdefault(o.evidence, []).append(o.weight)
    return _combine(min(_KIND_CAP.get(kind, 1.0), _combine(ws)) for kind, ws in by_kind.items())


def assess_direction(
    requested: Effect, observations: Iterable[DirectionObservation]
) -> DirectionReading:
    obs = list(observations)
    if requested == "unknown":
        return DirectionReading(
            requested=requested, verdict="unknown", support=0, against=0, observations=obs
        )
    support = _strength([o for o in obs if o.favours == requested])
    against = _strength([o for o in obs if o.favours != requested])
    strong, weak = max(support, against), min(support, against)
    verdict: Verdict
    if strong < _MIN_STRENGTH:
        verdict = "unknown"
    elif weak >= _MIN_STRENGTH and weak >= _CONFLICT_RATIO * strong:
        verdict = "conflicting"
    else:
        verdict = "supports" if support > against else "contradicts"
    return DirectionReading(
        requested=requested,
        verdict=verdict,
        support=round(support, 3),
        against=round(against, 3),
        observations=obs,
    )


# ---------------------------------------------------------------------------
# Observation builders
# ---------------------------------------------------------------------------


def from_precedent(reading: PrecedentReading, disease: str) -> list[DirectionObservation]:
    """Drugs approved in the indication: the clinic's verdict on the direction."""
    out: list[DirectionObservation] = []
    for d in [*reading.concordant, *reading.discordant]:
        if not d.approved or d.effect == "unknown":
            continue
        out.append(
            DirectionObservation(
                perturbation=d.effect,
                outcome="benefit",
                evidence="approved_drug",
                weight=EVIDENCE_WEIGHTS["approved_drug"],
                source=d.drug,
                detail=f"{d.drug.lower()} ({d.action_type.lower() or 'drug'}) is approved in {disease}",
            )
        )
    return out


def from_mechanism(
    mechanism: dict[str, Any] | None, *, in_scope: bool, disorder: str
) -> list[DirectionObservation]:
    """The Mendelian mechanism of the disease (or of a related disorder of the gene).

    Gain of function causing disease is harm from more activity; loss of function
    causing disease is harm from less activity.
    """
    if not mechanism or mechanism.get("mechanism") not in ("gof", "lof"):
        return []
    gof = mechanism["mechanism"] == "gof"
    weight = EVIDENCE_WEIGHTS["mendelian"] * float(mechanism.get("confidence") or 0.0)
    if not in_scope:
        weight *= _OUT_OF_SCOPE_FACTOR
    if weight <= 0:
        return []
    return [
        DirectionObservation(
            perturbation="increase" if gof else "decrease",
            outcome="harm",
            evidence="mendelian",
            in_scope=in_scope,
            weight=round(weight, 3),
            source="ClinVar pathogenic variant spectrum",
            detail=(
                f"pathogenic variants act by {'gain' if gof else 'loss'} of function in {disorder}"
            ),
        )
    ]


_CLAIM_EFFECT: dict[str, Perturbation] = {
    "inhibit": "decrease",
    "degrade": "decrease",
    "activate": "increase",
}


def from_claims(claims: Iterable[Any]) -> list[DirectionObservation]:
    """Human-genetics claims whose extracted direction says which way helps.

    ``claims`` are CoreClaims routed to the genetics lens; their ``direction`` was
    extracted without the run's direction in view (services/evidence/claim_extraction).
    """
    out: list[DirectionObservation] = []
    for c in claims:
        direction = getattr(
            getattr(c, "direction", None), "value", str(getattr(c, "direction", ""))
        )
        effect = _CLAIM_EFFECT.get(direction)
        if effect is None:
            continue
        out.append(
            DirectionObservation(
                perturbation=effect,
                outcome="benefit",
                evidence="genetics_literature",
                weight=EVIDENCE_WEIGHTS["genetics_literature"],
                source=str(getattr(c, "evidence_id", "")),
                detail=str(getattr(c, "claim_text", ""))[:160],
            )
        )
    return out


# Wording of a label warning that the drug can cause or worsen a condition (not, say,
# that patients with it were excluded from trials).
_HARM_CUE = re.compile(
    r"exacerbat|worsen|new[- ]onset|flare|aggravat|onset of|cases of|induc|precipitat",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.;])\s+")


def _mentions(sentence: str, names: Iterable[str]) -> str:
    low = sentence.lower()
    for n in sorted(names, key=len, reverse=True):
        if len(n) >= 4 and re.search(rf"\b{re.escape(n.lower())}\b", low):
            return n
    return ""


def from_label_warnings(
    labels: Iterable[dict[str, Any]],
    *,
    disease_names: Iterable[str],
    parent_names: Iterable[str] = (),
) -> list[DirectionObservation]:
    """Labels of drugs acting on the target that warn the drug can cause or worsen the
    run's disease (by its name or synonyms) or its parent condition.

    ``labels`` are openFDA label ``extra`` dicts: ``target_drug``, ``target_action_type``,
    ``drug_name``, ``warnings``, ``boxed_warning``.
    """
    names, parents = list(disease_names), list(parent_names)
    out: list[DirectionObservation] = []
    for lab in labels:
        if not lab.get("target_drug"):
            continue
        effect = drug_effect(lab.get("target_action_type"))
        if effect == "unknown":
            continue
        text = " ".join(str(lab.get(k) or "") for k in ("boxed_warning", "warnings"))
        best: tuple[bool, str] | None = None  # (names the disease itself, sentence)
        for sentence in _SENTENCE.split(text):
            if not _HARM_CUE.search(sentence):
                continue
            if _mentions(sentence, names):
                best = (True, sentence)
                break
            if best is None and _mentions(sentence, parents):
                best = (False, sentence)
        if best is None:
            continue
        in_scope, sentence = best
        weight = EVIDENCE_WEIGHTS["label_warning"] * (1.0 if in_scope else _OUT_OF_SCOPE_FACTOR)
        drug = str(lab.get("drug_name") or "drug").lower()
        out.append(
            DirectionObservation(
                perturbation=effect,
                outcome="harm",
                evidence="label_warning",
                in_scope=in_scope,
                weight=round(weight, 3),
                source=drug,
                detail=f'{drug} label warns: "{sentence.strip()[:200]}"',
            )
        )
    return out


def build_direction_reading(
    direction: str,
    *,
    disease: str,
    precedent: PrecedentReading | None = None,
    mechanism: dict[str, Any] | None = None,
    mechanism_in_scope: bool = False,
    mechanism_disorder: str = "",
    genetics_claims: Iterable[Any] = (),
    labels: Iterable[dict[str, Any]] = (),
    disease_names: Iterable[str] = (),
    parent_names: Iterable[str] = (),
) -> DirectionReading:
    """All direction-of-effect observations for the run, aggregated for ``direction``."""
    names = {disease, *disease_names} - {""}
    observations = [
        *(from_precedent(precedent, disease) if precedent is not None else []),
        *from_mechanism(
            mechanism,
            in_scope=mechanism_in_scope,
            disorder=mechanism_disorder
            or (disease if mechanism_in_scope else "a related disorder"),
        ),
        *from_claims(genetics_claims),
        *from_label_warnings(labels, disease_names=names, parent_names=parent_names),
    ]
    return assess_direction(requested_effect(direction), observations)
