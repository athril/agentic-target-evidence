# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for all service modules."""

from __future__ import annotations

from datetime import UTC, datetime

from schemas.evidence import Provenance


def make_provenance(service_name: str, tool_name: str, trace_id: str) -> Provenance:
    return Provenance(
        agent_name=service_name,
        tool_name=tool_name,
        timestamp=datetime.now(UTC),
        trace_id=trace_id,
    )
