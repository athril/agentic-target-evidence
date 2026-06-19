# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for BaseAgent harness (MP-24)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import ContractViolation, LoopLimitExceeded
from harness.base_agent import BaseAgent
from harness.context import RunContext
from harness.contract import AgentContract
from harness.loop_guard import LoopGuard
from schemas.messages import AgentMessage

# ---------------------------------------------------------------------------
# Minimal concrete agent for testing
# ---------------------------------------------------------------------------


class EchoAgent(BaseAgent):
    contract = AgentContract(
        name="echo",
        consumes={"target_gene", "disease"},
        produces={"result"},
        max_loops=3,
    )

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"result": "ok"},
            trace_id=msg.trace_id,
        )


class BoomAgent(BaseAgent):
    contract = AgentContract(
        name="boom",
        consumes={"target_gene"},
        produces=set(),
        max_loops=2,
    )

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        raise RuntimeError("act() always fails")


def _make_msg(task_spec: dict | None = None) -> AgentMessage:
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        from_agent="planner",
        to_agent="echo",
        intent="task",
        task_spec=task_spec or {"target_gene": "BRCA1", "disease": "breast cancer"},
        trace_id="trace-base",
    )


def _make_ctx() -> RunContext:
    return RunContext(
        run_id=uuid.uuid4(),
        trace_id="trace-base",
        router=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run_returns_result_message() -> None:
    agent = EchoAgent()
    ctx = _make_ctx()
    result = await agent.run(_make_msg(), ctx)
    assert result.intent == "result"


async def test_run_rejects_extra_task_spec_keys() -> None:
    agent = EchoAgent()
    ctx = _make_ctx()
    msg = _make_msg(task_spec={"target_gene": "BRCA1", "sneaky": "value"})
    with pytest.raises(ContractViolation):
        await agent.run(msg, ctx)


async def test_run_validates_outbound() -> None:
    """If act() returns undeclared payload keys, validate_outbound raises."""

    class BadOutputAgent(BaseAgent):
        contract = AgentContract(
            name="bad_output",
            consumes={"target_gene"},
            produces={"good_key"},
            max_loops=1,
        )

        async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
            return AgentMessage(
                message_id=uuid.uuid4(),
                run_id=msg.run_id,
                from_agent=msg.to_agent,
                to_agent=msg.from_agent,
                intent="result",
                payload={"good_key": "ok", "bad_key": "forbidden"},
                trace_id=msg.trace_id,
            )

    agent = BadOutputAgent()
    ctx = _make_ctx()
    msg = _make_msg(task_spec={"target_gene": "BRCA1"})
    with pytest.raises(ContractViolation, match="bad_key"):
        await agent.run(msg, ctx)


async def test_run_respects_loop_guard() -> None:
    agent = EchoAgent()
    ctx = _make_ctx()
    guard = LoopGuard(step_budget=10)

    # First call: succeeds
    await agent.run(_make_msg(), ctx, loop_guard=guard, edge_key="echo_loop")

    # Exhaust the per-edge counter
    await agent.run(_make_msg(), ctx, loop_guard=guard, edge_key="echo_loop")
    await agent.run(_make_msg(), ctx, loop_guard=guard, edge_key="echo_loop")
    with pytest.raises(LoopLimitExceeded):
        await agent.run(_make_msg(), ctx, loop_guard=guard, edge_key="echo_loop")


async def test_run_wraps_act_in_telemetry_span() -> None:
    agent = EchoAgent()
    ctx = _make_ctx()
    with patch("harness.base_agent.span") as mock_span:
        mock_span.return_value.__aenter__ = MagicMock(return_value=MagicMock())
        mock_span.return_value.__aexit__ = MagicMock(return_value=False)
        # The context manager must be async
        import contextlib

        @contextlib.asynccontextmanager
        async def fake_span(*a, **kw):
            yield MagicMock()

        mock_span.side_effect = fake_span
        await agent.run(_make_msg(), ctx)

    mock_span.assert_called_once_with(
        "echo",
        trace_id="trace-base",
        input_data='{"target_gene": "BRCA1", "disease": "breast cancer"}',
    )


async def test_base_agent_is_abstract() -> None:
    """BaseAgent cannot be instantiated without implementing act()."""
    with pytest.raises(TypeError):
        BaseAgent()  # type: ignore[abstract]
