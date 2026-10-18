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

import math
import re
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel

from core.evidence_text import disease_name_variants, mentions_disease, normalize_typography
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
# Literature claims are many weak votes whose extracted direction is often wrong both
# ways, so they are netted rather than pooled per side: pooling let a handful of
# mis-tagged claims on each side saturate both at the cap and read "conflicting" for
# nearly every gene. The net vote counts against the noise expected from that many
# votes (margin / sqrt(votes)), rising toward the cap: one claim, or 10 against 7, says
# nothing; 4 unanimous claims support; 8 unanimous (or 12 against 1) reach the strength
# that may overrule a lens.
_NETTED_KINDS: frozenset[EvidenceKind] = frozenset({"genetics_literature"})
_LITERATURE_NOISE_SCALE = 1.5


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


def _strength(observations: list[DirectionObservation], netted: float = 0.0) -> float:
    """Combined strength of one side: noisy-OR within each kind (capped), then across
    kinds, plus ``netted`` — this side's share of the netted kinds."""
    by_kind: dict[EvidenceKind, list[float]] = {}
    for o in observations:
        if o.evidence not in _NETTED_KINDS:
            by_kind.setdefault(o.evidence, []).append(o.weight)
    return _combine(
        [
            *(min(_KIND_CAP.get(kind, 1.0), _combine(ws)) for kind, ws in by_kind.items()),
            netted,
        ]
    )


def _votes(observations: list[DirectionObservation], kind: EvidenceKind) -> float:
    """Observations of ``kind`` in units of one in-scope observation of that kind."""
    unit = EVIDENCE_WEIGHTS[kind]
    return sum(o.weight / unit for o in observations if o.evidence == kind)


def _netted_strength(margin: float, votes: float, kind: EvidenceKind) -> float:
    """Strength of a net vote of ``margin`` out of ``votes`` (see ``_NETTED_KINDS``)."""
    if margin <= 0 or votes <= 0:
        return 0.0
    z = margin / math.sqrt(votes)
    return _KIND_CAP.get(kind, 1.0) * (1.0 - math.exp(-z / _LITERATURE_NOISE_SCALE))


def assess_direction(
    requested: Effect, observations: Iterable[DirectionObservation]
) -> DirectionReading:
    obs = list(observations)
    if requested == "unknown":
        return DirectionReading(
            requested=requested, verdict="unknown", support=0, against=0, observations=obs
        )
    for_obs = [o for o in obs if o.favours == requested]
    against_obs = [o for o in obs if o.favours != requested]
    net_for = net_against = 0.0
    for kind in _NETTED_KINDS:
        v_for, v_against = _votes(for_obs, kind), _votes(against_obs, kind)
        strength = _netted_strength(abs(v_for - v_against), v_for + v_against, kind)
        if v_for > v_against:
            net_for = _combine([net_for, strength])
        else:
            net_against = _combine([net_against, strength])
    support = _strength(for_obs, net_for)
    against = _strength(against_obs, net_against)
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


# Claims about an experimental system rather than people: a disease model, an induced
# phenotype. The genetics lens's routing already drops claims naming a species or cell
# system; these name the model by how it was made.
_EXPERIMENTAL_SYSTEM = re.compile(
    r"\b(?:animal|disease|experimental|preclinical|in\s+vivo)\s+models?\b|\bmodels?\s+of\b|"
    r"\w-induced\b",
    re.IGNORECASE,
)
# A word-like token starting with a capital letter, hyphenated parts included.
_TOKEN = re.compile(r"(?<![A-Za-z0-9-])[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*(?![A-Za-z0-9-])")
_PROTEIN_CHANGE = re.compile(r"[A-Z]\d+[A-Z]")
_ABBREVIATION = re.compile(r"[A-Z0-9]{2,6}")


def _gene_pattern(symbol: str) -> re.Pattern[str]:
    """A gene symbol as papers write it: letters and digits may be split by a hyphen or a
    space (a symbol "AB12C" also matches "AB-12C")."""
    parts = re.findall(r"[A-Za-z]+|\d+", symbol)
    body = r"[\s-]?".join(re.escape(p) for p in parts)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def _gene_like(token: str) -> bool:
    """A token shaped like a gene symbol: two or more capitals and a digit ("ABC3",
    "AB-1"), not a protein change ("R46L")."""
    return (
        len(token) >= 3
        and any(ch.isdigit() for ch in token)
        and sum(ch.isupper() for ch in token) >= 2
        and not _PROTEIN_CHANGE.fullmatch(token)
    )


def about_other_genes(text: str, symbols: Iterable[str]) -> bool:
    """The text names other gene-like symbols and none of ``symbols``.

    A literature claim retrieved for a target often comes from a paper about several
    genes; a claim naming only other genes is about them. A claim naming no gene symbol
    at all (it may use the protein's name) is not judged."""
    patterns = [_gene_pattern(s) for s in symbols if s]
    if not patterns or any(p.search(text) for p in patterns):
        return False
    return any(
        _gene_like(t) and not any(p.fullmatch(t) for p in patterns) for t in _TOKEN.findall(text)
    )


def names_condition(text: str, name: str) -> bool:
    """``text`` names the condition ``name``: an abbreviation ("AB") only in its exact
    case, any other name case-insensitively with its eponym forms."""
    name = (name or "").strip()
    if not name or not text:
        return False
    if _ABBREVIATION.fullmatch(name):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text) is not None
    return mentions_disease(text, name)


def from_claims(
    claims: Iterable[Any],
    *,
    gene: str = "",
    gene_aliases: Iterable[str] = (),
    disease_names: Iterable[str] = (),
    parent_names: Iterable[str] = (),
) -> list[DirectionObservation]:
    """Human-genetics claims whose extracted direction says which way helps.

    ``claims`` are CoreClaims routed to the genetics lens; their ``direction`` was
    extracted without the run's direction in view (services/evidence/claim_extraction).

    Direction of effect is specific to a gene and a disease: the same target can protect
    against one disease and drive another. With ``gene`` given, a claim naming only other
    genes is skipped; with ``disease_names`` given, a claim must name the disease (or,
    at reduced weight, a parent condition), and a claim about a disease model is skipped.
    """
    symbols = [s for s in (gene, *gene_aliases) if s]
    names = [n for n in disease_names if n]
    parents = [p for n in parent_names for p in condition_forms(n, parent=True)]
    out: list[DirectionObservation] = []
    for c in claims:
        direction = getattr(
            getattr(c, "direction", None), "value", str(getattr(c, "direction", ""))
        )
        effect = _CLAIM_EFFECT.get(direction)
        if effect is None:
            continue
        text = str(getattr(c, "claim_text", ""))
        if symbols and about_other_genes(text, symbols):
            continue
        in_scope = True
        if names:
            if _EXPERIMENTAL_SYSTEM.search(text):
                continue
            if not any(names_condition(text, n) for n in names):
                if not any(p.search(normalize_typography(text)) for p in parents):
                    continue
                in_scope = False
        weight = EVIDENCE_WEIGHTS["genetics_literature"] * (
            1.0 if in_scope else _OUT_OF_SCOPE_FACTOR
        )
        out.append(
            DirectionObservation(
                perturbation=effect,
                outcome="benefit",
                evidence="genetics_literature",
                in_scope=in_scope,
                weight=weight,
                source=str(getattr(c, "evidence_id", "")),
                detail=text[:160],
            )
        )
    return out


# Wording of a label warning that the drug can cause or worsen a condition (not, say,
# that patients with it were excluded from trials). The cue must govern the condition:
# shortly before it in the same clause ("exacerbation or new onset of <condition>",
# "cases of <condition>") or right after it ("<condition>: exacerbation or new onset may
# occur", "<condition>, including exacerbations, occurred"). "Drug-induced <injury>"
# describes the injury, not the condition named later in the sentence.
_CUE = (
    r"exacerbat\w*|worsen\w*|aggravat\w*|flares?|new[- ]onset|onset|cases|"
    r"(?<!-)induc(?:e|es|ed|ing|tion)|precipitat\w*"
)
_CUE_BEFORE = re.compile(rf"\b(?:{_CUE})\b[^.;]{{0,90}}$", re.IGNORECASE)
_CUE_AFTER = re.compile(rf"^[^.;]{{0,60}}?\b(?:{_CUE})", re.IGNORECASE)
# The condition named as the population a statement is about: "subjects with <condition>",
# "patients with pre-existing <condition>", "a history of <condition>".
_POPULATION = re.compile(
    r"\b(?:patients?|subjects?|individuals?|people|participants?|women|men|adults|children|"
    r"those)\s+(?:[\w,'-]+\s+){0,3}?with\s+(?:[\w,'-]+\s+){0,8}?$|"
    r"\bhistory\s+of\s+(?:[\w,'-]+\s+){0,3}?$",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:no|not|without)\b[^.;]{0,60}$", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.;])\s+")


def condition_forms(name: str, *, parent: bool = False) -> list[re.Pattern[str]]:
    """Patterns for how labels and papers name a condition: its eponym forms, and for a
    parent term also its head without a trailing anatomical qualifier ("<kind> disease of
    <site>" → "<kind> disease") with "disease"/"disorder" and plural variants.

    A one-word parent ("dermatitis", "leukemia") is a broad category, not a condition a
    warning can be about, so it yields no pattern; "<condition>-like" is not the
    condition."""
    name = normalize_typography(name or "").strip()
    if not name or (parent and len(name.split()) < 2):
        return []
    if _ABBREVIATION.fullmatch(name):
        return [re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])")]
    forms = set(disease_name_variants(name))
    if parent:
        head = re.split(r"\s+of\s+", name, maxsplit=1)[0]
        if len(head.split()) >= 2:
            forms.add(head)
        for f in list(forms):
            forms.add(re.sub(r"\bdisease\b", "disorder", f))
            forms.add(re.sub(r"\bdisorder\b", "disease", f))
    patterns = []
    for f in forms:
        words = [re.escape(w) for w in re.split(r"[\s-]+", f) if w]
        body = r"[\s-]+".join(words)
        patterns.append(
            re.compile(rf"(?<![A-Za-z0-9]){body}s?(?![A-Za-z0-9]|-like)", re.IGNORECASE)
        )
    return patterns


def warns_of(sentence: str, forms: Iterable[re.Pattern[str]]) -> bool:
    """The sentence warns that the drug can cause or worsen the condition (see ``_CUE``)."""
    sentence = normalize_typography(sentence)
    for pattern in forms:
        for m in pattern.finditer(sentence):
            before, after = sentence[: m.start()], sentence[m.end() :]
            if _POPULATION.search(before) or _NEGATION.search(before):
                continue
            if _CUE_BEFORE.search(before) or _CUE_AFTER.search(after):
                return True
    return False


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
    names = [p for n in disease_names for p in condition_forms(n)]
    parents = [p for n in parent_names for p in condition_forms(n, parent=True)]
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
            if warns_of(sentence, names):
                best = (True, sentence)
                break
            if best is None and warns_of(sentence, parents):
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
    gene: str = "",
    gene_aliases: Iterable[str] = (),
) -> DirectionReading:
    """All direction-of-effect observations for the run, aggregated for ``direction``.

    ``gene`` (and its aliases) and the disease's names scope the literature claims to
    this target and this disease (see `from_claims`)."""
    names = {disease, *disease_names} - {""}
    parents = list(parent_names)
    observations = [
        *(from_precedent(precedent, disease) if precedent is not None else []),
        *from_mechanism(
            mechanism,
            in_scope=mechanism_in_scope,
            disorder=mechanism_disorder
            or (disease if mechanism_in_scope else "a related disorder"),
        ),
        *from_claims(
            genetics_claims,
            gene=gene,
            gene_aliases=gene_aliases,
            disease_names=names,
            parent_names=parents,
        ),
        *from_label_warnings(labels, disease_names=names, parent_names=parents),
    ]
    return assess_direction(requested_effect(direction), observations)
