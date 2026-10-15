# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""FDA labels of the target's drugs: found by name, stamped, and recognised as on-target."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from capabilities.target_validation.workflow import _current_openfda_rows
from core.evidence_text import is_on_target_fda_label, label_acts_on_target
from mcp_servers.openfda.tools import DrugLabelRecord
from schemas.evidence import DataClass, Evidence, EvidenceType, Provenance
from services.retrieval.openfda import RETRIEVAL_VERSION, _named_drugs, _stamp

_KNOWN = {
    "drugone": {
        "name": "DRUGONE",
        "action_type": "INHIBITOR",
        "approved": True,
        "in_indication": False,
    },
    "drugtwo": {
        "name": "DRUGTWO",
        "action_type": "INHIBITOR",
        "approved": True,
        "in_indication": True,
    },
    "drugthree": {
        "name": "DRUGTHREE",
        "action_type": "INHIBITOR",
        "approved": False,
        "in_indication": False,
    },
}


def _label(drug: str, moa: str = "", indication: str = "") -> DrugLabelRecord:
    return DrugLabelRecord(
        drug_name=drug, mechanism_of_action=moa, indications_and_usage=indication
    )


def _ev(extra: dict, source: str = "fda:label:BLA1", disease: str = "disease one") -> Evidence:
    return Evidence(
        evidence_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        gene="GENEX",
        disease=disease,
        evidence_type=EvidenceType.REGULATORY,
        scope="abstract",
        source=source,
        source_link="https://api.fda.gov/drug/label.json",
        classification=DataClass.NON_SENSITIVE,
        provenance=Provenance(
            agent_name="t", timestamp=datetime(2026, 1, 1, tzinfo=UTC), trace_id="t"
        ),
        extra=extra,
    )


def test_approved_target_drugs_are_fetched_indication_first() -> None:
    assert _named_drugs(_KNOWN) == ["DRUGTWO", "DRUGONE"]


def test_a_known_drug_label_is_on_target_whatever_its_moa_calls_the_target() -> None:
    """Biologic labels name the protein ("IL-17A"), not the gene symbol."""
    extra = _stamp(_label("drugone", moa="binds the GENE-X cytokine"), "GENEX", _KNOWN)
    assert extra["target_drug"] is True
    assert extra["target_action_type"] == "INHIBITOR"
    assert extra["retrieval_version"] == RETRIEVAL_VERSION
    assert label_acts_on_target(_ev(extra))


def test_an_indication_only_label_is_kept_but_not_on_target() -> None:
    """Chemotherapy approved in the indication: precedent context, not the target's label."""
    extra = _stamp(_label("othertherapy", indication="treatment of disease one"), "GENEX", _KNOWN)
    ev = _ev(extra)
    assert extra["target_drug"] is False
    assert not label_acts_on_target(ev)
    assert is_on_target_fda_label(ev)  # still kept by screening as indication precedent


def test_moa_naming_the_gene_still_counts_without_a_known_drug_match() -> None:
    extra = _stamp(_label("newdrug", moa="a GENEX antagonist"), "GENEX", {})
    assert extra["target_drug"] is True


def test_labels_from_an_older_retrieval_are_a_cache_miss() -> None:
    old = [_ev({"drug_name": "x"}), _ev({"total_reports": 3}, source="fda:faers:x")]
    assert _current_openfda_rows(old) == []
    new = _ev({"drug_name": "x", "retrieval_version": RETRIEVAL_VERSION})
    assert _current_openfda_rows([*old, new]) == [old[1], new]
