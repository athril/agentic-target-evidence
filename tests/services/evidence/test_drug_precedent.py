# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Drug effects on the target and requested effects (services/evidence/drug_precedent.py)."""

from __future__ import annotations

import pytest

from services.evidence.drug_precedent import drug_effect, requested_effect


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
