# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Programme outcomes: how strongly the failed programmes against a target falsify its mechanism.

The precedent reading (services/evidence/drug_precedent.py) caps the score at 44 when any
same-direction trial in the indication stopped for cause, and at 59 on phase 3 attrition.
That treats five molecules failing with demonstrated target engagement (a secretase
inhibitor that cleared its biomarker and still did not slow the disease) like one
molecule-specific toxicity next to a trial that met its endpoint (a cholesteryl ester
transfer protein inhibitor whose sponsor chose not to file). The first falsifies the
mechanism; the second does not.

This module classifies each distinct same-direction molecule in the indication by what
happened to it, and turns the failures into one strength between 0 and 1:

* ``approved``              — approved in the indication.
* ``ongoing``               — a trial in the indication is still running.
* ``positive_unfiled``      — a phase 3 trial met its primary endpoint and the drug is not
  approved in the indication: a sponsor decision, not evidence against the mechanism.
* ``failed_safety``         — a trial stopped for harm.
* ``failed_efficacy``       — a trial stopped for futility or a negative result.
* ``failed_hard_endpoint``  — a phase 2/3 that met its primary endpoint but posted a
                              non-significant hard-endpoint secondary (survival, major
                              adverse events), or had more deaths on the drug arms beyond
                              chance: an efficacy failure on the endpoint that matters.
* ``discontinued_business`` — every stop was for business, enrolment or design reasons.
* ``stalled_unexplained``   — phase 3 was reached and nothing came of it, with no reason.
* ``early``                 — none of the above.

The stop reasons come from Open Targets (``config/stop_reasons.yaml`` maps its categories
to the classes above); a posted primary-outcome p-value below 0.05 on an in-indication
phase 3 trial of the drug means the endpoint was met. Weights, the engagement factor and
the score gates live in ``config/scoring.yaml`` (``programme_outcomes``).

Two things the registry cannot say are handled conservatively:

* *Target engagement.* Whether a drug that failed for efficacy actually moved a
  pharmacodynamic marker of its target is not in the registry. Until it is established
  (the clinical lens's engagement question), a failure counts with the ``not_assessed``
  factor, not as full falsification.
* *On-target versus off-target harm.* A safety stop may be the mechanism or the molecule.
  Safety failures therefore always use the ``not_reported`` factor, whatever the
  engagement answer says.

Pure module: no I/O beyond reading the two config files. The workflow supplies the
Open Targets known-drug rows, the registry trial facts and the disease scope.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from services.evidence.drug_precedent import (
    Effect,
    drug_effect,
    drug_key,
    is_ongoing,
    requested_effect,
    row_approved_in_indication,
    row_in_indication,
)

OutcomeClass = Literal[
    "approved",
    "ongoing",
    "failed_efficacy",
    "failed_hard_endpoint",
    "failed_safety",
    "discontinued_business",
    "positive_unfiled",
    "stalled_unexplained",
    "early",
]
Engagement = Literal["shown", "not_shown", "not_reported", "not_assessed"]
StopClass = Literal["efficacy", "safety", "business", "enrolment", "design", "unknown"]

_SCORING_PATH = Path("config/scoring.yaml")
_STOP_REASONS_PATH = Path("config/stop_reasons.yaml")
# Registry statuses of a trial that ended before completion.
_STOPPED = frozenset({"TERMINATED", "WITHDRAWN", "SUSPENDED"})
# Stop classes that say nothing about the mechanism.
_NEUTRAL_STOPS: frozenset[StopClass] = frozenset({"business", "enrolment", "design"})
_SIGNIFICANT = 0.05  # a posted primary-outcome p-value below this means the endpoint was met
_MAX_TRIALS = 6
_MAX_REASONS = 3
_COUNTED = ("failed_efficacy", "failed_hard_endpoint", "failed_safety", "stalled_unexplained")
# Efficacy failures, whose weight depends on whether the drug engaged the target.
ENGAGEMENT_ASKED = ("failed_efficacy", "failed_hard_endpoint")

_DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "failed_efficacy": {"phase3": 0.5, "phase2": 0.3, "phase1": 0.1},
    "failed_hard_endpoint": {"phase3": 0.5, "phase2": 0.3},
    "failed_safety": {"phase3": 0.35, "phase2": 0.25, "phase1": 0.1},
    "stalled_unexplained": {"phase3": 0.3},
}
_DEFAULT_ENGAGEMENT: dict[str, float] = {
    "shown": 1.0,
    "not_reported": 0.6,
    "not_assessed": 0.6,
    "not_shown": 0.3,
}


class ProgrammeGate(BaseModel):
    """Cap the score at ``max`` when the falsification strength reaches ``min_strength``."""

    min_strength: float
    max: float
    reason: str


DEFAULT_GATES = [
    ProgrammeGate(min_strength=0.6, max=44, reason="the mechanism failed in several programmes"),
    ProgrammeGate(
        min_strength=0.3, max=59, reason="programmes against the target stalled or failed"
    ),
]


class ProgrammeConfig(BaseModel):
    weights: dict[str, dict[str, float]] = {k: dict(v) for k, v in _DEFAULT_WEIGHTS.items()}
    engagement_factor: dict[str, float] = dict(_DEFAULT_ENGAGEMENT)
    positive_unfiled_relief: float = 0.3
    # A same-direction molecule approved in the indication shows the mechanism works there;
    # the other molecules' failures are their own (exposure, selectivity, toxicity, design).
    approved_relief: float = 1.0
    gates: list[ProgrammeGate] = list(DEFAULT_GATES)
    # Open Targets stop-reason category → StopClass (config/stop_reasons.yaml).
    stop_classes: dict[str, str] = {}


def load_programme_config(
    scoring_path: Path | None = None, stop_reasons_path: Path | None = None
) -> ProgrammeConfig:
    """The ``programme_outcomes`` block of config/scoring.yaml and the stop-reason classes."""
    scoring = _read_yaml(scoring_path or _SCORING_PATH).get("programme_outcomes") or {}
    classes = _read_yaml(stop_reasons_path or _STOP_REASONS_PATH).get("classes") or {}
    base = ProgrammeConfig()
    gates = [
        ProgrammeGate(
            min_strength=float(g["min_strength"]),
            max=float(g["max"]),
            reason=str(g.get("reason") or ""),
        )
        for g in scoring.get("gates") or []
    ]
    return base.model_copy(
        update={
            "weights": {
                **base.weights,
                **{
                    str(k): {str(p): float(w) for p, w in (v or {}).items()}
                    for k, v in (scoring.get("weights") or {}).items()
                },
            },
            "engagement_factor": {
                **base.engagement_factor,
                **{str(k): float(v) for k, v in (scoring.get("engagement_factor") or {}).items()},
            },
            "positive_unfiled_relief": float(
                scoring.get("positive_unfiled_relief", base.positive_unfiled_relief)
            ),
            "approved_relief": float(scoring.get("approved_relief", base.approved_relief)),
            "gates": gates or base.gates,
            "stop_classes": {str(k): str(v) for k, v in classes.items()},
        }
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def select_gate(strength: float, gates: Iterable[ProgrammeGate]) -> ProgrammeGate | None:
    """The gate with the highest ``min_strength`` that ``strength`` reaches, if any."""
    reached = [g for g in gates if strength >= g.min_strength]
    return max(reached, key=lambda g: g.min_strength) if reached else None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Programme(BaseModel):
    """One molecule's record in the indication."""

    drug: str  # drug_key(name)
    effect: Effect
    stage: float  # best stage in the indication
    outcome: OutcomeClass
    trials: list[str] = []  # in-indication trial ids behind the outcome
    reasons: list[str] = []  # stop reasons, quoted
    failure_stage: float = 0.0  # stage of the trial that failed or stalled (0 when none)
    engagement: Engagement = "not_assessed"
    engagement_claim_ids: list[str] = []
    contribution: float = 0.0  # weight × engagement factor, what it adds to the strength


class ProgrammeReading(BaseModel):
    requested: Effect
    # Same-direction programmes in the indication; opposite-direction ones are the
    # precedent reading's concern.
    programmes: list[Programme] = []
    falsification: float = 0.0  # 0–1
    # False when the known-drug rows predate stop reports (a cached Open Targets row): the
    # caller must not gate on a strength computed from nothing.
    available: bool = True
    gate: ProgrammeGate | None = None
    summary: str = ""

    @property
    def failed(self) -> list[Programme]:
        return [p for p in self.programmes if p.outcome in _COUNTED]


def noisy_or(values: Iterable[float]) -> float:
    """1 − ∏(1 − v): independent pieces of evidence that each partly falsify."""
    miss = 1.0
    for v in values:
        miss *= 1.0 - max(0.0, min(1.0, v))
    return 1.0 - miss


def _phase_key(stage: float) -> str | None:
    # PHASE_2_3 (2.5) counts as phase 2 and PHASE_3_4 (3.5) as phase 3: a seamless
    # phase 2/3 stopped at its interim is weaker evidence than a full phase 3.
    if stage >= 3:
        return "phase3"
    if stage >= 2:
        return "phase2"
    if stage >= 1:
        return "phase1"
    return None


def programme_contribution(p: Programme, cfg: ProgrammeConfig) -> float:
    """What one failed or stalled programme adds to the strength before combining."""
    if p.outcome not in _COUNTED:
        return 0.0
    phase = _phase_key(p.failure_stage)
    weight = cfg.weights.get(p.outcome, {}).get(phase, 0.0) if phase else 0.0
    # On-target and off-target harm cannot be told apart from the registry.
    engagement = "not_reported" if p.outcome == "failed_safety" else p.engagement
    factor = cfg.engagement_factor.get(engagement, cfg.engagement_factor["not_assessed"])
    return weight * factor


def falsification_strength(programmes: Iterable[Programme], cfg: ProgrammeConfig) -> float:
    """How strongly the programmes falsify the mechanism (see module doc and scoring.yaml)."""
    items = list(programmes)
    failures = noisy_or(programme_contribution(p, cfg) for p in items)
    relief = noisy_or(
        cfg.positive_unfiled_relief
        if p.outcome == "positive_unfiled"
        else cfg.approved_relief
        if p.outcome == "approved"
        else 0.0
        for p in items
    )
    return round(failures * (1.0 - relief), 4)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _is_stopped(report: Mapping[str, Any]) -> bool:
    return bool(report.get("categories")) or str(report.get("status") or "").upper() in _STOPPED


def report_classes(
    report: Mapping[str, Any], stop_classes: Mapping[str, str]
) -> frozenset[StopClass]:
    """The stop classes of one clinical report; empty when the trial was not stopped.

    A category outside the configured vocabulary reads as ``unknown``, so a new category
    never counts as a failure before somebody classifies it.
    """
    categories = [str(c) for c in report.get("categories") or []]
    if categories:
        return frozenset(_stop_class(stop_classes.get(c)) for c in categories)
    return frozenset({"unknown"}) if _is_stopped(report) else frozenset()


def _stop_class(value: str | None) -> StopClass:
    known: dict[str, StopClass] = {
        "efficacy": "efficacy",
        "safety": "safety",
        "business": "business",
        "enrolment": "enrolment",
        "design": "design",
    }
    return known.get(str(value), "unknown")


@dataclass
class _Record:
    effect: Effect
    stage: float = 0.0
    approved: bool = False
    row_running: bool = False
    reports: dict[tuple[str, float, str], dict[str, Any]] = field(default_factory=dict)


def _names_drug(fact: Mapping[str, Any], key: str) -> bool:
    by = str(fact.get("matched_by") or "")
    if by.startswith("drug:") and drug_key(by[5:]) == key:
        return True
    pattern = re.compile(rf"\b{re.escape(key)}\b", re.IGNORECASE)
    return any(pattern.search(str(i)) for i in fact.get("interventions") or [])


def _measured_indication(fact: Mapping[str, Any]) -> bool:
    """In the indication, or relevant to it (an outcome trial registered under a broader
    condition whose primary outcome counts the indication's events)."""
    return fact.get("in_indication") is True or fact.get("relevant_to_indication") is True


def _endpoint_met(trial_facts: Iterable[Mapping[str, Any]], key: str) -> list[str]:
    """Ids of in-indication phase 3 trials of the drug with a posted primary-outcome
    p-value below 0.05.

    The p-value is the experimental arm against its comparator, but the registry gives no
    sign: a significant harm would pass. In practice a phase 3 that posts such a result
    and is not approved is a sponsor decision, and the lens narrative reads the trial.
    """
    ids: list[str] = []
    for f in trial_facts:
        if not _measured_indication(f):
            continue
        if max(f.get("phase_numbers") or [0]) < 3 or not _names_drug(f, key):
            continue
        if any(float(a.get("p_value", 1.0)) < _SIGNIFICANT for a in f.get("arm_results") or []):
            ids.append(str(f.get("nct_id") or ""))
    return [i for i in dict.fromkeys(ids) if i]


def _hard_endpoint_failures(
    trial_facts: Iterable[Mapping[str, Any]], key: str
) -> list[Mapping[str, Any]]:
    """In-indication phase 2/3 trials of the drug that failed where it matters: a
    surrogate primary met (a posted p < 0.05) with a hard-endpoint secondary not met, or
    significantly more deaths on the drug arms. A primary that is itself a hard endpoint
    (major coronary events) is no surrogate: another hard secondary missing beside it is
    not a failure."""
    out: list[Mapping[str, Any]] = []
    for f in trial_facts:
        if not _measured_indication(f):
            continue
        if max(f.get("phase_numbers") or [0]) < 2 or not _names_drug(f, key):
            continue
        primary_met = any(
            float(a.get("p_value", 1.0)) < _SIGNIFICANT for a in f.get("arm_results") or []
        )
        hard_missed = any(h.get("not_met") is True for h in f.get("hard_endpoints") or [])
        deaths = f.get("death_imbalance") or {}
        excess_deaths = float(deaths.get("ci_lower") or 0.0) > 1.0
        surrogate_win = primary_met and not f.get("primary_is_hard")
        if (surrogate_win and hard_missed) or excess_deaths:
            out.append(f)
    return out


def _endpoint_missed(trial_facts: Iterable[Mapping[str, Any]], key: str) -> list[Mapping[str, Any]]:
    """Phase 2/3 trials of the drug that measured the indication and posted primary
    analyses against a comparator, none of them significant: the endpoint was missed."""
    out: list[Mapping[str, Any]] = []
    for f in trial_facts:
        if not _measured_indication(f) or max(f.get("phase_numbers") or [0]) < 2:
            continue
        if not _names_drug(f, key):
            continue
        arms = f.get("arm_results") or []
        if arms and all(float(a.get("p_value", 0.0)) >= _SIGNIFICANT for a in arms):
            out.append(f)
    return out


def _registry_records(
    known_drugs: Iterable[dict[str, Any]],
    trial_facts: Iterable[Mapping[str, Any]],
    present: Mapping[str, _Record],
) -> dict[str, _Record]:
    """Molecules of the target with a registry trial that measured the indication but no
    Open Targets row filed under it (an outcome trial registered under a broader
    condition): a programme in the indication all the same, staged by its trials."""
    effects: dict[str, Effect] = {}
    for r in known_drugs:
        name = drug_key(str(r.get("drug_name") or ""))
        if name:
            effects.setdefault(name, drug_effect(r.get("action_type")))
    out: dict[str, _Record] = {}
    for f in trial_facts:
        by = str(f.get("matched_by") or "")
        if not by.startswith("drug:") or not _measured_indication(f):
            continue
        name = drug_key(by[5:])
        if not name or name in present or name not in effects:
            continue
        rec = out.setdefault(name, _Record(effect=effects[name]))
        stage = float(max(f.get("phase_numbers") or [0]))
        rec.stage = max(rec.stage, stage)
        tid = str(f.get("nct_id") or "")
        rec.reports.setdefault(
            (tid, stage, str(f.get("status") or "")),
            {
                "trial_id": tid,
                "stage": stage,
                "status": str(f.get("status") or ""),
                "categories": [],
            },
        )
    return out


def _hard_endpoint_reason(fact: Mapping[str, Any]) -> str:
    parts = [
        f"{h.get('measure')} not met ({h.get('estimate') or 'p=' + str(h.get('p_text'))})"
        for h in fact.get("hard_endpoints") or []
        if h.get("not_met") is True
    ]
    deaths = fact.get("death_imbalance") or {}
    if float(deaths.get("ci_lower") or 0.0) > 1.0:
        parts.append(
            f"deaths {deaths.get('drug_deaths')}/{deaths.get('drug_at_risk')} on drug vs "
            f"{deaths.get('comparator_deaths')}/{deaths.get('comparator_at_risk')} on comparator "
            f"(RR {deaths.get('risk_ratio')}, 95% CI {deaths.get('ci_lower')}–{deaths.get('ci_upper')})"
        )
    return f"{fact.get('nct_id')}: " + "; ".join(parts)


def _collect(
    rows: Iterable[dict[str, Any]],
) -> dict[str, _Record]:
    records: dict[str, _Record] = {}
    for r in rows:
        name = drug_key(str(r.get("drug_name") or ""))
        if not name:
            continue
        rec = records.setdefault(name, _Record(effect=drug_effect(r.get("action_type"))))
        rec.stage = max(rec.stage, float(r.get("max_phase") or 0.0))
        rec.approved = rec.approved or row_approved_in_indication(r)
        reports = r.get("stop_reports") or []
        if not reports and is_ongoing(r.get("trial_status")):
            rec.row_running = True
        for rep in reports:
            tid = str(rep.get("trial_id") or "")
            key = (tid, float(rep.get("stage") or 0.0), str(rep.get("status") or ""))
            rec.reports.setdefault(key if tid else (str(id(rep)), 0.0, ""), dict(rep))
    return records


def _classify_record(
    key: str,
    rec: _Record,
    trial_facts: list[Mapping[str, Any]],
    stop_classes: Mapping[str, str],
) -> Programme:
    reports = list(rec.reports.values())
    classed = [(rep, report_classes(rep, stop_classes)) for rep in reports]

    def ids(items: Iterable[Mapping[str, Any]]) -> list[str]:
        return list(dict.fromkeys(str(i.get("trial_id")) for i in items if i.get("trial_id")))[
            :_MAX_TRIALS
        ]

    def why(items: Iterable[Mapping[str, Any]]) -> list[str]:
        return list(dict.fromkeys(str(i.get("why")) for i in items if i.get("why")))[:_MAX_REASONS]

    def make(outcome: OutcomeClass, picked: Sequence[Mapping[str, Any]], **kw: Any) -> Programme:
        return Programme(
            drug=key,
            effect=rec.effect,
            stage=rec.stage,
            outcome=outcome,
            trials=kw.pop("trials", None) or ids(picked),
            reasons=why(picked),
            **kw,
        )

    if rec.approved:
        return make("approved", [r for r in reports if float(r.get("stage") or 0) >= 4])
    running = [r for r in reports if is_ongoing(r.get("status"))]
    if running or rec.row_running:
        return make("ongoing", running)
    # Before the met-endpoint check: a surrogate win with a hard-endpoint loss is a failure.
    hard = _hard_endpoint_failures(trial_facts, key)
    if hard:
        return Programme(
            drug=key,
            effect=rec.effect,
            stage=rec.stage,
            outcome="failed_hard_endpoint",
            trials=[str(f.get("nct_id")) for f in hard][:_MAX_TRIALS],
            reasons=[_hard_endpoint_reason(f) for f in hard][:_MAX_REASONS],
            failure_stage=float(max(max(f.get("phase_numbers") or [0]) for f in hard)),
        )
    met = _endpoint_met(trial_facts, key)
    if met:
        return make("positive_unfiled", [], trials=met[:_MAX_TRIALS])
    for outcome, wanted in (("failed_safety", "safety"), ("failed_efficacy", "efficacy")):
        hit = [rep for rep, cls in classed if wanted in cls]
        if hit:
            return make(
                outcome,  # type: ignore[arg-type]
                hit,
                failure_stage=max(float(r.get("stage") or 0.0) for r in hit),
            )
    # A posted primary analysis that missed is an efficacy failure whatever the stop
    # reason on record (often none, or "completed").
    missed = _endpoint_missed(trial_facts, key)
    if missed:
        return Programme(
            drug=key,
            effect=rec.effect,
            stage=rec.stage,
            outcome="failed_efficacy",
            trials=[str(f.get("nct_id")) for f in missed][:_MAX_TRIALS],
            reasons=[
                f"{f.get('nct_id')}: primary endpoint not met (posted analysis)" for f in missed
            ][:_MAX_REASONS],
            failure_stage=float(max(max(f.get("phase_numbers") or [0]) for f in missed)),
        )
    stopped = [(rep, cls) for rep, cls in classed if cls]
    if stopped and all(cls <= _NEUTRAL_STOPS for _, cls in stopped):
        return make("discontinued_business", [rep for rep, _ in stopped])
    if rec.stage >= 3:
        # Phase 3 reached, nothing approved, nothing running, no reason on record. The
        # trial need not be "completed": the registry leaves most old terminated phase 2/3
        # trials without a reason (a secretase inhibitor's, stopped for futility), and
        # requiring completion made them read as early-stage.
        ended = [
            rep
            for rep in reports
            if float(rep.get("stage") or 0.0) >= 2.5 and not is_ongoing(rep.get("status"))
        ]
        return make("stalled_unexplained", ended, failure_stage=rec.stage)
    return make("early", reports)


def classify_programmes(
    known_drugs: Iterable[dict[str, Any]],
    trial_facts: Iterable[Mapping[str, Any]],
    *,
    direction: str,
    disease: str,
    scope_ids: frozenset[str] | set[str] | None = None,
    config: ProgrammeConfig | None = None,
) -> ProgrammeReading:
    """Classify each same-direction molecule in the indication and score the falsification."""
    cfg = config or load_programme_config()
    want = requested_effect(direction)
    known_drugs = list(known_drugs)
    rows = [r for r in known_drugs if row_in_indication(r, scope_ids, disease)]
    available = all("stop_reports" in r for r in rows)
    if want == "unknown":
        return ProgrammeReading(
            requested=want,
            available=available,
            summary=(
                f"The direction '{direction}' has no defined effect on the target, so the "
                "programmes in the indication were not classified."
            ),
        )
    facts = list(trial_facts)
    records = _collect(rows)
    records.update(_registry_records(known_drugs, facts, records))
    programmes = [
        _classify_record(key, rec, facts, cfg.stop_classes)
        for key, rec in sorted(records.items())
        if rec.effect == want
    ]
    programmes.sort(key=lambda p: (-p.stage, p.drug))
    return with_strength(
        ProgrammeReading(requested=want, programmes=programmes, available=available),
        cfg,
        disease,
    )


def with_strength(
    reading: ProgrammeReading, cfg: ProgrammeConfig, disease: str
) -> ProgrammeReading:
    """Recompute contributions, strength, gate and summary (after engagement is filled in)."""
    programmes = [
        p.model_copy(update={"contribution": round(programme_contribution(p, cfg), 4)})
        for p in reading.programmes
    ]
    strength = falsification_strength(programmes, cfg)
    updated = reading.model_copy(
        update={
            "programmes": programmes,
            "falsification": strength,
            "gate": select_gate(strength, cfg.gates),
        }
    )
    return updated.model_copy(update={"summary": _summary(updated, disease)})


def apply_engagement(
    reading: ProgrammeReading,
    answers: Mapping[str, tuple[Engagement, Sequence[str]]],
    cfg: ProgrammeConfig,
    disease: str,
) -> ProgrammeReading:
    """Fill each answered programme's target-engagement and recompute the strength.

    ``answers`` is keyed by the programme's drug. Only an efficacy failure takes an
    answer: a safety failure always uses the unreported factor, and a programme that did
    not fail has nothing for engagement to discount.
    """
    updated = [
        p.model_copy(
            update={
                "engagement": answers[p.drug][0],
                "engagement_claim_ids": list(answers[p.drug][1]),
            }
        )
        if p.outcome in ENGAGEMENT_ASKED and p.drug in answers
        else p
        for p in reading.programmes
    ]
    return with_strength(reading.model_copy(update={"programmes": updated}), cfg, disease)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

_OUTCOME_WORDS: dict[str, str] = {
    "approved": "approved",
    "ongoing": "still in trials",
    "failed_efficacy": "failed for lack of efficacy",
    "failed_hard_endpoint": "met a surrogate endpoint but failed a hard endpoint or had excess deaths",
    "failed_safety": "stopped for safety",
    "discontinued_business": "discontinued for business, enrolment or design reasons",
    "positive_unfiled": "met a phase 3 primary endpoint but is not approved",
    "stalled_unexplained": "reached phase 3 and ended with no result or reason on record",
    "early": "only early-stage or inconclusive records",
}


def _names(programmes: list[Programme], n: int = 4) -> str:
    shown = ", ".join(p.drug.lower() for p in programmes[:n])
    return shown + (f" and {len(programmes) - n} more" if len(programmes) > n else "")


def _summary(r: ProgrammeReading, disease: str) -> str:
    if not r.available:
        return (
            f"The programmes in {disease} were not classified: the Open Targets rows carry "
            "no stop reports."
        )
    if not r.programmes:
        return f"No drug with the requested effect on the target has been tested in {disease}."
    parts = []
    for outcome, word in _OUTCOME_WORDS.items():
        group = [p for p in r.programmes if p.outcome == outcome]
        if group:
            parts.append(f"{len(group)} {word} ({_names(group)})")
    tail = f"Falsification of the mechanism: {r.falsification:.2f}"
    tail += f" — {r.gate.reason}." if r.gate else " — too weak to bound the score."
    return f"Same-direction programmes in {disease}: " + "; ".join(parts) + f". {tail}"


def _stage_word(stage: float) -> str:
    if stage >= 4:
        return "approved/phase 4"
    return f"phase {stage:g}" if stage > 0 else "no phase"


def render_programme_block(r: ProgrammeReading) -> str:
    """Prompt block for the clinical lens: one line per programme, from code."""
    if not r.available or not r.programmes:
        return ""
    lines = [
        "Programme outcomes in this indication (pre-computed from Open Targets clinical "
        "reports and registry results; one line per distinct molecule with the requested "
        f"effect) — falsification strength {r.falsification:.2f} of 1.",
        f"  {r.summary}",
    ]
    for p in r.programmes[:10]:
        detail = (
            f"{p.drug.lower()} — best stage {_stage_word(p.stage)}; {_OUTCOME_WORDS[p.outcome]}"
        )
        if p.reasons:
            detail += f"; reason on record: {p.reasons[0]}"
        if p.trials:
            detail += f" [{', '.join(p.trials[:3])}]"
        if p.outcome in ENGAGEMENT_ASKED:
            detail += f"; target engagement: {p.engagement.replace('_', ' ')}"
        lines.append(f"  • {detail}")
    lines.append(
        "  Rule: a programme stopped for business, enrolment or design reasons, or one that "
        "met its primary endpoint and was not filed, is not evidence against the mechanism; "
        "never call it a failure. Only programmes that failed for efficacy (including a "
        "hard endpoint) or safety, or reached phase 3 and ended without a result, count "
        "against it."
    )
    return "\n".join(lines)
