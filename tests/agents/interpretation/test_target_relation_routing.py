# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Drug records reach a lens only by their relation to the target.

The failure these prevent: the openFDA text search returns labels that merely mention the
gene or the disease (a contrast agent, an indication drug acting on other proteins). Their
label and adverse-event claims reached the safety lens, which read them as the target's
liabilities. Open Targets pharmacogenomic rows (a variant changing a drug's response) were
read the same way.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from agents.interpretation._lens_base import claim_matches_lens
from capabilities.target_validation.workflow import _faers_safety_summary
from core.evidence_text import target_relation
from schemas.evidence import CoreClaim, DataClass, Evidence, EvidenceType, Provenance
from services.evidence.liabilities import target_safety

_PROV = Provenance(agent_name="t", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t")


def _claim(relation: str | None) -> CoreClaim:
    return CoreClaim(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENEX",
        disease="disease one",
        evidence_type=EvidenceType.REGULATORY,
        claim_text="DRUG-A label carries a boxed warning: hepatotoxicity",
        target_relation=relation,
        provenance=_PROV,
        classification=DataClass.NON_SENSITIVE,
    )


def _faers(relation: str | None) -> Evidence:
    extra = {"drug_name": "DRUG-A", "total_reports": 120, "top_reactions": []}
    if relation:
        extra["target_relation"] = relation
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENEX",
        disease="disease one",
        evidence_type=EvidenceType.REGULATORY,
        scope="abstract",
        source="fda:faers:DRUG-A",
        source_link="https://api.fda.gov/drug/event.json",
        classification=DataClass.NON_SENSITIVE,
        provenance=_PROV,
        extra=extra,
    )


@pytest.mark.parametrize(
    ("relation", "lenses"),
    [
        ("acts_on_target", {"safety", "regulatory", "commercial"}),
        ("indication", {"regulatory", "commercial"}),
        ("other", set()),
        (None, {"safety", "regulatory", "commercial"}),
    ],
)
def test_a_drug_claim_reaches_the_lenses_its_relation_allows(relation, lenses):
    claim = _claim(relation)

    reached = {
        lens for lens in ("safety", "regulatory", "commercial") if claim_matches_lens(claim, lens)
    }

    assert reached == lenses


def test_the_safety_lens_reads_adverse_event_reports_of_target_drugs_only():
    assert "DRUG-A" in _faers_safety_summary([_faers("acts_on_target")])
    assert _faers_safety_summary([_faers("indication")]) == ""
    assert target_relation(_faers("other")) == "other"
    assert target_relation(_faers(None)) is None


def test_pharmacogenomic_rows_are_not_liabilities():
    ot = {
        "safety_liabilities": [
            {"event": "cardiovascular disease events", "datasource": "ClinPGx", "effects": []},
            {
                "event": "hepatotoxicity",
                "datasource": "Brennan et al. (2024)",
                "effects": [{"direction": "Inhibition/Decrease/Downregulation"}],
            },
        ],
        "safety_liability_events": ["cardiovascular disease events", "hepatotoxicity"],
        "safety_liability_count": 2,
        "safety_text": "Open Targets safety for GENEX: 2 liability event(s).",
    }

    safety = target_safety(ot)

    assert safety["events"] == ["hepatotoxicity"]
    assert safety["count"] == 1
    assert [li["event"] for li in safety["pharmacogenomic"]] == ["cardiovascular disease events"]
    assert "cardiovascular" not in safety["text"]
    assert "1 pharmacogenomic record" in safety["text"]


def test_rows_cached_without_datasources_keep_their_events():
    ot = {"safety_liability_events": ["hepatotoxicity"], "safety_text": "t"}

    assert target_safety(ot)["events"] == ["hepatotoxicity"]
