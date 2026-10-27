# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Therapeutic-hypothesis precedent (services/evidence/drug_precedent.py).

Synthetic drug rows in the shape of ``KnownDrug.model_dump()``; the names are
placeholders, so the cases test the rules, not any one target.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.evidence.drug_precedent import (
    assess_precedent,
    drug_effect,
    render_precedent_block,
    requested_effect,
)

_SCOPE = frozenset({"MONDO_1", "MONDO_1A"})  # the indication and one subtype


def _row(
    drug: str,
    action: str,
    stage: float,
    *,
    disease_id: str = "MONDO_1",
    disease: str = "disease one",
    approved_here: bool = False,
    approved: bool = False,
    status: str = "COMPLETED",
    stops: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "drug_name": drug,
        "action_type": action,
        "max_phase": stage,
        "disease_id": disease_id,
        "disease_name": disease,
        "approved_in_indication": approved_here,
        "is_approved": approved or approved_here,
        "trial_status": status,
        "stopped_for_cause": stops or [],
    }


def _assess(rows: list[dict[str, Any]], direction: str) -> Any:
    return assess_precedent(rows, direction=direction, disease="disease one", scope_ids=_SCOPE)


@pytest.mark.parametrize(
    ("action", "effect"),
    [
        ("INHIBITOR", "decrease"),
        ("RNAI INHIBITOR", "decrease"),
        ("DEGRADER", "decrease"),
        ("ANTAGONIST", "decrease"),
        ("AGONIST", "increase"),
        ("POSITIVE ALLOSTERIC MODULATOR", "increase"),
        ("STABILISER", "increase"),
        ("MODULATOR", "unknown"),
        ("BINDING AGENT", "unknown"),
        ("", "unknown"),
    ],
)
def test_drug_effect(action: str, effect: str) -> None:
    assert drug_effect(action) == effect


def test_requested_effect_groups_inhibit_and_degrade() -> None:
    assert requested_effect("inhibit") == requested_effect("degrade") == "decrease"
    assert requested_effect("activate") == "increase"
    assert requested_effect("modulate") == requested_effect("unspecified") == "unknown"


def test_same_direction_approval_in_the_indication_validates() -> None:
    r = _assess([_row("DRUGA", "INHIBITOR", 4, approved_here=True)], "inhibit")
    assert r.status == "validated"
    assert r.concordant_approved == ["DRUGA"]


def test_degrade_is_validated_by_an_approved_antagonist() -> None:
    r = _assess([_row("DRUGA", "ANTAGONIST", 4, approved_here=True)], "degrade")
    assert r.status == "validated"


def test_opposite_direction_approval_opposes_the_hypothesis() -> None:
    """Approved inhibitors in the indication are evidence against activation."""
    rows = [_row("DRUGA", "INHIBITOR", 4, approved_here=True), _row("DRUGB", "INHIBITOR", 3)]
    r = _assess(rows, "activate")
    assert r.status == "opposed"
    assert r.discordant_approved == ["DRUGA"]
    assert "AGAINST 'activate'" in r.summary


def test_opposite_direction_does_not_oppose_when_the_requested_one_is_in_phase_2() -> None:
    rows = [_row("DRUGA", "INHIBITOR", 3), _row("DRUGB", "AGONIST", 2)]
    assert _assess(rows, "activate").status == "in_clinic"


def test_futility_stop_in_the_indication_fails_the_hypothesis() -> None:
    stop = "nct1 (phase 2, terminated): futility criterion was met [Negative]"
    rows = [
        _row("DRUGA", "INHIBITOR", 2, status="TERMINATED", stops=[stop]),
        # Approved in another indication: druggable, not validated here.
        _row("DRUGA", "INHIBITOR", 4, disease_id="MONDO_9", disease="other", approved_here=True),
    ]
    r = _assess(rows, "inhibit")
    assert r.status == "failed"
    assert r.failures == [stop]
    assert "futility" in render_precedent_block(r)


def test_approval_outranks_a_failed_trial_of_another_drug() -> None:
    rows = [
        _row("DRUGA", "DEGRADER", 3, status="TERMINATED", stops=["nct2 [Negative]"]),
        _row("DRUGB", "DEGRADER", 4, approved_here=True),
    ]
    assert _assess(rows, "degrade").status == "validated"


def test_running_phase_3_outranks_an_earlier_failure() -> None:
    rows = [
        _row("DRUGA", "INHIBITOR", 2, status="TERMINATED", stops=["nct3 [Negative]"]),
        _row("DRUGB", "INHIBITOR", 3, status="RECRUITING"),
    ]
    assert _assess(rows, "inhibit").status == "late_stage"


def test_finished_phase_3_without_approval_is_stalled() -> None:
    assert _assess([_row("DRUGA", "INHIBITOR", 3)], "inhibit").status == "stalled"


def test_subtype_rows_count_and_other_diseases_do_not() -> None:
    rows = [
        _row("DRUGA", "INHIBITOR", 4, disease_id="MONDO_1A", disease="subtype", approved_here=True)
    ]
    assert _assess(rows, "inhibit").status == "validated"
    other = [_row("DRUGA", "INHIBITOR", 4, disease_id="MONDO_9", disease="x", approved_here=True)]
    r = _assess(other, "inhibit")
    assert r.status == "untested"
    assert r.concordant_elsewhere == ["DRUGA"]


def test_unknown_action_types_never_decide() -> None:
    r = _assess([_row("DRUGA", "MODULATOR", 4, approved_here=True)], "inhibit")
    assert r.status == "untested"
    assert [d.drug for d in r.unclassified] == ["DRUGA"]


def test_undetermined_without_a_directional_request() -> None:
    r = _assess([_row("DRUGA", "INHIBITOR", 4, approved_here=True)], "modulate")
    assert r.status == "undetermined"


def test_a_combination_product_names_each_ingredient():
    from services.evidence.drug_precedent import ingredient_keys, names_drug

    assert {"ELEXACAFTOR", "TEZACAFTOR", "IVACAFTOR"} <= ingredient_keys(
        "elexacaftor, tezacaftor and ivacaftor"
    )
    assert names_drug("lumacaftor/ivacaftor", {"IVACAFTOR"})
    assert not names_drug("dornase alfa", {"IVACAFTOR"})
