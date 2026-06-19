# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers used by all data-acquisition agents."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from schemas.evidence import Evidence, Provenance
from schemas.messages import AgentMessage


def make_provenance(agent_name: str, tool_name: str, trace_id: str) -> Provenance:
    return Provenance(
        agent_name=agent_name,
        tool_name=tool_name,
        timestamp=datetime.now(UTC),
        trace_id=trace_id,
    )


def result_msg(source_msg: AgentMessage, evidences: list[Evidence]) -> AgentMessage:
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=source_msg.run_id,
        from_agent=source_msg.to_agent,
        to_agent=source_msg.from_agent,
        intent="result",
        payload=evidences,
        trace_id=source_msg.trace_id,
    )
