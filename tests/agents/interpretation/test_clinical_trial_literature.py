# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Literature about a registry trial's drug supports the clinical axes by rule."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from agents.interpretation._lens_confidence import score_verdict
from agents.interpretation.clinical_lens.agent import _trial_literature
from schemas.evidence import CoreClaim, DataClass, EvidenceType, Provenance
from schemas.verdicts import AxisVerdict, LensVerdict
from services.evidence.clinical_trial_interpret import TrialFact

_RUN = uuid.uuid4()
_FACTS = [TrialFact(nct_id="NCT05213624", interventions=("BI 764198", "Placebo"))]


def _claim(text: str, et: EvidenceType = EvidenceType.ARTICLE) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        source_evidence_id=uuid.uuid4(),
        run_id=_RUN,
        gene="TRPC6",
        disease="FSGS",
        evidence_type=et,
        claim_text=text,
        provenance=Provenance(
            agent_name="test", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        classification=DataClass.NON_SENSITIVE,
    )


_PHASE2 = _claim("BI 764198, a TRPC6 inhibitor, showed proteinuria response in FSGS patients.")
_PHASE1 = _claim("BI 764198 was well tolerated in Phase 1 trials and could be taken with food.")
_MOUSE = _claim("TRPC6 knockout in mouse models significantly improved proteinuria.")


def _verdict(validation: bool | None = True) -> LensVerdict:
    return LensVerdict(
        run_id=_RUN,
        trace_id="t",
        lens="clinical",
        target_gene="TRPC6",
        disease="FSGS",
        overall_verdict="support",
        axes=[
            AxisVerdict(axis="clinical_precedent", verdict=True),
            AxisVerdict(axis="clinical_validation", verdict=validation),
        ],
    )


def _ids(*claims: CoreClaim) -> list[str]:
    return [str(c.evidence_id) for c in claims]


def test_trial_drug_papers_attach_to_the_axis_they_report_on():
    out = _trial_literature(_FACTS)(_verdict(), [_PHASE2, _PHASE1, _MOUSE])
    precedent, validation = out.axes
    assert precedent.literature_picks == _ids(_PHASE1)
    assert validation.literature_picks == _ids(_PHASE2)
    assert str(_MOUSE.evidence_id) not in validation.supporting_claim_ids


def test_attached_papers_count_on_a_reviewed_axis():
    """A reviewed axis scores only its literature picks: a rule pick must be one of them."""
    attached = _trial_literature(_FACTS)(_verdict(), [_PHASE2])
    reviewed = attached.model_copy(
        update={"axes": [a.model_copy(update={"literature_reviewed": True}) for a in attached.axes]}
    )
    out = score_verdict(reviewed, [_PHASE2], lambda _c: 1.0)
    assert out.axes[1].confidence > 0


def test_unfavourable_or_drugless_runs_attach_nothing():
    unfavourable = _trial_literature(_FACTS)(_verdict(validation=False), [_PHASE2])
    assert unfavourable.axes[1].supporting_claim_ids == []
    verdict = _verdict()
    assert _trial_literature([])(verdict, [_PHASE2]) is verdict
