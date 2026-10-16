# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""The effect a target's drugs have on it, and the effect the run's direction asks for.

A run tests a triple — gene, disease, direction. This module maps each drug's action type
(ChEMBL / Open Targets) to the effect it has on the target's activity (decrease /
increase) and the run's therapeutic direction to the effect it asks for, so drug, label
and genetics evidence can be read for the direction under test.

Pure module: no I/O.
"""

from __future__ import annotations

import re
from typing import Literal

Effect = Literal["decrease", "increase", "unknown"]
# Registry statuses of a trial still running.
_ONGOING = frozenset(
    {"RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"}
)

# ChEMBL / Open Targets action types by the effect the drug has on its target's activity.
# "MODULATOR", "BINDING AGENT" and the like do not say which way, so they stay unknown.
_DECREASE = frozenset(
    {
        "INHIBITOR",
        "ANTAGONIST",
        "ALLOSTERIC ANTAGONIST",
        "NEGATIVE ALLOSTERIC MODULATOR",
        "NEGATIVE MODULATOR",
        "INVERSE AGONIST",
        "BLOCKER",
        "CHANNEL BLOCKER",
        "DEGRADER",
        "ANTISENSE INHIBITOR",
        "RNAI INHIBITOR",
        "GENETIC INHIBITOR",
        "DISRUPTING AGENT",
        "SEQUESTERING AGENT",
    }
)
_INCREASE = frozenset(
    {
        "AGONIST",
        "PARTIAL AGONIST",
        "ACTIVATOR",
        "POSITIVE ALLOSTERIC MODULATOR",
        "POSITIVE MODULATOR",
        "OPENER",
        "STABILISER",
        "STABILIZER",
        "EXOGENOUS PROTEIN",
        "EXOGENOUS GENE",
        "RELEASING AGENT",
    }
)
# Requested directions by effect. "modulate"/"unspecified" have none.
_REQUESTED: dict[str, Effect] = {
    "inhibit": "decrease",
    "degrade": "decrease",
    "antagonize": "decrease",
    "knockdown": "decrease",
    "activate": "increase",
    "agonize": "increase",
    "restore": "increase",
}
_SALT_RE = re.compile(
    r"\s+(?:sodium|potassium|calcium|hydrochloride|hcl|mesylate|maleate|citrate|acetate|"
    r"sulfate|phosphate|tartrate|succinate|fumarate|besylate|tosylate|anhydrous)$",
    re.IGNORECASE,
)


def drug_effect(action_type: str | None) -> Effect:
    """The effect a drug's action type has on its target: decrease, increase or unknown."""
    at = " ".join(str(action_type or "").upper().replace("_", " ").split())
    if at in _DECREASE:
        return "decrease"
    if at in _INCREASE:
        return "increase"
    return "unknown"


def is_ongoing(trial_status: str | None) -> bool:
    """A registry status of a trial still running."""
    return str(trial_status or "").upper() in _ONGOING


def requested_effect(direction: str | None) -> Effect:
    """The effect the run's therapeutic direction asks for."""
    return _REQUESTED.get(str(direction or "").strip().lower(), "unknown")


def drug_key(name: str) -> str:
    """A drug name normalised for matching: upper case, salt suffix removed."""
    return _SALT_RE.sub("", " ".join(str(name).upper().split()))
