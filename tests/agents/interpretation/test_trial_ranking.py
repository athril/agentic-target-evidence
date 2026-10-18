# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Trial claims reach a lens most informative first (trial_information)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from agents.interpretation._lens_base import _shown_claims, trial_information
from schemas.evidence import CoreClaim, DataClass, Direction, EvidenceType, Provenance


def _trial(text: str) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENE1",
        disease="disease one",
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="t", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        claim_text=text,
        direction=Direction.UNSPECIFIED,
        confidence=0.9,
    )


def test_a_large_completed_phase_3_outranks_small_or_running_trials():
    outcome = "PHASE3 COMPLETED clinical trial NCT00000001: Drug A, Placebo in X (n=27564)"
    small_pm = "PHASE4 RECRUITING clinical trial NCT00000002: Drug A in Y (n=120)"
    running = "PHASE3 RECRUITING clinical trial NCT00000003: Drug A in disease one (n=900)"
    early = "PHASE1 COMPLETED clinical trial NCT00000004: Drug B in volunteers (n=40)"
    result = "NCT00000001 (PHASE3 COMPLETED) primary outcome — Time to first event"
    order = sorted([small_pm, early, running, result, outcome], key=lambda t: -trial_information(t))
    assert order[:2] == [outcome, result]
    assert order.index(running) < order.index(small_pm) < order.index(early)


def test_the_lens_is_shown_the_informative_trials_when_it_cannot_see_all():
    filler = [
        _trial(f"PHASE4 NOT_YET_RECRUITING clinical trial NCT1{i:07d}: Drug A in Y (n=50)")
        for i in range(30)
    ]
    landmark = _trial("PHASE3 COMPLETED clinical trial NCT00000009: Drug A in X (n=18924)")
    shown = _shown_claims([*filler, landmark], limit=10)
    assert shown[0] is landmark
