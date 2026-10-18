# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""A trial of one of the target's known drugs is a trial of the target."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from capabilities.target_validation.workflow import _with_target_drug_trials
from core.evidence_text import is_target_drug_trial
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance


def _ev(etype: EvidenceType, source: str, extra: dict) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENE1",
        disease="disease one",
        evidence_type=etype,
        scope="abstract",
        source=source,
        source_link="https://example.com",
        provenance=Provenance(
            agent_name="t", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        classification=DataClass.NON_SENSITIVE,
        extra=extra,
    )


_STATE = {
    "opentargets_evidence": [
        _ev(EvidenceType.GENETICS, "opentargets", {"known_drugs": [{"drug_name": "DRUGANIB"}]})
    ]
}


def test_a_trial_naming_only_the_drug_becomes_a_target_trial():
    trial = _ev(
        EvidenceType.CLINICAL_TRIAL,
        "NCT00000001",
        {
            "phase": "PHASE3",
            "interventions": ["Druganib", "Placebo druganib"],
            "conditions": ["A broader vascular condition"],
            "matched_by": "gene",
        },
    )
    assert not is_target_drug_trial(trial)
    [stamped] = _with_target_drug_trials([trial], _STATE)  # type: ignore[arg-type]
    assert stamped.extra["matched_by"] == "drug:DRUGANIB"
    assert is_target_drug_trial(stamped)


def test_other_trials_and_other_evidence_are_left_alone():
    other = _ev(
        EvidenceType.CLINICAL_TRIAL,
        "NCT00000002",
        {"phase": "PHASE3", "interventions": ["Otherumab"], "matched_by": "gene"},
    )
    paper = _ev(EvidenceType.ARTICLE, "pmid:1", {"interventions": ["Druganib"]})
    out = _with_target_drug_trials([other, paper], _STATE)  # type: ignore[arg-type]
    assert out[0].extra["matched_by"] == "gene" and "matched_by" not in out[1].extra
    assert _with_target_drug_trials([other], {}) == [other]  # type: ignore[arg-type]
