# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The safety verdict computed from the gene's findings (safety_lens/aggregate.py).

Each test names the failure the rule prevents. Synthetic liabilities with placeholder
systems: they reproduce situations (harm in people here, an approved class, an unexposed
target with knockout phenotypes, a single compound's safety stop), not any one target's
data.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from agents.interpretation._lens_confidence import NON_DIRECTIONAL_CAP
from agents.interpretation.safety_lens.aggregate import (
    LiabilityStatus,
    finding_statuses,
    harm_events,
    safety_axes,
    settle_by_labels,
)
from schemas.evidence import EvidenceType
from services.evidence.liabilities import BOXED_WARNING, DIRECTION_HARM_SYSTEM, SAFETY_STOP

_KINDS_FOR_CAP = (EvidenceType.GENETICS, EvidenceType.CONSTRAINT, EvidenceType.DRUGGABILITY)


def _li(system: str, severity: str, **kw: Any) -> dict[str, Any]:
    return {"system": system, "severity": severity, "source": "src", "evidence": ["e"], **kw}


def _stop(drug: str) -> dict[str, Any]:
    return _li(SAFETY_STOP, "high", source=drug, seen="this indication")


_WORSENS = _li(DIRECTION_HARM_SYSTEM, "high", evidence=['drug-a label warns: "worsens it"'])


def _open(liabilities: list[dict[str, Any]]) -> list[LiabilityStatus]:
    return finding_statuses(liabilities, [], approved_here=False)[0]


def _run(
    liabilities: list[dict[str, Any]],
    *,
    exposure: bool = False,
    approved_here: bool = False,
    assessed: bool = True,
    statuses: list[LiabilityStatus] | None = None,
    **kw: Any,
) -> tuple[bool | None, str, int]:
    axes, overall, rule = safety_axes(
        liabilities,
        statuses if statuses is not None else _open(liabilities),
        human_exposure=exposure,
        approved_here=approved_here,
        assessed=assessed,
        direction=kw.pop("direction", "inhibit"),
        **kw,
    )
    tox = next(a for a in axes if a.axis == "toxicity")
    return tox.verdict, overall, rule


# ------------------------------------------------------------------- the rules in order


def test_a_label_warning_that_the_drug_worsens_the_disease_opposes():
    assert _run([_WORSENS], exposure=True) == (False, "oppose", 1)


def test_a_harm_event_does_not_oppose_an_approved_class():
    """An approved class's labelled harm is a managed risk, not a reason to oppose."""
    settled = [LiabilityStatus(index=1, status="resolved_by_human_data", claim_ids=["c"])]
    verdict, _, rule = _run([_WORSENS], exposure=True, approved_here=True, statuses=settled)
    assert rule != 1 and verdict is True


def test_stops_of_two_compounds_here_are_harm_in_people():
    assert harm_events([_stop("drug-a"), _stop("drug-b")])
    assert _run([_stop("drug-a"), _stop("drug-b")], exposure=True)[1:] == ("oppose", 1)


def test_one_compounds_stop_is_open_not_a_harm_event():
    """One compound's stop cannot tell the compound from the mechanism."""
    assert harm_events([_stop("drug-a")]) == []
    assert _run([_stop("drug-a")], exposure=True) == (None, "neutral", 4)


@pytest.mark.parametrize(("oncology", "overall"), [(False, "oppose"), (True, "neutral")])
def test_lowering_a_common_essential_target_is_unfavourable(oncology, overall):
    verdict, got, rule = _run([], exposure=True, common_essential=True, oncology=oncology)
    assert (verdict, got, rule) == (False, overall, 2)


@pytest.mark.parametrize(("direction", "approved_here"), [("activate", False), ("inhibit", True)])
def test_the_essentiality_rule_needs_lowering_and_no_approval_here(direction, approved_here):
    assert (
        _run(
            [],
            exposure=True,
            common_essential=True,
            direction=direction,
            approved_here=approved_here,
        )[2]
        != 2
    )


def test_two_open_high_findings_without_exposure_oppose():
    assert _run([_li("liver", "high"), _li("heart", "high")]) == (False, "oppose", 3)


def test_two_open_high_findings_with_exposure_are_uncertain_not_support():
    """The split lens checked ``len(open_high) == 1`` and let two reach support."""
    assert _run([_li("liver", "high"), _li("heart", "high")], exposure=True) == (
        None,
        "neutral",
        4,
    )


def test_the_disease_worsening_item_is_not_counted_as_a_finding():
    """It is a harm event, not a second high finding."""
    settled = [LiabilityStatus(index=i, status="unresolved") for i in (1, 2)]
    _, _, rule = _run([_WORSENS, _li("liver", "high")], approved_here=True, statuses=settled)
    assert rule == 4


def test_one_open_medium_finding_without_exposure_is_uncertain():
    """The split lens gave one open mouse liability favourable while no data gave "not
    assessed": finding a harm improved the verdict."""
    assert _run([_li("lung", "medium")]) == (None, "neutral", 5)


def test_open_medium_findings_with_exposure_do_not_block_support():
    assert _run([_li("lung", "medium"), _li("skin", "medium")], exposure=True)[:2] == (
        True,
        "support",
    )


def test_an_unexamined_change_is_not_assessed():
    assert _run([], assessed=False, direction="activate") == (None, "neutral", 6)
    assert _run([], assessed=False, exposure=True)[0] is True


def test_favourable_without_exposure_is_capped_as_a_non_finding():
    axes, overall, rule = safety_axes([], [], human_exposure=False, assessed=True)
    tox = next(a for a in axes if a.axis == "toxicity")
    assert (tox.verdict, overall, rule) == (True, "support", 7)
    assert tox.confidence_cap == NON_DIRECTIONAL_CAP
    axes, _, _ = safety_axes([], [], human_exposure=True, assessed=True)
    assert next(a for a in axes if a.axis == "toxicity").confidence_cap is None


_RANK = {False: 0, None: 1, True: 2}


@pytest.mark.parametrize(
    ("exposure", "assessed"), list(itertools.product((False, True), (False, True)))
)
def test_more_evidence_of_harm_never_gives_a_better_verdict(exposure, assessed):
    pool = [_li("lung", "medium"), _li("liver", "high"), _li("heart", "high"), _stop("drug-a")]
    for n in range(len(pool)):
        before = _run(pool[:n], exposure=exposure, assessed=assessed)[0]
        after = _run(pool[: n + 1], exposure=exposure, assessed=assessed)[0]
        assert _RANK[after] <= _RANK[before], (n, before, after)


# ------------------------------------------------------------------- where people saw it


_LABELS = [
    {
        "drug": "DRUG-A",
        "text": "5.2 Hepatotoxicity: liver enzyme elevations occurred.",
        "claim_ids": ["label-claim"],
    }
]


def test_an_approval_in_this_indication_settles_every_finding():
    liabilities = [
        _li("liver", "medium"),
        _li("heart", "high"),
        _li("dosage sensitivity", "high"),
        _li(BOXED_WARNING, "high", seen="this indication"),
        _li("curated adverse events (heart, liver)", "high", systems=["heart", "liver"]),
    ]
    statuses, notes = finding_statuses(liabilities, _LABELS, approved_here=True)
    assert [s.status for s in statuses] == [
        "confirmed_in_humans",  # the label names the liver
        "resolved_by_human_data",  # not among the labelled warnings
        "resolved_by_human_data",  # not an organ system: not among them either
        "confirmed_in_humans",  # the boxed warning of a drug approved here
        "confirmed_in_humans",  # the label names one of its systems
    ]
    assert all(s.claim_ids for s in statuses) and len(notes) == 5
    assert _run(liabilities, exposure=True, approved_here=True, statuses=statuses)[0] is True


def test_an_approval_only_elsewhere_leaves_every_finding_open():
    """Another indication's dose, duration and population do not stand for this one."""
    liabilities = [_li(BOXED_WARNING, "high", seen="other indications")]
    statuses, _ = finding_statuses(liabilities, _LABELS, approved_here=False)
    assert [s.status for s in statuses] == ["unresolved"]
    assert _run(liabilities, exposure=True, statuses=statuses)[:2] == (None, "neutral")


def test_an_approved_label_settles_findings_without_held_claims():
    """CFTR: the combination products' labels carried no claims the lens held, were
    dropped, and their boxed liver warning read "not among the labelled warnings of
    ivacaftor"."""
    statuses = [LiabilityStatus(index=1, status="unresolved")]
    uncited = [{**_LABELS[0], "claim_ids": []}]
    out, _ = settle_by_labels([_li("liver", "medium")], statuses, uncited)
    assert out[0].status != "unresolved" and out[0].claim_ids == []
    assert settle_by_labels([_li("liver", "medium")], statuses, []) == (statuses, [])


def test_a_boxed_warning_of_a_combination_approved_here_is_confirmed():
    from services.evidence.liabilities import candidate_liabilities

    items = candidate_liabilities(
        direction="activate",
        boxed_warnings=[("ELEXACAFTOR, TEZACAFTOR AND IVACAFTOR", "Drug-induced liver injury.")],
        approved_here=["ELEXACAFTOR", "IVACAFTOR"],
    )
    boxed = [li.model_dump() for li in items if li.system == BOXED_WARNING]
    assert boxed[0]["seen"] == "this indication"
    statuses, _ = finding_statuses(boxed, _LABELS, approved_here=True)
    assert statuses[0].status == "confirmed_in_humans"


def test_status_labels_say_what_settled_the_finding():
    from agents.interpretation.safety_lens.aggregate import status_label

    assert status_label(
        "resolved_by_human_data", "not among the labelled warnings of x, approved here"
    ) == ("not a labelled warning here")
    assert status_label("confirmed_in_humans", "named in the labelled warnings of x") == (
        "labelled warning here (managed)"
    )
    assert status_label("unresolved") == "unresolved"


def test_a_status_already_settled_is_kept():
    statuses = [LiabilityStatus(index=1, status="resolved_by_human_data", claim_ids=["c1"])]
    out, notes = settle_by_labels([_li("liver", "medium")], statuses, _LABELS)
    assert out == statuses and notes == []


# ------------------------------------------------------------------------- tissue axis


def _tissue(**kw: Any) -> bool | None:
    axes, _, _ = safety_axes([], [], human_exposure=True, **kw)
    return next(a for a in axes if a.axis == "tissue_specificity").verdict


def test_a_top_five_disease_tissue_is_favourable():
    assert _tissue(disease_tissue_rank=5) is True
    assert _tissue(disease_tissue_rank=6) is None


def test_hpa_enrichment_is_favourable_and_nothing_known_is_uncertain():
    assert _tissue(hpa_enriched=True) is True
    assert _tissue() is None


def test_the_overall_verdict_follows_toxicity():
    assert _run([], exposure=True)[1] == "support"
    assert _run([_li("liver", "high")], exposure=True)[1] == "neutral"


def test_score_axis_holds_the_cap():
    from agents.interpretation._lens_confidence import score_axis
    from schemas.verdicts import AxisVerdict
    from tests.agents.interpretation.test_lens_confidence import _claim

    claims = [_claim(f"c{i}", et) for i, et in enumerate(_KINDS_FOR_CAP)]
    axis = AxisVerdict(
        axis="toxicity",
        verdict=True,
        supporting_claim_ids=[str(c.evidence_id) for c in claims],
        confidence_cap=NON_DIRECTIONAL_CAP,
    )
    pool = {str(c.evidence_id): c for c in claims}
    uncapped = score_axis(
        axis.model_copy(update={"confidence_cap": None}), pool, lambda _c: 1.0, {}
    )
    capped = score_axis(axis, pool, lambda _c: 1.0, {})
    assert uncapped.confidence > NON_DIRECTIONAL_CAP
    assert capped.confidence == NON_DIRECTIONAL_CAP


def test_harm_in_the_indication_ends_the_oncology_exception_for_an_essential_target():
    """PLK1: common essential, oncology, and a phase 3 with excess deaths — neutral."""
    essential = [_li("cell fitness (common essential)", "high")]
    base = dict(exposure=True, common_essential=True, oncology=True)
    assert _run(essential, **base)[1] == "neutral"
    assert _run([*essential, _stop("volasertib")], **base)[1] == "oppose"
