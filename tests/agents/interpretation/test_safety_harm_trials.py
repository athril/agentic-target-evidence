# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Posted deaths and serious adverse events per arm in the safety lens's human-harm facts.

The failure these prevent: human-harm facts listed drugs and phases only, so a class whose
phase 3 had excess deaths on the drug arm read as safe in people. Synthetic trials and
drugs; counts shaped like a registry adverse-events module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from agents.interpretation.safety_lens.questions import harm_facts
from capabilities.target_validation.workflow import _harm_trials
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance
from services.evidence.clinical_trial_interpret import serious_imbalance


def _arms(drug: tuple[int, int, int], placebo: tuple[int, int, int]) -> list[dict[str, Any]]:
    def arm(group: str, deaths: int, serious: int, n: int) -> dict[str, Any]:
        return {
            "group": group,
            "deaths": deaths,
            "deaths_at_risk": n,
            "serious": serious,
            "serious_at_risk": n,
        }

    return [arm("Drugamab 10 mg", *drug), arm("Placebo", *placebo)]


def _trial(nct: str, interventions: list[str], arm_events: list[dict[str, Any]]) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENE-A",
        disease="disease a",
        evidence_type=EvidenceType.CLINICAL_TRIAL,
        scope="full_text",
        source=nct,
        source_link="https://example.org",
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="t", tool_name="t", timestamp=datetime.now(UTC), trace_id="t"
        ),
        extra={
            "phase": "PHASE3",
            "status": "COMPLETED",
            "interventions": interventions,
            "conditions": ["Disease Z"],
            "arm_events": arm_events,
            "screening_verdict": {"verdict": "keep"},
        },
    )


def _state(trials: list[Evidence]) -> dict[str, Any]:
    ot = Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENE-A",
        disease="disease a",
        evidence_type=EvidenceType.GENETICS,
        scope="abstract",
        source="opentargets:GENE-A",
        source_link="https://example.org",
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="t", tool_name="t", timestamp=datetime.now(UTC), trace_id="t"
        ),
        extra={"known_drugs": [{"drug_name": "DRUGAMAB"}]},
    )
    return {"screened_evidence": trials, "opentargets_evidence": [ot], "disease": "disease a"}


def test_serious_events_get_the_same_imbalance_as_deaths() -> None:
    imbalance = serious_imbalance(_arms((10, 120, 400), (5, 40, 400)))

    assert imbalance is not None
    assert imbalance.risk_ratio == 3.0
    assert imbalance.excess


def test_trials_of_the_targets_drugs_with_posted_harm_come_first_with_excess_on_top() -> None:
    balanced = _trial("NCT00000001", ["Drugamab", "Placebo"], _arms((5, 40, 400), (5, 41, 400)))
    excess = _trial("NCT00000002", ["Drugamab"], _arms((60, 90, 400), (20, 60, 400)))
    other_drug = _trial("NCT00000003", ["Otherumab"], _arms((60, 90, 400), (20, 60, 400)))

    trials = _harm_trials(_state([balanced, excess, other_drug]))  # type: ignore[arg-type]

    assert [t["nct_id"] for t in trials] == ["NCT00000002", "NCT00000001"]
    assert trials[0]["drugs"] == ["drugamab"]


def test_human_harm_facts_state_the_counts_and_whether_they_differ_beyond_chance() -> None:
    excess = _trial("NCT00000002", ["Drugamab"], _arms((60, 90, 400), (20, 60, 400)))
    trials = _harm_trials(_state([excess]))  # type: ignore[arg-type]

    text = harm_facts("inhibit", {"requested": "decrease"}, trials)

    assert "NCT00000002 (drugamab, phase 3)" in text
    assert "deaths 60/400 on drug vs 20/400 on comparator" in text
    assert "higher on the drug arms beyond chance" in text
    assert harm_facts("inhibit", {"requested": "decrease"}) == harm_facts(
        "inhibit", {"requested": "decrease"}, []
    )
