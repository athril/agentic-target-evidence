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


def describe_exception(exc: BaseException) -> str:
    """``"<Type>: <message>"``, or just the type when the message is empty.

    Timeouts and transport errors often carry no message (``str(httpx.ReadTimeout())``
    is ``""``), so a ``"%s" % exc`` log line or report note read "failed: " with no
    cause at all.
    """
    # anyio wraps a task's failure in an ExceptionGroup whose message ("unhandled errors
    # in a TaskGroup (1 sub-exception)") hides the cause; name the first leaf instead.
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
