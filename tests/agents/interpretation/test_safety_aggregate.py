# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The safety verdict computed from typed answers (safety_lens/aggregate.py), and the code
that validates those answers (safety_lens/questions.py).

Each test names the failure the rule prevents. The panel-shaped cases use synthetic
liabilities with placeholder systems: they reproduce the situations of the benchmark panel
(harm in people, a direction harm, an approved class with resolved liabilities, an
unexposed target with two mouse-knockout systems), not any one target's data.
"""

from __future__ import annotations

from typing import Any

import pytest

from agents.interpretation._lens_confidence import NON_DIRECTIONAL_CAP
from agents.interpretation.safety_lens.aggregate import safety_axes
from agents.interpretation.safety_lens.questions import (
    HarmAnswer,
    HarmEvent,
    LiabilityAnswer,
    LiabilityStatus,
    TissueAnswer,
    normalise_harm,
    normalise_liabilities,
    normalise_tissue,
)
from schemas.evidence import EvidenceType
from services.evidence.liabilities import DIRECTION_HARM_SYSTEM

_KINDS_FOR_CAP = (EvidenceType.GENETICS, EvidenceType.CONSTRAINT, EvidenceType.DRUGGABILITY)


def _li(system: str, severity: str) -> dict[str, Any]:
    return {"system": system, "severity": severity, "source": "src", "evidence": ["e"]}


def _status(index: int, status: str, *ids: str) -> LiabilityStatus:
    return LiabilityStatus(index=index, status=status, claim_ids=list(ids))  # type: ignore[arg-type]


def _answer(*statuses: LiabilityStatus) -> LiabilityAnswer:
    return LiabilityAnswer(liabilities=list(statuses))


_NO_HARM = HarmAnswer(harm_reported=False)
_REPORTED = TissueAnswer(disease_cell_type_expression="reported", claim_ids=["t1"])
_NOT_REPORTED = TissueAnswer(disease_cell_type_expression="not_reported")


def _run(
    liabilities: list[dict[str, Any]],
    statuses: list[LiabilityStatus],
    *,
    harm: HarmAnswer | None = _NO_HARM,
    tissue: TissueAnswer | None = _REPORTED,
    exposure: bool = True,
    direction_harms: bool = False,
    approved_here: bool = False,
    **kw: Any,
) -> tuple[dict[str, bool | None], str]:
    axes, overall = safety_axes(
        liabilities,
        _answer(*statuses),
        harm,
        tissue,
        human_exposure=exposure,
        direction_harms=direction_harms,
        approved_here=approved_here,
        **kw,
    )
    return {a.axis: a.verdict for a in axes}, overall


# ------------------------------------------------------------------- toxicity rows


def test_a_direction_harm_makes_toxicity_unfavourable():
    """The requested change worsens the disease: no other answer can rescue it."""
    liabilities = [_li(DIRECTION_HARM_SYSTEM, "high")]
    verdicts, overall = _run(
        liabilities, [_status(1, "resolved_by_human_data", "c1")], direction_harms=True
    )
    assert verdicts["toxicity"] is False and overall == "oppose"


def test_a_high_liability_confirmed_in_people_makes_toxicity_unfavourable():
    liabilities = [_li("organ system a", "high")]
    verdicts, overall = _run(liabilities, [_status(1, "confirmed_in_humans", "c1")])
    assert verdicts["toxicity"] is False and overall == "oppose"


def test_a_medium_liability_confirmed_in_people_is_not_enough_alone():
    """A mouse-knockout organ system confirmed by a mild human signal is a thing to monitor."""
    liabilities = [_li("organ system a", "medium")]
    verdicts, _ = _run(liabilities, [_status(1, "confirmed_in_humans", "c1")])
    assert verdicts["toxicity"] is True


def test_harm_reported_in_people_makes_toxicity_unfavourable():
    liabilities = [_li("organ system a", "medium")]
    harm = HarmAnswer(
        harm_reported=True,
        events=[
            HarmEvent(
                description="cognitive worsening", claim_ids=["h1"], in_requested_direction=True
            )
        ],
    )
    verdicts, overall = _run(liabilities, [_status(1, "unresolved")], harm=harm)
    assert verdicts["toxicity"] is False and overall == "oppose"


def test_two_unresolved_high_liabilities_without_exposure_are_unfavourable():
    """A target no drug has reached in people, with two serious open liabilities."""
    liabilities = [_li("organ system a", "high"), _li("organ system b", "high")]
    verdicts, overall = _run(
        liabilities, [_status(1, "unresolved"), _status(2, "unresolved")], exposure=False
    )
    assert verdicts["toxicity"] is False and overall == "oppose"


def test_two_unresolved_high_liabilities_with_exposure_are_not_unfavourable():
    liabilities = [_li("organ system a", "high"), _li("organ system b", "high")]
    verdicts, _ = _run(
        liabilities, [_status(1, "unresolved"), _status(2, "unresolved")], exposure=True
    )
    assert verdicts["toxicity"] is True


def test_one_unresolved_high_liability_is_uncertain():
    liabilities = [_li("organ system a", "high"), _li("organ system b", "medium")]
    verdicts, overall = _run(
        liabilities, [_status(1, "unresolved"), _status(2, "resolved_by_human_data", "c1")]
    )
    assert verdicts["toxicity"] is None and overall == "neutral"


def test_two_unresolved_medium_liabilities_without_exposure_are_uncertain_not_unfavourable():
    """A liver enzyme with two mouse-knockout systems and no drug in people came out
    Oppose under an earlier version of the rule; the expert reading is Support or Neutral."""
    liabilities = [_li("liver", "medium"), _li("metabolism", "medium")]
    verdicts, overall = _run(
        liabilities, [_status(1, "unresolved"), _status(2, "unresolved")], exposure=False
    )
    assert verdicts["toxicity"] is None
    assert overall == "neutral"


def test_unresolved_medium_liabilities_with_exposure_do_not_block_support():
    liabilities = [_li("liver", "medium"), _li("heart", "medium")]
    verdicts, overall = _run(
        liabilities, [_status(1, "unresolved"), _status(2, "unresolved")], exposure=True
    )
    assert verdicts["toxicity"] is True and overall == "support"


def test_a_missing_answer_leaves_toxicity_uncertain():
    axes, overall = safety_axes(
        [_li("organ system a", "medium")],
        None,
        _NO_HARM,
        _REPORTED,
        human_exposure=True,
        direction_harms=False,
    )
    assert axes[0].verdict is None and overall == "neutral"


def test_everything_settled_is_favourable():
    liabilities = [_li("organ system a", "high"), _li("organ system b", "medium")]
    verdicts, overall = _run(
        liabilities,
        [_status(1, "resolved_by_human_data", "c1"), _status(2, "resolved_by_human_data", "c2")],
    )
    assert verdicts["toxicity"] is True and overall == "support"


def test_an_unassessed_direction_is_uncertain_not_favourable():
    """Absence is not safety: with no source covering the requested change, what would be
    a favourable toxicity axis is uncertain."""
    liabilities = [_li("organ system a", "high")]
    statuses = [_status(1, "resolved_by_human_data", "c1")]

    verdicts, overall = _run(liabilities, statuses, assessed=False, direction="activate")
    axes, _ = safety_axes(
        liabilities,
        _answer(*statuses),
        _NO_HARM,
        _REPORTED,
        human_exposure=True,
        direction_harms=False,
        assessed=False,
        direction="activate",
    )

    assert verdicts["toxicity"] is None and overall == "neutral"
    assert axes[0].rationale.startswith("Harms of raising the target are not assessed")
    assert _run(liabilities, statuses, assessed=True)[0]["toxicity"] is True


def test_an_unassessed_direction_does_not_soften_an_unfavourable_axis():
    verdicts, _ = _run([], [], direction_harms=True, assessed=False, direction="activate")
    assert verdicts["toxicity"] is False


def test_an_unanswered_liability_counts_as_unresolved():
    """The model skipped liability 2: it must not be read as settled."""
    liabilities = [_li("organ system a", "high"), _li("organ system b", "high")]
    verdicts, _ = _run(liabilities, [_status(1, "unresolved")], exposure=False)
    assert verdicts["toxicity"] is False


def test_a_status_without_a_citation_does_not_settle_a_liability():
    liabilities = [_li("organ system a", "high"), _li("organ system b", "high")]
    verdicts, _ = _run(
        liabilities,
        [_status(1, "resolved_by_human_data"), _status(2, "resolved_by_human_data")],
        exposure=False,
    )
    assert verdicts["toxicity"] is False


# --------------------------------------------------------------- tissue rows


def test_absence_with_a_citation_is_unfavourable_and_hides_a_top_rank():
    absent = TissueAnswer(disease_cell_type_expression="absent", claim_ids=["t1"])
    verdicts, overall = _run([], [], tissue=absent, disease_tissue_rank=1)
    assert verdicts["tissue_specificity"] is False and overall == "neutral"


def test_absence_without_a_citation_is_not_absence():
    """Low bulk expression alone is not absence in a minor cell type."""
    absent = TissueAnswer(disease_cell_type_expression="absent")
    verdicts, _ = _run([], [], tissue=absent)
    assert verdicts["tissue_specificity"] is None


def test_reported_expression_is_favourable():
    verdicts, _ = _run([], [], tissue=_REPORTED)
    assert verdicts["tissue_specificity"] is True


def test_a_top_five_disease_tissue_is_favourable():
    verdicts, _ = _run([], [], tissue=_NOT_REPORTED, disease_tissue_rank=5)
    assert verdicts["tissue_specificity"] is True


def test_a_disease_tissue_ranked_sixth_is_not_by_rank():
    verdicts, _ = _run([], [], tissue=_NOT_REPORTED, disease_tissue_rank=6)
    assert verdicts["tissue_specificity"] is None


def test_hpa_enrichment_is_favourable():
    verdicts, _ = _run([], [], tissue=_NOT_REPORTED, hpa_enriched=True)
    assert verdicts["tissue_specificity"] is True


def test_nothing_known_about_expression_is_uncertain():
    verdicts, _ = _run([], [], tissue=_NOT_REPORTED)
    assert verdicts["tissue_specificity"] is None


# ------------------------------------------------------------------ overall rows


def test_toxicity_favourable_and_tissue_unfavourable_is_neutral_with_disagreeing_axes():
    """Neutral must not be flipped by align_neutral_with_axes: the axes disagree."""
    absent = TissueAnswer(disease_cell_type_expression="absent", claim_ids=["t1"])
    axes, overall = safety_axes(
        [], _answer(), _NO_HARM, absent, human_exposure=True, direction_harms=False
    )
    assert overall == "neutral" and {a.verdict for a in axes} == {True, False}


def test_a_neutral_verdict_always_has_an_uncertain_or_disagreeing_axis():
    liabilities = [_li("organ system a", "high")]
    axes, overall = safety_axes(
        liabilities, _answer(_status(1, "unresolved")), _NO_HARM, _REPORTED,
        human_exposure=True, direction_harms=False,
    )  # fmt: skip
    assert overall == "neutral" and None in {a.verdict for a in axes}


def test_no_answer_at_all_is_insufficient_evidence():
    _, overall = safety_axes([], None, None, None, human_exposure=False, direction_harms=False)
    assert overall == "insufficient_evidence"


def test_confidences_are_left_for_the_evidence_scorer():
    axes, _ = safety_axes(
        [], _answer(), _NO_HARM, _REPORTED, human_exposure=True, direction_harms=False
    )
    assert all(a.confidence == 0.0 for a in axes)


def test_supporting_ids_are_the_claims_behind_the_deciding_answers():
    liabilities = [_li("organ system a", "high")]
    harm = HarmAnswer(
        harm_reported=True,
        events=[HarmEvent(description="d", claim_ids=["h1", "c1"], in_requested_direction=True)],
    )
    axes, _ = safety_axes(
        liabilities,
        _answer(_status(1, "confirmed_in_humans", "c1", "c2")),
        harm,
        TissueAnswer(disease_cell_type_expression="reported", claim_ids=["t1"]),
        human_exposure=True,
        direction_harms=False,
    )
    assert axes[0].supporting_claim_ids == ["c1", "c2", "h1"]
    assert axes[1].supporting_claim_ids == ["t1"]
    assert "organ system a" in axes[0].rationale


# ------------------------------------------------------- an approved same-direction drug


def test_harm_confirmed_in_people_does_not_oppose_an_approved_class():
    """An antibody class approved in the very indication, with a labelled skin reaction,
    came out Oppose under an earlier version: the clinical record accepted that risk."""
    liabilities = [_li("skin reaction", "high"), _li("organ system b", "medium")]
    verdicts, overall = _run(
        liabilities,
        [_status(1, "confirmed_in_humans", "c1"), _status(2, "unresolved")],
        approved_here=True,
    )
    assert verdicts["toxicity"] is True and overall == "support"


def test_harm_reported_in_people_does_not_oppose_an_approved_class():
    harm = HarmAnswer(
        harm_reported=True,
        events=[HarmEvent(description="d", claim_ids=["h1"], in_requested_direction=True)],
    )
    verdicts, _ = _run(
        [_li("organ system a", "medium")], [_status(1, "unresolved")], harm=harm, approved_here=True
    )
    assert verdicts["toxicity"] is True


def test_a_direction_harm_does_not_oppose_an_approved_class():
    liabilities = [_li(DIRECTION_HARM_SYSTEM, "high")]
    verdicts, _ = _run(
        liabilities, [_status(1, "unresolved")], direction_harms=True, approved_here=True
    )
    assert verdicts["toxicity"] is None  # one high liability is still unresolved


def test_without_approval_the_same_confirmed_harm_opposes():
    liabilities = [_li("skin reaction", "high")]
    verdicts, overall = _run(
        liabilities, [_status(1, "confirmed_in_humans", "c1")], approved_here=False
    )
    assert verdicts["toxicity"] is False and overall == "oppose"


# ---------------------------------------------------------------- panel shapes


def test_secretase_inhibitor_like_harm_in_people_opposes():
    """Cognitive worsening in treated patients: toxicity unfavourable, lens opposes."""
    liabilities = [_li("nervous system", "medium"), _li("skeletal muscle", "medium")]
    harm = HarmAnswer(
        harm_reported=True,
        events=[
            HarmEvent(
                description="cognitive worsening versus placebo",
                claim_ids=["h1"],
                in_requested_direction=True,
            )
        ],
    )
    verdicts, overall = _run(
        liabilities, [_status(1, "unresolved"), _status(2, "unresolved")], harm=harm, exposure=False
    )
    assert verdicts["toxicity"] is False and overall == "oppose"


def test_inflammatory_cytokine_blocker_in_a_disease_it_worsens_opposes():
    liabilities = [_li(DIRECTION_HARM_SYSTEM, "high"), _li("immune", "medium")]
    verdicts, overall = _run(
        liabilities,
        [_status(1, "unresolved"), _status(2, "unresolved")],
        direction_harms=True,
    )
    assert verdicts["toxicity"] is False and overall == "oppose"


def test_lipid_enzyme_inhibitor_like_approved_class_with_resolved_liabilities_supports():
    liabilities = [_li("heart", "medium"), _li("liver", "medium"), _li("metabolism", "medium")]
    verdicts, overall = _run(
        liabilities,
        [
            _status(1, "resolved_by_human_data", "c1"),
            _status(2, "unresolved"),
            _status(3, "unresolved"),
        ],
        exposure=True,
        hpa_enriched=True,
    )
    assert verdicts == {"toxicity": True, "tissue_specificity": True} and overall == "support"


def test_liver_enzyme_like_unexposed_target_with_two_mouse_systems_is_not_opposed():
    """The expectation allows Support or Neutral; the rule must not oppose it."""
    liabilities = [_li("liver", "medium"), _li("metabolism", "medium")]
    verdicts, overall = _run(
        liabilities,
        [_status(1, "unresolved"), _status(2, "unresolved")],
        exposure=False,
        tissue=_NOT_REPORTED,
        disease_tissue_rank=1,
    )
    assert overall in ("support", "neutral") and verdicts["toxicity"] is not False


# ----------------------------------------------------------- answer validation


def test_normalise_liabilities_fills_drops_and_dedupes():
    answer = _answer(
        _status(1, "resolved_by_human_data", "c1"),
        _status(1, "confirmed_in_humans", "c9"),
        _status(7, "confirmed_in_humans", "c2"),
        _status(3, "confirmed_in_humans"),
    )
    out, notes = normalise_liabilities(answer, 3)
    assert [(s.index, s.status) for s in out] == [
        (1, "resolved_by_human_data"),
        (2, "unresolved"),
        (3, "unresolved"),
    ]
    assert len(notes) == 4


def test_normalise_liabilities_truncates_notes():
    out, _ = normalise_liabilities(
        _answer(LiabilityStatus(index=1, status="unresolved", note="x" * 500)), 1
    )
    assert len(out[0].note) == 200


def test_normalise_harm_keeps_only_cited_events_in_the_requested_direction():
    answer = HarmAnswer(
        harm_reported=True,
        events=[
            HarmEvent(description="a", claim_ids=["c1"], in_requested_direction=False),
            HarmEvent(description="b", claim_ids=[], in_requested_direction=True),
        ],
    )
    out, notes = normalise_harm(answer)
    assert out is not None and out.harm_reported is False and out.events == []
    assert notes


def test_normalise_harm_recomputes_harm_reported_from_what_remains():
    answer = HarmAnswer(
        harm_reported=False,
        events=[HarmEvent(description="a" * 400, claim_ids=["c1"], in_requested_direction=True)],
    )
    out, _ = normalise_harm(answer)
    assert out is not None and out.harm_reported is True and len(out.events[0].description) == 160


def test_normalise_tissue_drops_tissues_the_facts_do_not_name():
    answer = TissueAnswer(
        disease_cell_type_expression="reported",
        claim_ids=["c1"],
        off_target_tissues_of_concern=["Liver", "invented tissue", "brain cortex"],
    )
    out, notes = normalise_tissue(answer, ["Liver", "Brain_Cortex"])
    assert out is not None and out.off_target_tissues_of_concern == ["Liver", "Brain_Cortex"]
    assert notes


@pytest.mark.parametrize("claim_ids", [[], ["c1"]])
def test_normalise_tissue_requires_a_citation_for_absence(claim_ids):
    answer = TissueAnswer(disease_cell_type_expression="absent", claim_ids=claim_ids)
    out, _ = normalise_tissue(answer, [])
    assert out is not None
    assert out.disease_cell_type_expression == ("absent" if claim_ids else "not_reported")


# ------------------------------------------------------------- settled by approved labels


def test_unresolved_organ_liabilities_are_settled_by_the_approved_labels():
    from agents.interpretation.safety_lens.aggregate import settle_by_labels
    from agents.interpretation.safety_lens.questions import LiabilityStatus

    liabilities = [
        {"system": "liver", "severity": "medium"},
        {"system": "heart", "severity": "high"},
        {"system": "dosage sensitivity", "severity": "high"},
        {"system": "skin", "severity": "medium"},
    ]
    statuses = [
        LiabilityStatus(index=1, status="unresolved"),
        LiabilityStatus(index=2, status="unresolved"),
        LiabilityStatus(index=3, status="unresolved"),
        LiabilityStatus(index=4, status="resolved_by_human_data", claim_ids=["c1"]),
    ]
    labels = [
        {
            "drug": "DRUG-A",
            "text": "5.2 Hepatotoxicity: liver enzyme elevations occurred.",
            "claim_ids": ["label-claim"],
        }
    ]
    out, notes = settle_by_labels(liabilities, statuses, labels)
    assert out[0].claim_ids == ["label-claim"] and out[1].claim_ids == ["label-claim"]
    uncited = [{**labels[0], "claim_ids": []}]
    assert settle_by_labels(liabilities, statuses, uncited) == (statuses, [])
    assert [s.status for s in out] == [
        "confirmed_in_humans",  # the label names the liver
        "resolved_by_human_data",  # not among the labelled warnings
        "unresolved",  # not an organ system: left to the question
        "resolved_by_human_data",  # the question's own answer is kept
    ]
    assert out[3].claim_ids == ["c1"] and len(notes) == 2


def test_without_approved_labels_nothing_is_settled():
    from agents.interpretation.safety_lens.aggregate import settle_by_labels
    from agents.interpretation.safety_lens.questions import LiabilityStatus

    statuses = [LiabilityStatus(index=1, status="unresolved")]
    out, notes = settle_by_labels([{"system": "liver"}], statuses, [])
    assert out == statuses and notes == []


# ------------------------------------------------------------------- absence cap


def _toxicity_axis(statuses: list[LiabilityStatus], *, exposure: bool, n: int = 2):
    liabilities = [_li(f"organ system {i}", "medium") for i in range(n)]
    axes, _ = safety_axes(
        liabilities,
        _answer(*statuses),
        _NO_HARM,
        _REPORTED,
        human_exposure=exposure,
        direction_harms=False,
    )
    return next(a for a in axes if a.axis == "toxicity")


def test_a_favourable_toxicity_reached_by_absence_is_capped_as_a_non_finding():
    """Nothing settled by human data and nobody exposed: CETP safety scored 0.94 from
    the records attached to a favourable axis that rested on nothing being reported."""
    axis = _toxicity_axis([_status(1, "unresolved")], exposure=False, n=1)
    assert axis.verdict is True
    assert axis.confidence_cap == NON_DIRECTIONAL_CAP


@pytest.mark.parametrize(
    ("statuses", "exposure"),
    [
        ([_status(1, "unresolved"), _status(2, "unresolved")], True),  # people exposed
        ([_status(1, "resolved_by_human_data", "c1"), _status(2, "unresolved")], False),
    ],
)
def test_exposure_or_a_settled_liability_leaves_toxicity_uncapped(statuses, exposure):
    axis = _toxicity_axis(statuses, exposure=exposure)
    assert axis.verdict is True
    assert axis.confidence_cap is None


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
