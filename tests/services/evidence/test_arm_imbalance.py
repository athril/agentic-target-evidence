# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Per-arm imbalances of posted trial results (services/evidence/clinical_trial_interpret).

Synthetic arms shaped like a registry adverse-events module.
"""

from __future__ import annotations

from typing import Any

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


def test_serious_events_get_the_same_imbalance_as_deaths() -> None:
    imbalance = serious_imbalance(_arms((10, 120, 400), (5, 40, 400)))

    assert imbalance is not None
    assert imbalance.risk_ratio == 3.0
    assert imbalance.excess
