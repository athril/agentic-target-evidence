# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from schemas.evidence import DataClass, Evidence
from schemas.messages import AgentMessage

_SENSITIVE_AGENTS = {"internal_data"}


def classify(payload: AgentMessage | Evidence | list[Evidence]) -> DataClass:
    """Derive the DataClass for a payload without calling an LLM.

    Rules (in priority order):
    1. If the source agent is in _SENSITIVE_AGENTS → SENSITIVE.
    2. If any Evidence item in the payload is already tagged SENSITIVE → SENSITIVE.
    3. Otherwise → NON_SENSITIVE.
    """
    if isinstance(payload, AgentMessage):
        if payload.from_agent in _SENSITIVE_AGENTS:
            return DataClass.SENSITIVE
        evidences: list[Evidence] = []
        if isinstance(payload.payload, list):
            evidences = [e for e in payload.payload if isinstance(e, Evidence)]
        return classify(evidences) if evidences else DataClass.NON_SENSITIVE

    if isinstance(payload, Evidence):
        return payload.classification

    # list[Evidence]
    if any(e.classification == DataClass.SENSITIVE for e in payload):
        return DataClass.SENSITIVE
    return DataClass.NON_SENSITIVE
