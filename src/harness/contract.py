# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pydantic import BaseModel

from core.exceptions import ContractViolation
from schemas.messages import AgentMessage


class AgentContract(BaseModel):
    """Declares what a single agent may consume and produce.

    The harness enforces these at every message boundary — agents never
    see fields they did not declare, and cannot emit fields they did not declare.
    """

    name: str
    consumes: set[str]  # allowed keys in task_spec (inbound)
    produces: set[str]  # allowed keys in payload dict (outbound)
    max_loops: int = 3
    skills: list[str] = []


# Agent/service split: a "service" is a folder/role label, NOT an exemption
# from governance. Any service that calls a model (claim extraction, semantic
# clustering, screening, …) is a model-op and reuses this exact contract so it
# still routes via ctx.model, gets a Langfuse span, and counts against step_budget.
ServiceContract = AgentContract


def validate_inbound(msg: AgentMessage, contract: AgentContract) -> None:
    """Raise ContractViolation if task_spec contains undeclared keys."""
    if not msg.task_spec:
        return
    extra = set(msg.task_spec.keys()) - contract.consumes
    if extra:
        raise ContractViolation(
            f"Agent {contract.name!r} received undeclared task_spec keys: {sorted(extra)}. "
            f"Declared consumes: {sorted(contract.consumes)}",
            trace_id=msg.trace_id,
        )


def validate_outbound(msg: AgentMessage, contract: AgentContract) -> None:
    """Raise ContractViolation if a dict payload contains undeclared keys."""
    if not isinstance(msg.payload, dict):
        return  # list[Evidence] and None are always valid — schema enforces them
    extra = set(msg.payload.keys()) - contract.produces
    if extra:
        raise ContractViolation(
            f"Agent {contract.name!r} emitted undeclared payload keys: {sorted(extra)}. "
            f"Declared produces: {sorted(contract.produces)}",
            trace_id=msg.trace_id,
        )
