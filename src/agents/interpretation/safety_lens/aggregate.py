# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The safety verdict, computed from the gene's findings and where people saw them.

Pure code: no model call. The candidate liabilities (services/evidence/liabilities.py)
are the findings; each one's status comes from where its human evidence was seen
(``finding_statuses``), and the toxicity axis from the first rule that applies:

1. A harm event, and no requested-direction drug approved here → unfavourable.
2. The target is common essential, the requested change lowers it, and no
   requested-direction drug is approved here → unfavourable; in oncology the overall
   verdict is neutral.
3. Nobody was given a requested-direction drug, and two or more open high findings →
   unfavourable.
4. Any open high finding → uncertain, whatever the exposure.
5. Nobody was given a requested-direction drug, and any open finding → uncertain.
6. No data on the requested change were examined, and nobody was given such a drug →
   uncertain, "not assessed".
7. Otherwise favourable, capped as non-directional when nobody was given such a drug.

More evidence of harm never gives a better verdict: the split lens's rules let one open
medium liability give favourable while no data gave "not assessed", and two open high
liabilities reach support once a drug had been given to people.

Harm events are harm in people in this indication: a label warning that a
requested-direction drug causes or worsens the disease, or safety stops of two or more
requested-direction compounds. One compound's stop is an open high finding: it cannot
tell the compound from the mechanism.

"Given to people" is ``human_exposure``: a requested-direction drug approved in any
indication, or through a completed phase 3 or 4 trial. An approval elsewhere leaves the
findings open (another indication's dose, duration and population do not stand for this
one); an approval in this indication settles them against its labels
(``settle_by_labels``).

Tissue-specificity axis: ``True`` when the disease tissue is among the gene's five
highest-expressing tissues, or HPA names the gene tissue-enriched; otherwise ``None``.
It cannot be unfavourable, so the overall verdict follows toxicity: ``False`` → oppose
(neutral for rule 2 in oncology), ``True`` → support, ``None`` → neutral.

Confidences stay 0.0 here: ``score_verdict`` computes them from the cited claims.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agents.interpretation._lens_confidence import NON_DIRECTIONAL_CAP
from schemas.verdicts import AxisVerdict
from services.evidence.drug_precedent import requested_effect
from services.evidence.liabilities import BOXED_WARNING, DIRECTION_HARM_SYSTEM, SAFETY_STOP
from services.evidence.organ_systems import SYSTEMS, prose_names_system

# 2: verdict rules run before the narrative; 3: liabilities settled by approved labels;
# 4: a favourable toxicity axis reached by absence is capped as a non-finding.
# 5: an unassessed direction of change is uncertain, not favourable.
# 6: a finding over several organ systems is settled when a label names any of them.
# 7: the verdict from the findings alone, without questions; more harm never improves it.
AGGREGATION_VERSION = "7"
TOP_TISSUE_RANK = 5

Overall = Literal["support", "oppose", "neutral", "insufficient_evidence"]
Status = Literal["confirmed_in_humans", "resolved_by_human_data", "unresolved"]


class LiabilityStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int  # 1-based number of the liability in the candidate list
    status: Status
    claim_ids: list[str] = []  # the label claims that settled it
    note: str = ""  # ≤ 200 chars


def _systems(items: list[dict[str, Any]]) -> str:
    return ", ".join(str(li.get("system")) for li in items[:4]) + (
        f" and {len(items) - 4} more" if len(items) > 4 else ""
    )


def is_finding(liability: dict[str, Any]) -> bool:
    """Every candidate liability but the label warning that the drug worsens the disease,
    which is a harm event."""
    return liability.get("system") != DIRECTION_HARM_SYSTEM


def settle_by_labels(
    liabilities: list[dict[str, Any]],
    statuses: list[LiabilityStatus],
    labels: list[dict[str, Any]],
) -> tuple[list[LiabilityStatus], list[str]]:
    """Settle the unresolved liabilities against the labels of same-direction drugs
    approved in this indication.

    Such a drug's warnings record what its use in these patients showed: a liability its
    label names was seen in people (a labelled, managed risk); one it does not name was
    not clinically significant there. A finding over several organ systems is named when
    a label names any of them; the boxed warning of a drug approved here is its own label;
    a finding that is not an organ system (constraint, essentiality, a safety stop) is
    not among the labelled warnings. Statuses already settled are kept.

    ``labels`` carry ``drug``, ``text`` (boxed and Warnings & Precautions) and
    ``claim_ids`` — the label's claims the lens holds. A settled status cites them; a
    label with none settles nothing.
    """
    labels = [lab for lab in labels if lab.get("claim_ids")]
    if not labels:
        return statuses, []
    drugs = ", ".join(str(lab.get("drug") or "").lower() for lab in labels)
    all_ids = list(dict.fromkeys(str(i) for lab in labels for i in lab["claim_ids"]))
    out: list[LiabilityStatus] = []
    notes: list[str] = []
    for status in statuses:
        if status.status != "unresolved":
            out.append(status)
            continue
        item = liabilities[status.index - 1] or {}
        system = str(item.get("system") or "")
        systems = [x for x in item.get("systems") or [] if x in SYSTEMS] or (
            [system] if system in SYSTEMS else []
        )
        naming = [
            lab
            for lab in labels
            if any(prose_names_system(str(lab.get("text") or ""), x) for x in systems)
        ]
        if system == BOXED_WARNING and item.get("seen") == "this indication":
            settled = status.model_copy(
                update={
                    "status": "confirmed_in_humans",
                    "claim_ids": all_ids,
                    "note": "the boxed warning of a drug approved here",
                }
            )
        elif naming:
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


def finding_statuses(
    liabilities: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    *,
    approved_here: bool,
) -> tuple[list[LiabilityStatus], list[str]]:
    """One status per candidate liability, in order: settled against the labels of
    requested-direction drugs approved in this indication, otherwise open."""
    statuses = [
        LiabilityStatus(index=i, status="unresolved") for i in range(1, len(liabilities) + 1)
    ]
    if not approved_here:
        return statuses, []
    return settle_by_labels(liabilities, statuses, labels)


def harm_events(liabilities: list[dict[str, Any]]) -> list[str]:
    """Harm in people in this indication: label warnings that a requested-direction drug
    worsens the disease, and safety stops of two or more requested-direction compounds."""
    events = [str(e) for li in liabilities if not is_finding(li) for e in li.get("evidence") or []]
    stopped = list(
        dict.fromkeys(
            str(li.get("source")) for li in liabilities if li.get("system") == SAFETY_STOP
        )
    )
    if len(stopped) >= 2:
        events.append(f"trials of {', '.join(stopped)} in this indication were stopped for safety")
    return events


def _toxicity(
    liabilities: list[dict[str, Any]],
    statuses: list[LiabilityStatus],
    *,
    human_exposure: bool,
    approved_here: bool,
    assessed: bool,
    direction: str,
    common_essential: bool,
) -> tuple[AxisVerdict, int]:
    """The toxicity axis and the number of the rule that decided it."""
    status_of = {s.index: s for s in statuses}
    open_findings = [
        li
        for i, li in enumerate(liabilities, 1)
        if is_finding(li) and (s := status_of.get(i)) is not None and s.status == "unresolved"
    ]
    open_high = [li for li in open_findings if li.get("severity") == "high"]
    events = harm_events(liabilities)
    settled = [s for s in statuses if s.status != "unresolved"]
    claim_ids = list(dict.fromkeys(cid for s in settled for cid in s.claim_ids))
    lowers = requested_effect(direction) != "increase"

    verdict: bool | None
    cap: float | None = None
    rule: int
    if events and not approved_here:
        rule, verdict = 1, False
        why = f"Harm in people in this indication: {events[0]}."
    elif common_essential and lowers and not approved_here:
        rule, verdict = 2, False
        why = (
            "Common essential: every cell line needs it, so the dependency is not selective "
            "for the indication and lowering its activity reaches normal proliferating tissue."
        )
    elif not human_exposure and len(open_high) >= 2:
        rule, verdict = 3, False
        why = (
            f"No drug acting on the target in the requested direction has been given to "
            f"people and {len(open_high)} high-severity findings are open: "
            f"{_systems(open_high)}."
        )
    elif open_high:
        rule, verdict = 4, None
        why = f"High-severity findings without human data here: {_systems(open_high)}."
    elif not human_exposure and open_findings:
        rule, verdict = 5, None
        why = (
            f"No drug acting on the target in the requested direction has been given to "
            f"people, and findings remain to monitor: {_systems(open_findings)}."
        )
    elif not assessed and not human_exposure:
        rule, verdict = 6, None
        change = "raising" if requested_effect(direction) == "increase" else "lowering"
        why = f"Harms of {change} the target are not assessed: no source covers that change."
    else:
        rule, verdict = 7, True
        why = (
            f"No high-severity finding is open and no harm in people was seen here "
            f"({len(settled)} of {len(liabilities)} candidate liabilities settled by human "
            f"data)."
        )
        # Favourable without exposure: the change was examined and nothing was found,
        # which is a non-finding however many records are attached to it.
        if not human_exposure:
            cap = NON_DIRECTIONAL_CAP
    axis = AxisVerdict(
        axis="toxicity",
        verdict=verdict,
        rationale=why,
        supporting_claim_ids=claim_ids,
        confidence_cap=cap,
    )
    return axis, rule


def _tissue(*, disease_tissue_rank: int | None, hpa_enriched: bool) -> AxisVerdict:
    verdict: bool | None
    if disease_tissue_rank is not None and disease_tissue_rank <= TOP_TISSUE_RANK:
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
    return AxisVerdict(axis="tissue_specificity", verdict=verdict, rationale=why)


def safety_axes(
    liabilities: list[dict[str, Any]],
    statuses: list[LiabilityStatus],
    *,
    human_exposure: bool,
    approved_here: bool = False,
    assessed: bool = True,
    direction: str = "",
    common_essential: bool = False,
    oncology: bool = False,
    disease_tissue_rank: int | None = None,
    hpa_enriched: bool = False,
) -> tuple[list[AxisVerdict], Overall, int]:
    """The two axes, the overall verdict and the number of the toxicity rule that decided
    it (see the module docstring for the rules)."""
    tox, rule = _toxicity(
        liabilities,
        statuses,
        human_exposure=human_exposure,
        approved_here=approved_here,
        assessed=assessed,
        direction=direction,
        common_essential=common_essential,
    )
    axes = [tox, _tissue(disease_tissue_rank=disease_tissue_rank, hpa_enriched=hpa_enriched)]
    overall: Overall
    if tox.verdict is False:
        overall = "neutral" if rule == 2 and oncology else "oppose"
    elif tox.verdict is True:
        overall = "support"
    else:
        overall = "neutral"
    return axes, overall, rule
