# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-drug FDA label lines carry the facts the lens could not sort out itself."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from capabilities.target_validation.workflow import _fda_label_summary
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance


def _label(drug: str, **extra) -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENE1",
        disease="disease one",
        evidence_type=EvidenceType.REGULATORY,
        scope="abstract",
        source=f"fda:label:{drug}",
        source_link="https://example.com",
        provenance=Provenance(
            agent_name="t", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        classification=DataClass.NON_SENSITIVE,
        extra={"drug_name": drug, **extra},
    )


def test_an_on_target_drug_states_its_direction_and_a_warning_about_the_indication():
    rows = [
        _label(
            "DRUG-A",
            target_drug=True,
            target_action_type="INHIBITOR",
            warnings="5.4 Disease One: exacerbations occurred in clinical trials.",
        ),
        _label("DRUG-B", target_drug=True, target_action_type="AGONIST"),
        _label("DRUG-C", boxed_warning="WARNING: SERIOUS INFECTIONS"),
    ]
    text = _fda_label_summary(rows, direction="inhibit", disease_names=["disease one"])
    a, b, c = text.splitlines()[1:]
    assert "Direction vs requested: same" in a
    assert "Warns it can cause or worsen this indication: YES" in a
    assert "Direction vs requested: OPPOSITE" in b and "Warns it can" not in b
    assert "Labelled mechanism names GENE1: no" in c and "Direction vs requested" not in c


def test_without_a_direction_or_names_the_lines_are_unchanged():
    rows = [_label("DRUG-A", target_drug=True, target_action_type="INHIBITOR")]
    assert "Direction vs requested" not in _fda_label_summary(rows)
