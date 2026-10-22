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
inferred from the pathogenic-variant spectrum (constraint_interpret), Open Targets
direction annotations and GWAS–QTL colocalisations, statements of grounded sentences in
which carriers of a variant of the target whose effect is known from data had more or
less of the disease, and drug labels warning that a drug acting on the target can cause
or worsen the indication.

Pure module: no I/O.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel

from core.evidence_text import disease_name_variants, normalize_typography
from schemas.statement import TargetStatement
from services.evidence.disease_scope import DiseaseScope
from services.evidence.drug_precedent import (
    Effect,
    PrecedentReading,
    drug_effect,
    requested_effect,
)

Perturbation = Literal["decrease", "increase"]
Outcome = Literal["benefit", "harm"]
EvidenceKind = Literal[
    "approved_drug",
    "label_warning",
    "mendelian",
    "genetics_structured",
    "genetics_qtl",
    "genetics_literature",
    "preclinical_perturbation",
]
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
    # Open Targets burden and credible-set rows with a direction annotation: measured in
    # cohorts, stronger than one literature sentence, weaker than a curated mechanism.
    "genetics_structured": 0.5,
    # A disease GWAS signal colocalising with a QTL of the target: the same cohort-measured
    # strength as the other structured rows, a separate key only so it is reported apart.
    "genetics_qtl": 0.5,
    "genetics_literature": 0.25,
    # One paper's perturbation outcome in a model of the disease (biology_lens/
    # perturbations.py): read in its own reading, never pooled with human evidence.
    "preclinical_perturbation": 0.15,
}
_KIND_CAP: dict[EvidenceKind, float] = {
    "genetics_literature": 0.6,
    # Two unanimous papers just reach a verdict; one says nothing.
    "preclinical_perturbation": 0.5,
}
# How well a human-genetics literature statement's design identifies which way of
# changing the target helps, as a factor on its weight (``statement_design``). Counting
# every sentence as one vote let a run's many candidate-polymorphism associations (small
# cohorts, linkage to an unknown causal variant, poor replication) outvote or dilute the
# few designed for direction: a Mendelian randomisation is causal; carriers of a variant
# whose change is known from its consequence or curated direction read the direction from
# the variant itself; an association whose variant is typed by its QTL is the weakest.
# ``genome_wide`` is kept for observations of runs before the statement route.
StudyDesign = Literal["causal", "functional", "genome_wide", "association"]
DESIGN_WEIGHTS: dict[StudyDesign, float] = {
    "causal": 1.0,
    "functional": 0.8,
    "genome_wide": 0.6,
    "association": 0.35,
}
_OUT_OF_SCOPE_FACTOR = 0.5  # evidence about a related disorder, not this disease
# A related disorder's Mendelian mechanism: below ``MIN_STRENGTH`` even at full mechanism
# confidence, so it corroborates other evidence and never decides alone.
_RELATED_MECHANISM_FACTOR = 0.35
MIN_STRENGTH = 0.3  # below this, a side has said nothing
_CONFLICT_RATIO = 0.5  # weaker side ≥ half the stronger: conflicting
HUMAN_GENETICS: frozenset[EvidenceKind] = frozenset(
    {"mendelian", "genetics_structured", "genetics_qtl", "genetics_literature"}
)
# Open Targets datasources whose direction annotations are curated disease mechanisms.
_MENDELIAN_SOURCES = frozenset(
    {
        "eva",
        "orphanet",
        "gene2phenotype",
        "clingen",
        "genomics_england",
        "uniprot_variants",
        "uniprot_literature",
    }
)
# Literature claims are many weak votes whose extracted direction is often wrong both
# ways, so they are netted rather than pooled per side: pooling let a handful of
# mis-tagged claims on each side saturate both at the cap and read "conflicting" for
# nearly every gene. The net vote counts against the noise expected from that many
# votes (margin / sqrt(votes)), rising toward the cap. A vote is one in-scope claim of a
# causal design; other designs count as their ``DESIGN_WEIGHTS`` share of a vote. In those
# units one claim, or 10 against 7, says nothing; 4 unanimous claims support; 8 unanimous
# (or 12 against 1) reach the strength that may overrule a lens.
_NETTED_KINDS: frozenset[EvidenceKind] = frozenset(
    {"genetics_literature", "preclinical_perturbation"}
)
_LITERATURE_NOISE_SCALE = 1.5


class DirectionObservation(BaseModel):
    perturbation: Perturbation
    outcome: Outcome
    evidence: EvidenceKind
    in_scope: bool = True
    weight: float  # strength of this observation, scope, design and confidence applied
    source: str  # what to cite: a drug, a curation, a claim id
    detail: str
    design: str = ""  # literature: the study design weighed (``DESIGN_WEIGHTS``)

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
            design = f", {o.design.replace('_', '-')} design" if o.design else ""
            lines.append(f"  - {side} ({o.evidence}{design}{scope}): {o.detail}")
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
    if strong < MIN_STRENGTH:
        verdict = "unknown"
    elif weak >= MIN_STRENGTH and weak >= _CONFLICT_RATIO * strong:
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
    mechanism: dict[str, Any] | None,
    *,
    in_scope: bool,
    disorder: str,
    somatic_indication: bool = False,
) -> list[DirectionObservation]:
    """The Mendelian mechanism of the disease (or of a related disorder of the gene).

    Gain of function causing disease is harm from more activity; loss of function
    causing disease is harm from less activity.

    The mechanism is inferred from the gene's pathogenic-variant spectrum, so it is the
    mechanism of the disorder those variants cause. Direction of effect is specific to a
    disease: another disorder's mechanism transfers only when the two share a causal
    pathway, which code cannot tell, so it may corroborate a direction but never sets one
    alone (``_RELATED_MECHANISM_FACTOR``). For a cancer indication a germline disorder's
    mechanism says nothing about what the tumour depends on and is not counted.
    """
    if not mechanism or mechanism.get("mechanism") not in ("gof", "lof"):
        return []
    if somatic_indication and not in_scope:
        return []
    gof = mechanism["mechanism"] == "gof"
    weight = EVIDENCE_WEIGHTS["mendelian"] * float(mechanism.get("confidence") or 0.0)
    if not in_scope:
        weight *= _RELATED_MECHANISM_FACTOR
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


def from_structured_direction(rows: Iterable[Any]) -> list[DirectionObservation]:
    """Open Targets genetic evidence for the run's disease (or a subtype) annotated with a
    direction on the target (loss or gain of function) and on the trait (risk or
    protection); one observation per datasource and direction.

    Curated Mendelian sources (ClinVar, Orphanet, Gene2Phenotype, ClinGen…) give the
    disease's own mechanism and weigh as Mendelian evidence; burden and credible-set rows
    weigh as structured genetics. Rows of another disease never reach here: the caller
    asks only for the run's scope.
    """
    groups: dict[tuple[str, Perturbation, Outcome], list[Any]] = {}
    for r in rows:
        on_target = str(getattr(r, "direction_on_target", "") or "")
        on_trait = str(getattr(r, "direction_on_trait", "") or "")
        if on_target not in ("LoF", "GoF") or on_trait not in ("risk", "protect"):
            continue
        perturbation: Perturbation = "decrease" if on_target == "LoF" else "increase"
        outcome: Outcome = "benefit" if on_trait == "protect" else "harm"
        groups.setdefault((str(getattr(r, "datasource", "")), perturbation, outcome), []).append(r)
    out: list[DirectionObservation] = []
    for (source, perturbation, outcome), members in sorted(groups.items()):
        kind: EvidenceKind = "mendelian" if source in _MENDELIAN_SOURCES else "genetics_structured"
        diseases = sorted({str(getattr(m, "disease_name", "") or "") for m in members} - {""})
        change = "loss" if perturbation == "decrease" else "gain"
        effect = "protects" if outcome == "benefit" else "raises risk"
        out.append(
            DirectionObservation(
                perturbation=perturbation,
                outcome=outcome,
                evidence=kind,
                in_scope=True,
                weight=EVIDENCE_WEIGHTS[kind],
                source=f"Open Targets {source}",
                detail=(
                    f"{len(members)} {source} row(s): {change} of function {effect} "
                    f"({', '.join(diseases[:3]) or 'the disease'})"
                ),
            )
        )
    return out


# QTL types whose effect is on the amount of the gene's product (expression, protein). An
# sQTL's effect is on the use of one splice junction, so its sign is no change of the target.
_AMOUNT_QTL_TYPES = frozenset({"eqtl", "pqtl"})


def from_qtl_colocalisation(rows: Iterable[Any], gene: str = "") -> list[DirectionObservation]:
    """Disease GWAS signals colocalising with QTLs of the target (Open Targets
    ``get_qtl_colocalisations``), one observation per QTL type (expression or protein).

    Each row's ``beta_ratio_sign`` is positive when the allele raising the target's
    product raises the risk. The type's rows are netted: the mean sign says which way
    (positive: an increase that harms; negative: an increase that helps) and its size
    scales the structured genetics weight, so rows of both signs (tissues disagreeing)
    weaken the observation instead of each sign counting as a full one, and a balanced
    mix says nothing."""
    signs_of: dict[str, list[float]] = {}
    for r in rows:
        study_type = str(getattr(r, "study_type", "") or "").lower()
        sign = float(getattr(r, "beta_ratio_sign", 0.0) or 0.0)
        # A pQTL led by a missense change of the target can be an epitope artefact.
        if study_type == "pqtl" and getattr(r, "lead_missense_in_target", False):
            continue
        if study_type in _AMOUNT_QTL_TYPES and sign:
            signs_of.setdefault(study_type, []).append(sign)
    target = gene or "the target"
    out: list[DirectionObservation] = []
    for study_type, signs in sorted(signs_of.items()):
        net = sum(signs) / len(signs)
        if not net:
            continue
        raising = sum(1 for x in signs if x > 0)
        lowering = len(signs) - raising
        mixed = (
            f" ({raising} raising, {lowering} lowering the risk)" if raising and lowering else ""
        )
        out.append(
            DirectionObservation(
                perturbation="increase",
                outcome="harm" if net > 0 else "benefit",
                evidence="genetics_qtl",
                in_scope=True,
                weight=EVIDENCE_WEIGHTS["genetics_qtl"] * abs(net),
                source=f"Open Targets GWAS–{study_type} colocalisation",
                detail=(
                    f"{len(signs)} strong {study_type} colocalisation(s){mixed}: on balance the "
                    f"allele raising {target} {'raises' if net > 0 else 'lowers'} the risk"
                ),
            )
        )
    return out


_ABBREVIATION = re.compile(r"[A-Z0-9]{2,6}")


# How a statement's study identifies which way of changing the target helps
# (``DESIGN_WEIGHTS``): a Mendelian randomisation is causal; a variant whose change is
# known from its consequence or curated direction reads the direction from the variant
# itself; any other association is the weakest.
def statement_design(statement: TargetStatement) -> StudyDesign:
    if statement.design == "mendelian_randomisation":
        return "causal"
    if statement.effect_source in ("consequence", "curated"):
        return "functional"
    return "association"


def from_statements(
    statements: Iterable[TargetStatement], *, somatic_indication: bool = False
) -> list[DirectionObservation]:
    """Human-genetics statements (services/evidence/statements.py): a variant of the
    target whose effect on it is known from data, and an in-scope disease outcome that
    moved, one observation per paper and variant.

    Carriers whose change lowers the target with less disease, or raises it with more,
    favour lowering it; the reverse favours raising it. In a cancer indication a
    sentence's variant is usually somatic, and the germline carriers' reading does not
    apply: those go to the somatic datasources instead, and none is read here."""
    if somatic_indication:
        return []
    out: list[DirectionObservation] = []
    seen: set[tuple[str, str]] = set()
    for st in statements:
        if (
            st.exposure_kind != "variant"
            or st.effect_on_target not in ("decrease", "increase")
            or st.outcome_kind != "disease"
            or st.outcome_direction not in ("higher", "lower")
            or not st.in_scope
            or st.species not in ("", "9606")
        ):
            continue
        key = (st.source, st.exposure_id.lower())
        if key in seen:
            continue
        seen.add(key)
        design = statement_design(st)
        lower = st.outcome_direction == "lower"
        out.append(
            DirectionObservation(
                perturbation=st.effect_on_target,
                outcome="benefit" if lower else "harm",
                evidence="genetics_literature",
                in_scope=True,
                weight=EVIDENCE_WEIGHTS["genetics_literature"] * DESIGN_WEIGHTS[design],
                source=st.source,
                detail=(
                    f"{st.exposure_text or st.exposure_id}"
                    f"{f' ({st.exposure_id})' if st.exposure_id != st.exposure_text else ''}"
                    f" ({'less' if st.effect_on_target == 'decrease' else 'more'} target, "
                    f"by {st.effect_source}): {'lower' if lower else 'higher'} risk of "
                    f"{st.outcome_text} — {st.text[:120]}"
                ),
                design=design,
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


def label_forms(
    disease_names: Iterable[str] = (),
    parent_names: Iterable[str] = (),
    scope: DiseaseScope | None = None,
) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]]]:
    """(patterns for the disease, patterns for a related condition) a label warning may
    name: the scope's in-scope and multi-word related forms when a scope is given, else
    the names' eponym forms and parents' head forms."""
    if scope is not None:
        return scope.patterns("in_scope"), scope.patterns("related", min_words=2)
    return (
        [p for n in disease_names for p in condition_forms(n)],
        [p for n in parent_names for p in condition_forms(n, parent=True)],
    )


def from_label_warnings(
    labels: Iterable[dict[str, Any]],
    *,
    disease_names: Iterable[str] = (),
    parent_names: Iterable[str] = (),
    scope: DiseaseScope | None = None,
) -> list[DirectionObservation]:
    """Labels of drugs acting on the target that warn the drug can cause or worsen the
    run's disease (by its name or synonyms) or its parent condition — by the run's
    DiseaseScope when given (subtypes, spellings, abbreviations; multi-word related
    concepts at reduced weight).

    ``labels`` are openFDA label ``extra`` dicts: ``target_drug``, ``target_action_type``,
    ``drug_name``, ``warnings``, ``boxed_warning``.
    """
    names, parents = label_forms(disease_names, parent_names, scope)
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
    statements: Iterable[TargetStatement] = (),
    labels: Iterable[dict[str, Any]] = (),
    disease_names: Iterable[str] = (),
    parent_names: Iterable[str] = (),
    gene: str = "",
    somatic_indication: bool = False,
    scope: DiseaseScope | None = None,
    structured_rows: Iterable[Any] = (),
    qtl_rows: Iterable[Any] = (),
) -> DirectionReading:
    """All direction-of-effect observations for the run, aggregated for ``direction``.

    Literature observations come from the run's target statements (`from_statements`);
    ``somatic_indication`` (a cancer) drops a related disorder's germline mechanism (see
    `from_mechanism`) and the statements' germline reading. ``qtl_rows`` are the target's
    GWAS–QTL colocalisations in the run's scope (`from_qtl_colocalisation`)."""
    names = {disease, *disease_names} - {""}
    parents = list(parent_names)
    structured = from_structured_direction(structured_rows)
    # A curated mechanism of this disease outranks the mechanism inferred from the gene's
    # whole variant spectrum, which pools the variants of every disorder of the gene.
    curated = any(o.evidence == "mendelian" for o in structured)
    observations = [
        *(from_precedent(precedent, disease) if precedent is not None else []),
        *structured,
        *from_qtl_colocalisation(qtl_rows, gene),
        *(
            []
            if curated
            else from_mechanism(
                mechanism,
                in_scope=mechanism_in_scope,
                disorder=mechanism_disorder
                or (disease if mechanism_in_scope else "a related disorder"),
                somatic_indication=somatic_indication,
            )
        ),
        *from_statements(statements, somatic_indication=somatic_indication),
        *from_label_warnings(labels, disease_names=names, parent_names=parents, scope=scope),
    ]
    return assess_direction(requested_effect(direction), observations)
