# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The safety verdict, computed from the answers to the three questions.

Pure code: no model call. Each rule names the failure it prevents.

Toxicity axis (``True`` = acceptable safety profile), first match wins:

=============================================================  =========
Condition                                                       Verdict
=============================================================  =========
the evidence says the requested change worsens the disease, or  False
a high-severity liability is confirmed in people, or harm is
reported in people in the requested direction (none of these
counts when a same-direction drug is approved in the indication)
no human exposure and two or more high-severity liabilities     False
are unresolved
exactly one high-severity liability is unresolved, or no human  None
exposure and two or more medium liabilities are unresolved, or
an answer is missing, or no source covers the harms of the
requested change (no liability tagged with it and no people
exposed to same-direction drugs: absence is not safety)
otherwise                                                       True
=============================================================  =========

Two refinements of an earlier version of this table, each from a case it got wrong:

* *Medium liabilities are never grounds for ``False`` on their own.* That version opposed any
  two unresolved high-or-medium liabilities without human exposure; a target with no drug
  in people and two mouse-knockout organ systems (a liver enzyme, expected Support or
  Neutral) came out Oppose, because a mouse phenotype names a system to monitor, not a
  harm. Unresolved medium liabilities lower the verdict to uncertain, and the confidence
  cap of the unexposed-liabilities rule (``_lens_dependency.cap_unexposed_toxicity``)
  still applies afterwards.
* *An approved same-direction drug settles the human safety of the mechanism.* That
  version opposed on any harm confirmed in people; an antibody class approved in the very
  indication, whose label warns of a manageable skin reaction, came out Oppose. When a
  drug acting in the requested direction is approved in the indication, harm confirmed in
  the people who took it is a labelled, managed risk, not a reason to oppose the target
  (the safety skill already says to oppose only for a liability the clinical record shows
  is limiting). Those liabilities count as settled.

Tissue-specificity axis: ``False`` when a cited claim reports the target absent from the
disease cell type or tissue; ``True`` when expression there is reported, or the disease
tissue is among the gene's five highest-expressing tissues, or HPA names the gene
tissue-enriched (its category does not say which tissue; the safety skill treats it as
cell-type enrichment that bulk data dilute); otherwise ``None``.

Overall: toxicity ``False`` → oppose; toxicity ``True`` and tissue not ``False`` → support;
no answer at all → insufficient evidence; otherwise neutral. A neutral verdict always has
an uncertain axis or axes that disagree, so ``align_neutral_with_axes`` leaves it alone.

Confidences stay 0.0 here: ``score_verdict`` computes them from the cited claims.
"""

from __future__ import annotations

from typing import Any, Literal

from agents.interpretation._lens_confidence import NON_DIRECTIONAL_CAP
from agents.interpretation.safety_lens.questions import (
    HarmAnswer,
    LiabilityAnswer,
    LiabilityStatus,
    TissueAnswer,
    normalise_liabilities,
)
from schemas.verdicts import AxisVerdict
from services.evidence.drug_precedent import requested_effect
from services.evidence.organ_systems import SYSTEMS, prose_names_system

# 2: verdict rules run before the narrative; 3: liabilities settled by approved labels;
# 4: a favourable toxicity axis reached by absence is capped as a non-finding.
# 5: an unassessed direction of change is uncertain, not favourable.
# 6: a finding over several organ systems is settled when a label names any of them.
AGGREGATION_VERSION = "6"
TOP_TISSUE_RANK = 5

Overall = Literal["support", "oppose", "neutral", "insufficient_evidence"]


def _systems(items: list[dict[str, Any]]) -> str:
    return ", ".join(str(li.get("system")) for li in items[:4]) + (
        f" and {len(items) - 4} more" if len(items) > 4 else ""
    )


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _toxicity(
    liabilities: list[dict[str, Any]],
    liability_answer: LiabilityAnswer | None,
    harm: HarmAnswer | None,
    *,
    human_exposure: bool,
    direction_harms: bool,
    approved_here: bool,
    assessed: bool = True,
    direction: str = "",
) -> AxisVerdict:
    statuses: list[LiabilityStatus] = (
        normalise_liabilities(liability_answer, len(liabilities))[0]
        if liability_answer is not None
        else []
    )
    status_of = {s.index: s for s in statuses}

    def with_status(wanted: str, severities: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            li
            for i, li in enumerate(liabilities, 1)
            if li.get("severity") in severities
            and status_of.get(i) is not None
            and status_of[i].status == wanted
        ]

    confirmed_high = with_status("confirmed_in_humans", ("high",))
    open_high = with_status("unresolved", ("high",))
    open_medium = with_status("unresolved", ("medium",))
    harm_events = list(harm.events) if harm is not None and harm.harm_reported else []

    answered_ids = [cid for s in statuses if s.status != "unresolved" for cid in s.claim_ids] + [
        cid for e in harm_events for cid in e.claim_ids
    ]
    claim_ids = _dedupe(answered_ids)

    verdict: bool | None
    cap: float | None = None
    if direction_harms and not approved_here:
        verdict = False
        why = (
            "Evidence in this run says the requested change in the target's activity "
            "worsens the disease."
        )
    elif confirmed_high and not approved_here:
        verdict = False
        why = f"High-severity liability confirmed in people: {_systems(confirmed_high)}."
    elif harm_events and not approved_here:
        verdict = False
        why = f"Harm in people in the requested direction: {harm_events[0].description}"
    elif not human_exposure and len(open_high) >= 2:
        verdict = False
        why = (
            f"No drug acting on the target has been given to people and {len(open_high)} "
            f"high-severity liabilities have no human data: {_systems(open_high)}."
        )
    elif liability_answer is None or harm is None:
        verdict = None
        why = "The liability or human-harm question could not be answered."
    elif len(open_high) == 1:
        verdict = None
        why = f"One high-severity liability has no human data: {_systems(open_high)}."
    elif not human_exposure and len(open_medium) >= 2:
        verdict = None
        why = (
            f"No drug acting on the target has been given to people and {len(open_medium)} "
            f"medium-severity liabilities have no human data: {_systems(open_medium)}."
        )
    elif not assessed:
        verdict = None
        change = "raising" if requested_effect(direction) == "increase" else "lowering"
        why = f"Harms of {change} the target are not assessed: no source covers that direction."
    else:
        verdict = True
        settled = sum(1 for s in statuses if s.status != "unresolved")
        why = (
            f"No high-severity liability is open and no harm in people was reported "
            f"({settled} of {len(liabilities)} candidate liabilities settled by human data)."
        )
        # Favourable by absence: nothing was settled by human data and nobody has been
        # exposed, so the axis is a non-finding however many records are attached to it.
        if not human_exposure and not any(s.status == "resolved_by_human_data" for s in statuses):
            cap = NON_DIRECTIONAL_CAP
    return AxisVerdict(
        axis="toxicity",
        verdict=verdict,
        rationale=why,
        supporting_claim_ids=claim_ids,
        confidence_cap=cap,
    )


def _tissue(
    tissue: TissueAnswer | None, *, disease_tissue_rank: int | None, hpa_enriched: bool
) -> AxisVerdict:
    claim_ids = list(tissue.claim_ids) if tissue is not None else []
    verdict: bool | None
    if tissue is not None and tissue.disease_cell_type_expression == "absent" and claim_ids:
        verdict, why = False, "A cited claim reports the target absent from the disease cell type."
    elif tissue is not None and tissue.disease_cell_type_expression == "reported":
        verdict, why = True, "Expression in the disease cell type or tissue is reported."
    elif disease_tissue_rank is not None and disease_tissue_rank <= TOP_TISSUE_RANK:
        verdict = True
        why = f"The disease tissue is among the gene's top {TOP_TISSUE_RANK} tissues (rank {disease_tissue_rank})."
    elif hpa_enriched:
        verdict, why = True, "HPA reports the gene tissue-enriched."
    else:
        verdict = None
        why = (
            "Expression in the disease cell type is not reported; bulk expression cannot "
            "resolve a minor cell type."
        )
    return AxisVerdict(
        axis="tissue_specificity",
        verdict=verdict,
        rationale=why,
        supporting_claim_ids=_dedupe(claim_ids),
    )


def settle_by_labels(
    liabilities: list[dict[str, Any]],
    statuses: list[LiabilityStatus],
    labels: list[dict[str, Any]],
) -> tuple[list[LiabilityStatus], list[str]]:
    """Settle organ-system liabilities the questions left unresolved against the labels of
    same-direction drugs approved in this indication.

    Such a drug's warnings record what its use in these patients showed: a liability its
    label names was seen in people (a labelled, managed risk); one it does not name was not
    clinically significant there. The liability question resolves only from cited claims,
    so with an approved class every mouse-knockout system stayed unresolved ("0 of 5
    settled by human data"), and one knockout finding left an approved class neutral.
    Answers the questions gave, and liabilities that are not organ systems, are kept.

    ``labels`` carry ``drug``, ``text`` (boxed and Warnings & Precautions) and
    ``claim_ids`` — the label's claims the lens holds. A settled status cites them, as
    every settled status must; a label with none settles nothing.
    """
    labels = [lab for lab in labels if lab.get("claim_ids")]
    if not labels:
        return statuses, []
    drugs = ", ".join(str(lab.get("drug") or "").lower() for lab in labels)
    all_ids = list(dict.fromkeys(str(i) for lab in labels for i in lab["claim_ids"]))
    out: list[LiabilityStatus] = []
    notes: list[str] = []
    for status in statuses:
        item = liabilities[status.index - 1] or {}
        system = str(item.get("system") or "")
        # A finding over several organ systems (the curated events of one change) is
        # named when a label names any of them.
        systems = [x for x in item.get("systems") or [] if x in SYSTEMS] or (
            [system] if system in SYSTEMS else []
        )
        if status.status != "unresolved" or not systems:
            out.append(status)
            continue
        naming = [
            lab
            for lab in labels
            if any(prose_names_system(str(lab.get("text") or ""), x) for x in systems)
        ]
        if naming:
            names = ", ".join(str(lab.get("drug") or "").lower() for lab in naming)
            settled = status.model_copy(
                update={
                    "status": "confirmed_in_humans",
                    "claim_ids": list(
                        dict.fromkeys(str(i) for lab in naming for i in lab["claim_ids"])
                    ),
                    "note": f"named in the labelled warnings of {names}"[:200],
                }
            )
        else:
            settled = status.model_copy(
                update={
                    "status": "resolved_by_human_data",
                    "claim_ids": all_ids,
                    "note": f"not among the labelled warnings of {drugs}, approved here"[:200],
                }
            )
        notes.append(f"liability {status.index} ({system}) settled by approved labels")
        out.append(settled)
    return out, notes


def safety_axes(
    liabilities: list[dict[str, Any]],
    liability_answer: LiabilityAnswer | None,
    harm: HarmAnswer | None,
    tissue: TissueAnswer | None,
    *,
    human_exposure: bool,
    direction_harms: bool,
    approved_here: bool = False,
    disease_tissue_rank: int | None = None,
    hpa_enriched: bool = False,
    assessed: bool = True,
    direction: str = "",
) -> tuple[list[AxisVerdict], Overall]:
    """The two axes and the overall verdict (see the module docstring for the rules)."""
    axes = [
        _toxicity(
            liabilities,
            liability_answer,
            harm,
            human_exposure=human_exposure,
            direction_harms=direction_harms,
            approved_here=approved_here,
            assessed=assessed,
            direction=direction,
        ),
        _tissue(tissue, disease_tissue_rank=disease_tissue_rank, hpa_enriched=hpa_enriched),
    ]
    tox, tis = axes[0].verdict, axes[1].verdict
    overall: Overall
    if liability_answer is None and harm is None and tissue is None:
        overall = "insufficient_evidence"
    elif tox is False:
        overall = "oppose"
    elif tox is True and tis is not False:
        overall = "support"
    else:
        overall = "neutral"
    return axes, overall
