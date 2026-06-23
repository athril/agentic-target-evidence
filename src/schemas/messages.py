# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .evidence import Evidence


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    message_id: UUID
    run_id: UUID
    from_agent: str
    to_agent: str
    intent: Literal["task", "result", "error", "handoff"]
    task_spec: dict[str, Any] | None = None
    payload: list[Evidence] | dict[str, Any] | None = None
    trace_id: str
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def error_reply(self, detail: str) -> AgentMessage:
        return AgentMessage(
            message_id=_uuid.uuid4(),
            run_id=self.run_id,
            from_agent=self.to_agent,
            to_agent=self.from_agent,
            intent="error",
            task_spec={"detail": detail},
            payload=None,
            trace_id=self.trace_id,
        )
