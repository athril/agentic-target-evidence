# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The safety lens's three questions: their answer models, facts and validation.

The single-call safety lens asked a 7B model to weigh twenty rules at once and return the
verdict. The split lens asks three narrow questions instead (instructions in
``skills/safety_q_*.md``) and computes the verdict from the answers
(``aggregate.py``):

* ``liability_resolution`` — has human data settled each candidate on-target liability?
* ``human_harm``           — do the claims report harm in people, in the requested direction?
* ``tissue``               — is the target expressed in the disease cell type?

Each answer is validated again here, in code: the prompt asks for the right thing, but a
status without a citation or an index out of range is corrected whatever the model said.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agents.interpretation._lens_base import claim_matches_lens
from agents.interpretation._lens_questions import LensQuestion
from schemas.evidence import CoreClaim, EvidenceType

_NOTE_CHARS = 200
_EVENT_CHARS = 160

# Structured claims that cannot report harm in people.
_NOT_ABOUT_PEOPLE = frozenset(
    {
        EvidenceType.OMICS,
        EvidenceType.EXPRESSION,
        EvidenceType.CONSTRAINT,
        EvidenceType.FUNCTIONAL_GENOMICS,
    }
)
_EXPRESSION_TYPES = frozenset({EvidenceType.OMICS, EvidenceType.EXPRESSION})


# --------------------------------------------------------------------------- answers


class LiabilityStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int  # 1-based number of the liability in the facts list
    status: Literal["confirmed_in_humans", "resolved_by_human_data", "unresolved"]
    claim_ids: list[str] = []  # required unless status == "unresolved"
    note: str = ""  # ≤ 200 chars


class LiabilityAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    liabilities: list[LiabilityStatus]


class HarmEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str  # ≤ 160 chars
    claim_ids: list[str]
    in_requested_direction: bool


class HarmAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harm_reported: bool
    events: list[HarmEvent] = []


class TissueAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disease_cell_type_expression: Literal["reported", "not_reported", "absent"]
    claim_ids: list[str] = []
    off_target_tissues_of_concern: list[str] = []  # must be tissues named in the facts


SAFETY_QUESTIONS: tuple[LensQuestion, ...] = (
    LensQuestion(
        id="liability_resolution",
        skill="safety_q_liabilities",
        answer_model=LiabilityAnswer,
        # Without a claim nothing can be settled: every liability stays unresolved.
        empty_answer={"liabilities": []},
    ),
    LensQuestion(
        id="human_harm",
        skill="safety_q_human_harm",
        answer_model=HarmAnswer,
        empty_answer={"harm_reported": False, "events": []},
    ),
    LensQuestion(id="tissue", skill="safety_q_tissue", answer_model=TissueAnswer),
)
LIABILITIES, HUMAN_HARM, TISSUE = SAFETY_QUESTIONS


# ------------------------------------------------------------------- claim selection


def liability_claims(claims: list[CoreClaim]) -> list[CoreClaim]:
    """Safety-routed claims, which include the regulatory label claims."""
    return [c for c in claims if claim_matches_lens(c, "safety")]


def harm_claims(claims: list[CoreClaim]) -> list[CoreClaim]:
    """Safety-routed claims that can report harm in people: literature and labels."""
    return [c for c in liability_claims(claims) if c.evidence_type not in _NOT_ABOUT_PEOPLE]


def tissue_claims(claims: list[CoreClaim]) -> list[CoreClaim]:
    return [c for c in claims if c.evidence_type in _EXPRESSION_TYPES]


# ------------------------------------------------------------------------- validation


def normalise_liabilities(
    answer: LiabilityAnswer | dict[str, Any] | None, count: int
) -> tuple[list[LiabilityStatus], list[str]]:
    """Exactly one status per liability 1..``count``, and what was corrected.

    An index out of range is dropped, a repeated one keeps its first status, a missing one
    is ``unresolved``, and a status other than ``unresolved`` with no citation becomes
    ``unresolved``: a liability is only settled by a claim the model pointed to.
    """
    if answer is None:
        return [], []
    parsed = (
        answer if isinstance(answer, LiabilityAnswer) else LiabilityAnswer.model_validate(answer)
    )
    notes: list[str] = []
    by_index: dict[int, LiabilityStatus] = {}
    for s in parsed.liabilities:
        if not 1 <= s.index <= count:
            notes.append(f"liability {s.index} does not exist; ignored")
        elif s.index in by_index:
            notes.append(f"liability {s.index} answered twice; first answer kept")
        else:
            by_index[s.index] = s
    out: list[LiabilityStatus] = []
    for i in range(1, count + 1):
        given = by_index.get(i)
        if given is None:
            notes.append(f"liability {i} not answered; unresolved")
            status = LiabilityStatus(index=i, status="unresolved")
        elif given.status != "unresolved" and not given.claim_ids:
            notes.append(f"liability {i} was {given.status} without a valid citation; unresolved")
            status = given.model_copy(update={"status": "unresolved"})
        else:
            status = given
        out.append(status.model_copy(update={"note": status.note[:_NOTE_CHARS]}))
    return out, notes


def normalise_harm(
    answer: HarmAnswer | dict[str, Any] | None,
) -> tuple[HarmAnswer | None, list[str]]:
    """Only events in the requested direction with a valid citation; ``harm_reported`` is
    recomputed from what remains."""
    if answer is None:
        return None, []
    parsed = answer if isinstance(answer, HarmAnswer) else HarmAnswer.model_validate(answer)
    kept = [
        e.model_copy(update={"description": e.description[:_EVENT_CHARS]})
        for e in parsed.events
        if e.in_requested_direction and e.claim_ids
    ]
    notes = (
        [
            f"{len(parsed.events) - len(kept)} event(s) dropped: not in the requested direction or uncited"
        ]
        if len(kept) != len(parsed.events)
        else []
    )
    return HarmAnswer(harm_reported=bool(kept), events=kept), notes


def normalise_tissue(
    answer: TissueAnswer | dict[str, Any] | None, named_tissues: list[str]
) -> tuple[TissueAnswer | None, list[str]]:
    """Tissues of concern are limited to the tissues the facts name; ``absent`` needs a
    citation (low bulk expression is not absence)."""
    if answer is None:
        return None, []
    parsed = answer if isinstance(answer, TissueAnswer) else TissueAnswer.model_validate(answer)
    notes: list[str] = []
    allowed = {_tissue_key(t): t for t in named_tissues}
    kept = [
        allowed[_tissue_key(t)]
        for t in parsed.off_target_tissues_of_concern
        if _tissue_key(t) in allowed
    ]
    if len(kept) != len(parsed.off_target_tissues_of_concern):
        notes.append("tissues not named in the facts were dropped")
    status = parsed.disease_cell_type_expression
    if status == "absent" and not parsed.claim_ids:
        notes.append("absence reported without a valid citation; not_reported")
        status = "not_reported"
    return (
        TissueAnswer(
            disease_cell_type_expression=status,
            claim_ids=parsed.claim_ids,
            off_target_tissues_of_concern=list(dict.fromkeys(kept)),
        ),
        notes,
    )


def _tissue_key(name: str) -> str:
    return " ".join(str(name).lower().replace("_", " ").split())


# ------------------------------------------------------------------------------ facts


def liability_facts(
    liabilities: list[dict[str, Any]], *, human_exposure: bool, approved: list[str]
) -> str:
    lines = ["Candidate on-target liabilities of the target (numbered):"]
    for i, li in enumerate(liabilities, 1):
        lines.append(
            f"{i}. {li.get('system')} [{li.get('severity')}; {li.get('source')}]: "
            f"{'; '.join(str(e) for e in li.get('evidence') or [])}"
        )
    if not liabilities:
        lines.append("(none)")
    if human_exposure:
        names = f" (approved in this indication: {', '.join(approved)})" if approved else ""
        lines.append(f"\nDrugs acting on the target have been given to people{names}.")
    else:
        lines.append("\nNo drug acting on the target is known to have been given to people.")
    return "\n".join(lines)


def _imbalance_text(label: str, imbalance: dict[str, Any]) -> str:
    beyond = (
        "higher on the drug arms beyond chance"
        if float(imbalance.get("ci_lower") or 0.0) > 1.0
        else "not different beyond chance"
    )
    return (
        f"{label} {imbalance.get('drug_deaths')}/{imbalance.get('drug_at_risk')} on drug vs "
        f"{imbalance.get('comparator_deaths')}/{imbalance.get('comparator_at_risk')} on "
        f"comparator, risk ratio {imbalance.get('risk_ratio')} (95% CI "
        f"{imbalance.get('ci_lower')}–{imbalance.get('ci_upper')}), {beyond}"
    )


def harm_trial_lines(harm_trials: list[dict[str, Any]], limit: int = 6) -> list[str]:
    """Posted deaths and serious adverse events per arm in trials of drugs on the target
    (counts from the registry; the risk ratios are computed from them)."""
    lines: list[str] = []
    for t in harm_trials[:limit]:
        parts = [
            _imbalance_text(label, t[key])
            for key, label in (
                ("death_imbalance", "deaths"),
                ("serious_imbalance", "serious adverse events"),
            )
            if t.get(key)
        ]
        if parts:
            drugs = ", ".join(t.get("drugs") or []) or "drug"
            lines.append(
                f"  • {t.get('nct_id')} ({drugs}, phase {t.get('phase') or '?'}): "
                + "; ".join(parts)
            )
    return lines


def harm_facts(
    direction: str, precedent: dict[str, Any], harm_trials: list[dict[str, Any]] | None = None
) -> str:
    effect = {"decrease": "reduces", "increase": "increases"}.get(
        str(precedent.get("requested") or ""), "changes"
    )
    lines = [f"Requested direction: {direction} (it {effect} the target's activity)."]
    drugs = [(d, "same direction as requested") for d in precedent.get("concordant") or []] + [
        (d, "OPPOSITE direction") for d in precedent.get("discordant") or []
    ]
    if drugs:
        lines.append("Drugs acting on the target in this indication:")
        for d, label in drugs[:8]:
            stage = "approved" if d.get("approved") else f"phase {float(d.get('stage') or 0):g}"
            lines.append(
                f"  • {str(d.get('drug') or '').lower()} — {str(d.get('action_type') or 'action unknown').lower()}"
                f" ({label}) — {stage}"
            )
    stops = [r for d in precedent.get("concordant") or [] for r in d.get("stopped_for_cause") or []]
    if stops:
        lines.append("Trials of same-direction drugs in this indication stopped for cause:")
        lines += [f"  • {' '.join(str(s).split())[:200]}" for s in stops[:5]]
    posted = harm_trial_lines(harm_trials or [])
    if posted:
        lines.append(
            "Deaths and serious adverse events posted by trials of drugs on the target "
            "(any indication; counts per arm, risk ratios computed from them):"
        )
        lines += posted
    return "\n".join(lines)


def tissue_facts(spec: dict[str, Any], caveat: str) -> tuple[str, list[str]]:
    """The expression facts and the tissue names they mention (the only ones the answer may
    name as tissues of concern)."""
    ranks: dict[str, int] = spec.get("gtex_tissue_ranks") or {}
    disease_tissues = list(spec.get("disease_relevant_tissues") or [])
    top = list(spec.get("top_tpm_tissues") or [])
    named = list(dict.fromkeys([*disease_tissues, *top]))
    lines = [f"Disease tissue: {spec.get('disease_tissue') or 'not resolved'}"]
    cells = list(spec.get("disease_cell_types") or [])
    if cells:
        lines.append(f"Disease cell types: {', '.join(cells)}")
    if spec.get("bulk_tpm") is not None:
        rank = ranks.get(str(spec.get("disease_tissue") or ""))
        where = f", rank {rank} of {len(ranks)}" if rank and ranks else ""
        lines.append(f"Bulk TPM in the disease tissue: {float(spec['bulk_tpm']):.2f}{where}")
    if spec.get("hpa_specificity"):
        lines.append(f"HPA specificity: {spec['hpa_specificity']}")
    if top:
        lines.append(f"Tissues with the highest bulk TPM: {', '.join(top)}")
    if spec.get("disease_tissue_expression_note"):
        lines.append(str(spec["disease_tissue_expression_note"]))
    if caveat:
        lines.append(caveat)
    return "\n".join(lines), named
