# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Therapeutic-hypothesis precedent: what drugs acting on the target did in the indication.

A run tests a triple — gene, disease, direction. The lenses read evidence about the gene
and the disease, but nothing checked the third element against the clinic, so the same
evidence supported opposite hypotheses: PCSK9 × myocardial infarction scored PROCEED for
*activation* (76) on the strength of approved PCSK9 *inhibitors*, and IL17A × Crohn's
disease scored as clinically validated on psoriasis trials, while its only Crohn's trial
had stopped for futility.

This module reads the target's known drugs (Open Targets, per indication — see
``mcp_servers.opentargets.tools.get_known_drugs``), keeps the rows for the run's disease
or its ontology descendants, maps each drug's action type to the effect it has on the
target (decrease / increase) and compares that with the requested direction:

* ``validated``  — a drug with the requested effect is approved in the indication.
* ``opposed``    — drugs with the *opposite* effect are approved (or reached phase 3) in
  the indication and none with the requested effect got past phase 1: the clinic says
  the therapeutic direction is the other one.
* ``failed``     — drugs with the requested effect were tested in the indication and
  trials stopped for lack of efficacy or for safety; none is approved and none is in a
  running phase 3.
* ``stalled``    — a requested-effect drug reached phase 3 in the indication, none is
  approved and no phase 3 is still running (late-stage attrition).
* ``late_stage`` — a requested-effect drug is in a running phase 3 in the indication.
* ``in_clinic``  — requested-effect drugs in phase 1–2 in the indication.
* ``untested``   — no drug acting on the target has been tested in the indication.
* ``undetermined`` — the requested direction has no effect class (``modulate``,
  ``unspecified``), so nothing can be compared.

Pure module: no I/O. The workflow supplies the known-drug rows and the disease scope.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel

from services.evidence.disease_scope import in_scope

Effect = Literal["decrease", "increase", "unknown"]
Status = Literal[
    "validated",
    "opposed",
    "failed",
    "stalled",
    "late_stage",
    "in_clinic",
    "untested",
    "undetermined",
]
# Registry statuses of a trial still running.
_ONGOING = frozenset(
    {"RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"}
)

# ChEMBL / Open Targets action types by the effect the drug has on its target's activity.
# "MODULATOR", "BINDING AGENT" and the like do not say which way, so they stay unknown.
_DECREASE = frozenset(
    {
        "INHIBITOR",
        "ANTAGONIST",
        "ALLOSTERIC ANTAGONIST",
        "NEGATIVE ALLOSTERIC MODULATOR",
        "NEGATIVE MODULATOR",
        "INVERSE AGONIST",
        "BLOCKER",
        "CHANNEL BLOCKER",
        "DEGRADER",
        "ANTISENSE INHIBITOR",
        "RNAI INHIBITOR",
        "GENETIC INHIBITOR",
        "DISRUPTING AGENT",
        "SEQUESTERING AGENT",
    }
)
_INCREASE = frozenset(
    {
        "AGONIST",
        "PARTIAL AGONIST",
        "ACTIVATOR",
        "POSITIVE ALLOSTERIC MODULATOR",
        "POSITIVE MODULATOR",
        "OPENER",
        "STABILISER",
        "STABILIZER",
        "EXOGENOUS PROTEIN",
        "EXOGENOUS GENE",
        "RELEASING AGENT",
    }
)
# Requested directions by effect. "modulate"/"unspecified" have none.
_REQUESTED: dict[str, Effect] = {
    "inhibit": "decrease",
    "degrade": "decrease",
    "antagonize": "decrease",
    "knockdown": "decrease",
    "activate": "increase",
    "agonize": "increase",
    "restore": "increase",
}
_SALT_RE = re.compile(
    r"\s+(?:sodium|potassium|calcium|hydrochloride|hcl|mesylate|maleate|citrate|acetate|"
    r"sulfate|phosphate|tartrate|succinate|fumarate|besylate|tosylate|anhydrous)$",
    re.IGNORECASE,
)


def drug_effect(action_type: str | None) -> Effect:
    """The effect a drug's action type has on its target: decrease, increase or unknown."""
    at = " ".join(str(action_type or "").upper().replace("_", " ").split())
    if at in _DECREASE:
        return "decrease"
    if at in _INCREASE:
        return "increase"
    return "unknown"


def requested_effect(direction: str | None) -> Effect:
    """The effect the run's therapeutic direction asks for."""
    return _REQUESTED.get(str(direction or "").strip().lower(), "unknown")


def _drug_key(name: str) -> str:
    return _SALT_RE.sub("", " ".join(str(name).upper().split()))


class DrugInIndication(BaseModel):
    """One drug's best record in the run's indication (its disease or a descendant)."""

    drug: str
    action_type: str
    effect: Effect
    stage: float  # highest stage reached in the indication (4 = approved / phase 4)
    approved: bool  # approved in the indication
    ongoing: bool = False  # the record at that highest stage is a trial still running
    indications: list[str] = []  # the in-scope disease names the drug has records for
    stopped_for_cause: list[str] = []  # in-indication trials stopped for efficacy/safety


class PrecedentReading(BaseModel):
    direction: str
    requested: Effect
    status: Status
    concordant: list[DrugInIndication] = []  # requested effect
    discordant: list[DrugInIndication] = []  # opposite effect
    unclassified: list[DrugInIndication] = []  # action type gives no direction
    # Drugs with the requested effect approved for some other indication only.
    concordant_elsewhere: list[str] = []
    summary: str = ""

    @property
    def concordant_approved(self) -> list[str]:
        return [d.drug for d in self.concordant if d.approved]

    @property
    def discordant_approved(self) -> list[str]:
        return [d.drug for d in self.discordant if d.approved]

    @property
    def failures(self) -> list[str]:
        return [s for d in self.concordant for s in d.stopped_for_cause]


def _in_indication(
    rows: Iterable[dict[str, Any]], scope_ids: frozenset[str] | set[str] | None, disease: str
) -> list[DrugInIndication]:
    best: dict[str, DrugInIndication] = {}
    for r in rows:
        if not in_scope(
            str(r.get("disease_id") or "") or None,
            str(r.get("disease_name") or ""),
            scope_ids=scope_ids,
            disease_name=disease,
        ):
            continue
        name = _drug_key(str(r.get("drug_name") or ""))
        if not name:
            continue
        stage = float(r.get("max_phase") or 0.0)
        # Approved in the indication: an approval report names it, or the drug is approved
        # and its in-indication record is post-marketing (phase 4).
        approved = bool(r.get("approved_in_indication")) or (
            bool(r.get("is_approved")) and stage >= 4.0
        )
        cur = best.get(name)
        if cur is None:
            cur = best[name] = DrugInIndication(
                drug=name,
                action_type=str(r.get("action_type") or ""),
                effect=drug_effect(r.get("action_type")),
                stage=0.0,
                approved=False,
            )
        status = str(r.get("trial_status") or "").upper()
        if stage > cur.stage or (stage == cur.stage and status in _ONGOING):
            cur.ongoing = status in _ONGOING
        cur.stage = max(cur.stage, stage)
        cur.approved = cur.approved or approved
        disease_name = str(r.get("disease_name") or "")
        if disease_name and disease_name not in cur.indications:
            cur.indications.append(disease_name)
        for s in r.get("stopped_for_cause") or []:
            if s not in cur.stopped_for_cause:
                cur.stopped_for_cause.append(str(s))
    return sorted(best.values(), key=lambda d: (-int(d.approved), -d.stage, d.drug))


def _names(drugs: list[DrugInIndication], n: int = 4) -> str:
    shown = ", ".join(d.drug.lower() for d in drugs[:n])
    return shown + (f" and {len(drugs) - n} more" if len(drugs) > n else "")


def _best(drugs: list[DrugInIndication]) -> float:
    return max((d.stage for d in drugs), default=0.0)


def _stage_word(stage: float) -> str:
    if stage >= 4:
        return "approved/phase 4"
    if stage <= 0:
        return "no phase"
    return f"phase {stage:g}"


def assess_precedent(
    known_drugs: Iterable[dict[str, Any]],
    *,
    direction: str,
    disease: str,
    scope_ids: frozenset[str] | set[str] | None = None,
) -> PrecedentReading:
    """Compare the requested direction with what target-acting drugs did in the indication."""
    rows = list(known_drugs)
    want = requested_effect(direction)
    drugs = _in_indication(rows, scope_ids, disease)
    other: Effect = "increase" if want == "decrease" else "decrease"
    concordant = [d for d in drugs if want != "unknown" and d.effect == want]
    discordant = [d for d in drugs if want != "unknown" and d.effect == other]
    unclassified = [d for d in drugs if d.effect == "unknown" or want == "unknown"]
    in_scope_names = {d.drug for d in drugs}
    elsewhere = sorted(
        {
            _drug_key(str(r.get("drug_name") or ""))
            for r in rows
            if r.get("is_approved") and drug_effect(r.get("action_type")) == want
        }
        - in_scope_names
    )

    conc_best, disc_best = _best(concordant), _best(discordant)
    status: Status
    if want == "unknown":
        status = "undetermined"
    elif any(d.approved for d in concordant):
        status = "validated"
    elif (any(d.approved for d in discordant) or disc_best >= 3) and conc_best < 2:
        status = "opposed"
    elif any(d.ongoing and d.stage >= 3 for d in concordant):
        status = "late_stage"
    elif any(d.stopped_for_cause for d in concordant):
        status = "failed"
    elif conc_best >= 3:
        status = "stalled"
    elif conc_best > 0:
        status = "in_clinic"
    else:
        status = "untested"

    reading = PrecedentReading(
        direction=direction,
        requested=want,
        status=status,
        concordant=concordant,
        discordant=discordant,
        unclassified=unclassified,
        concordant_elsewhere=elsewhere,
    )
    return reading.model_copy(update={"summary": _summary(reading, disease)})


def _summary(r: PrecedentReading, disease: str) -> str:
    verb = {"decrease": "reduce", "increase": "increase"}.get(r.requested, "")
    if r.status == "undetermined":
        return (
            f"The direction '{r.direction}' has no defined effect on the target, so it was "
            "not compared with the effect of drugs in the indication."
        )
    if r.status == "validated":
        return (
            f"Drugs that {verb} the target's activity are approved in {disease} "
            f"({_names([d for d in r.concordant if d.approved])}): the requested direction "
            "is clinically validated in this indication."
        )
    if r.status == "opposed":
        d = r.discordant
        return (
            f"Drugs acting in the OPPOSITE direction ({d[0].effect} target activity: "
            f"{_names(d)}; best stage {_stage_word(_best(d))}) are the ones developed in "
            f"{disease}, and no drug that would {verb} it got past phase 1. The clinical and "
            f"approval record supports the opposite direction, so it is evidence AGAINST "
            f"'{r.direction}'."
        )
    if r.status == "failed":
        return (
            f"Drugs that {verb} the target were tested in {disease} "
            f"({_names(r.concordant)}; best stage {_stage_word(_best(r.concordant))}) and "
            f"trials stopped for lack of efficacy or safety: {'; '.join(r.failures[:3])}. "
            "None is approved in this indication."
        )
    if r.status == "stalled":
        return (
            f"Drugs that {verb} the target reached {_stage_word(_best(r.concordant))} in "
            f"{disease} ({_names(r.concordant)}) and none was approved in this indication, "
            "with no phase 3 still running: late-stage attrition, a negative signal for "
            "the hypothesis unless the programmes stopped for reasons unrelated to it."
        )
    if r.status == "late_stage":
        running = [d for d in r.concordant if d.ongoing and d.stage >= 3]
        return (
            f"Drugs that {verb} the target are in running phase 3 trials in {disease} "
            f"({_names(running)}); none is approved in this indication yet."
        )
    if r.status == "in_clinic":
        return (
            f"Drugs that {verb} the target are in early clinical testing in {disease} "
            f"({_names(r.concordant)}; best stage {_stage_word(_best(r.concordant))})."
        )
    elsewhere = (
        f" Drugs with this effect are approved for other indications "
        f"({', '.join(x.lower() for x in r.concordant_elsewhere[:4])}) — evidence that the "
        "target can be drugged this way, not that it works in this disease."
        if r.concordant_elsewhere
        else ""
    )
    return f"No drug acting on the target has been tested in {disease}.{elsewhere}"


def render_precedent_block(r: PrecedentReading) -> str:
    """Prompt block for the lenses: the comparison and the per-drug facts behind it."""
    lines = [
        f"Therapeutic-hypothesis precedent (pre-computed from Open Targets drug records for "
        f"this indication; requested direction '{r.direction}' = {r.requested} target "
        f"activity) — status: {r.status.upper()}.",
        f"  {r.summary}",
    ]
    for label, group in (
        ("same direction as requested", r.concordant),
        ("OPPOSITE direction", r.discordant),
        ("direction not stated by the action type", r.unclassified),
    ):
        for d in group[:6]:
            stops = f"; stopped for cause: {d.stopped_for_cause[0]}" if d.stopped_for_cause else ""
            lines.append(
                f"  • {d.drug.lower()} [{d.action_type.lower() or 'action unknown'}; {label}] — "
                f"{'approved' if d.approved else _stage_word(d.stage)} in "
                f"{', '.join(d.indications[:2])}{stops}"
            )
    if r.status == "opposed":
        lines.append(
            "  Rule: approval or late-stage success of opposite-direction drugs in this "
            "indication is evidence against the requested direction — never count it as "
            "clinical or regulatory support for it."
        )
    elif r.status == "failed":
        lines.append(
            "  Rule: trials of same-direction drugs stopped for lack of efficacy or safety "
            "in this indication are clinical evidence against the hypothesis; trials in "
            "other indications do not offset them."
        )
    elif r.status == "stalled":
        lines.append(
            "  Rule: phase 3 programmes in this indication that ended without approval are "
            "not clinical validation; say so, and do not count them as support."
        )
    if r.status in ("validated", "opposed", "failed", "stalled", "late_stage", "in_clinic"):
        lines.append(
            "  Trials and approvals in OTHER indications show the target can be drugged; "
            "they are not clinical validation in this one."
        )
    return "\n".join(lines)
