# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow helpers that feed deterministic facts to the lens guards.

Both were missing before: the clinical lens never received trial facts (so its
phase/status guard could not run in a real pipeline), and the dependency checks keyed
only on DepMap (so a DepMap outage silently disabled them).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from capabilities.target_validation.workflow import _dependency_reading_json, _trial_facts_json
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance


def _ev(evidence_type: EvidenceType, source: str, extra: dict) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="TRPC6",
        disease="FSGS",
        evidence_type=evidence_type,
        scope="abstract",
        source=source,
        source_link="https://example.com",
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="test", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        extra=extra,
    )


def test_trial_facts_built_from_kept_trials_only():
    facts = _trial_facts_json(
        [
            _ev(
                EvidenceType.CLINICAL_TRIAL,
                "NCT05213624",
                {"phase": "PHASE2", "status": "COMPLETED"},
            ),
            _ev(
                EvidenceType.CLINICAL_TRIAL,
                "NCT07220083",
                {"phase": "PHASE3", "status": "RECRUITING"},
            ),
            _ev(EvidenceType.ARTICLE, "PMID:1", {}),
        ]
    )
    assert [(f["nct_id"], f["phase_numbers"]) for f in facts] == [
        ("NCT05213624", [2]),
        ("NCT07220083", [3]),
    ]


def test_dependency_reading_falls_back_to_project_score():
    state = {
        "target_gene": "TRPC6",
        "functional_evidence": [
            _ev(
                EvidenceType.FUNCTIONAL_GENOMICS,
                "project_score:TRPC6",
                {"num_fitness_lines": 2, "total_lines": 512, "bf_scaled_mean": 3.485},
            )
        ],
    }
    reading = _dependency_reading_json(state)  # type: ignore[arg-type]
    assert reading is not None
    assert reading["source"] == "Project Score"
    assert reading["n_dependent"] == 2 and reading["n_total"] == 512


def test_dependency_reading_none_without_any_source():
    assert _dependency_reading_json({"target_gene": "TRPC6", "functional_evidence": []}) is None  # type: ignore[arg-type]
