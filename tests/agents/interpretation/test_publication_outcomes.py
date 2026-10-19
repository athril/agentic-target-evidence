# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Publication-reported trial outcomes (clinical_lens/publication_outcomes.py).

The failure: a randomised trial from before the registry, in which the disease worsened on
one of the target's drugs, existed only in a publication, so the programme reading said
no drug had been tested in the indication.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

import agents.interpretation.clinical_lens.publication_outcomes as po
from agents.interpretation._lens_questions import QuestionResult
from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance
from services.evidence.disease_scope import build_disease_scope

_DRUGS = ["DRUG-A", "DRUG-B"]


def _claim(text: str, paper: uuid.UUID | None = None, kind=EvidenceType.ARTICLE) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=paper or uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENE-A",
        disease="disease one",
        evidence_type=kind,
        claim_text=text,
        provenance=Provenance(agent_name="t", timestamp=datetime.now(UTC), trace_id="t"),
        classification=DataClass.NON_SENSITIVE,
    )


def _paper(scope: str = "in_scope", family: str = "rct", level: int = 2, rcr: float = 1.0):
    return {"scope": scope, "rcr": rcr, "design": {"clinical": {"family": family, "level": level}}}


def test_candidates_are_trial_reports_of_the_drugs_in_the_indication() -> None:
    rct, review, other_disease, trial3, neighbour, unclassified = (uuid.uuid4() for _ in range(6))
    papers = {
        str(rct): _paper(),
        str(review): _paper(family="review", level=0),
        str(other_disease): _paper(scope="target_level"),
        str(trial3): _paper(family="phase3_rct", level=3),
        str(neighbour): _paper(scope="related"),
        str(unclassified): _paper(family="other", level=0),
    }
    kept = _claim("Patients randomised to DRUG-A had more relapses than placebo.", rct)
    phase3 = _claim("In the phase 3 trial, DRUG-B did not meet its primary endpoint.", trial3)
    named = _claim("In disease one, DRUG-A did not meet its primary endpoint.", neighbour)
    no_type = _claim("Patients randomised to DRUG-B had more relapses.", unclassified)
    claims = [
        kept,
        phase3,
        named,
        no_type,
        _claim("DRUG-A is a fusion protein.", rct),  # no trial reported
        _claim("Patients randomised to DRUG-C improved.", rct),  # not a drug of the target
        _claim("DRUG-A trials were reviewed.", review),  # a review
        _claim("DRUG-A failed in a randomised trial.", other_disease),  # not this indication
        # A neighbouring disease's paper, reporting its own trial.
        _claim("DRUG-A met its primary endpoint in a randomised trial.", neighbour),
        _claim("DRUG-A failed in a randomised trial.", kind=EvidenceType.CLINICAL_TRIAL),
    ]
    scope = build_disease_scope("disease one")

    assert po.candidate_claims(claims, _DRUGS, papers, scope) == [phase3, kept, named, no_type]


def test_answers_become_publication_outcomes_per_drug() -> None:
    claim = _claim("In a randomised trial of 168 patients, DRUG-A increased exacerbations.")
    answers = {
        str(claim.evidence_id): {
            "drug": "drug-a",
            "phase": "unknown",
            "design": "randomised_controlled",
            "enrollment": 168,
            "result": "worsened",
        }
    }

    out = po.to_outcomes(answers, [claim], _DRUGS)

    [o] = out["DRUG-A"]
    assert (o.result, o.phase, o.randomised, o.enrollment, o.stage) == (
        "worsened",
        0.0,
        True,
        168,
        2.0,
    )
    assert o.excerpt == claim.claim_text


@pytest.mark.parametrize(
    "answer",
    [
        {"drug": "DRUG-A", "result": "unknown"},  # nothing stated
        {"drug": "DRUG-C", "result": "not_met"},  # not a drug of the target
        {"drug": "DRUG-B", "result": "not_met"},  # a drug the claim does not name
    ],
)
def test_unchecked_answers_are_dropped(answer: dict[str, Any]) -> None:
    claim = _claim("DRUG-A did not meet its primary endpoint versus DRUG-C.")

    assert po.to_outcomes({str(claim.evidence_id): answer}, [claim], _DRUGS) == {}


async def test_answers_for_claims_the_batch_did_not_show_are_dropped(monkeypatch) -> None:
    shown, hidden = _claim("DRUG-A trial: not met."), _claim("DRUG-B trial: not met.")
    result = QuestionResult(
        question_id="publication_outcome",
        answer={
            "claims": [
                {"claim_id": str(shown.evidence_id), "drug": "DRUG-A", "result": "not_met"},
                {"claim_id": str(hidden.evidence_id), "drug": "DRUG-B", "result": "not_met"},
                {"claim_id": "made-up", "drug": "DRUG-A", "result": "not_met"},
            ]
        },
        shown_claim_ids=[str(shown.evidence_id)],
    )
    monkeypatch.setattr(po, "ask", AsyncMock(return_value=result))

    out = await po.ask_publication_outcomes(
        [shown, hidden],
        drugs=_DRUGS,
        disease="disease one",
        ctx=None,  # type: ignore[arg-type]
        classification=DataClass.NON_SENSITIVE,
    )

    assert list(out) == [str(shown.evidence_id)]


async def test_a_failed_batch_leaves_its_claims_unanswered(monkeypatch) -> None:
    monkeypatch.setattr(po, "ask", AsyncMock(side_effect=RuntimeError("model down")))

    out = await po.ask_publication_outcomes(
        [_claim("DRUG-A trial: not met.")],
        drugs=_DRUGS,
        disease="disease one",
        ctx=None,  # type: ignore[arg-type]
        classification=DataClass.NON_SENSITIVE,
    )

    assert out == {}


def test_the_question_is_given_the_drugs_and_the_indication() -> None:
    block = po.facts_block(_DRUGS, "disease one")

    assert block == "Indication: disease one\nDrugs acting on the target: drug-a, drug-b"
