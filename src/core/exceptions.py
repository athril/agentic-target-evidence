# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Base exception hierarchy for the gene target validation system.

All domain exceptions inherit from GeneValidationError so callers can
catch the whole family with a single except clause when needed.
Every exception carries optional run_id and trace_id for Langfuse correlation.
"""

from __future__ import annotations

from uuid import UUID


class GeneValidationError(Exception):
    def __init__(
        self,
        message: str,
        *,
        run_id: UUID | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.trace_id = trace_id


class ContractViolation(GeneValidationError):
    """Harness: task_spec contains keys not declared in the agent contract."""


class LoopLimitExceeded(GeneValidationError):
    """Harness: per-edge loop counter or global step budget exhausted."""


class SkillNotFound(GeneValidationError):
    """Harness: requested skill markdown file does not exist."""


class MCPToolError(GeneValidationError):
    """MCP: a tool call returned an error or unexpected response."""
