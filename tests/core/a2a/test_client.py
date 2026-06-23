# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the A2A client."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from core.a2a.client import A2AClient, A2AError
from schemas.messages import AgentMessage


def _sample_msg() -> AgentMessage:
    return AgentMessage(
        message_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        from_agent="planner",
        to_agent="literature",
        intent="task",
        task_spec={"target_gene": "BRCA1"},
        trace_id="trace-test",
    )


def _result_body(msg: AgentMessage) -> str:
    reply = AgentMessage(
        message_id=uuid.uuid4(),
        run_id=msg.run_id,
        from_agent=msg.to_agent,
        to_agent=msg.from_agent,
        intent="result",
        trace_id=msg.trace_id,
    )
    return reply.model_dump_json()


def _error_body(msg: AgentMessage, detail: str = "something failed") -> str:
    reply = msg.error_reply(detail)
    return reply.model_dump_json()


@respx.mock
async def test_invoke_returns_agent_message_on_200() -> None:
    msg = _sample_msg()
    respx.post("http://agent-svc/a2a/invoke").mock(
        return_value=httpx.Response(200, text=_result_body(msg))
    )

    client = A2AClient(ssl_context=None)
    result = await client.invoke(msg, "http://agent-svc")

    assert result.intent == "result"
    assert result.run_id == msg.run_id


@respx.mock
async def test_invoke_appends_invoke_path_if_missing() -> None:
    msg = _sample_msg()
    route = respx.post("http://svc/a2a/invoke").mock(
        return_value=httpx.Response(200, text=_result_body(msg))
    )

    client = A2AClient(ssl_context=None)
    await client.invoke(msg, "http://svc")

    assert route.called


@respx.mock
async def test_invoke_does_not_double_append_path() -> None:
    msg = _sample_msg()
    route = respx.post("http://svc/a2a/invoke").mock(
        return_value=httpx.Response(200, text=_result_body(msg))
    )

    client = A2AClient(ssl_context=None)
    await client.invoke(msg, "http://svc/a2a/invoke")

    assert route.called


@respx.mock
async def test_invoke_raises_a2a_error_on_400() -> None:
    msg = _sample_msg()
    respx.post("http://agent-svc/a2a/invoke").mock(
        return_value=httpx.Response(400, text=_error_body(msg))
    )

    client = A2AClient(ssl_context=None)
    with pytest.raises(A2AError) as exc_info:
        await client.invoke(msg, "http://agent-svc")

    assert exc_info.value.status_code == 400


@respx.mock
async def test_invoke_raises_a2a_error_on_503() -> None:
    msg = _sample_msg()
    respx.post("http://agent-svc/a2a/invoke").mock(
        return_value=httpx.Response(503, text=_error_body(msg, "loop_limit"))
    )

    client = A2AClient(ssl_context=None)
    with pytest.raises(A2AError) as exc_info:
        await client.invoke(msg, "http://agent-svc")

    assert exc_info.value.status_code == 503


@respx.mock
async def test_invoke_raises_a2a_error_on_invalid_response_body() -> None:
    msg = _sample_msg()
    respx.post("http://agent-svc/a2a/invoke").mock(
        return_value=httpx.Response(200, text="not json")
    )

    client = A2AClient(ssl_context=None)
    with pytest.raises(A2AError, match="could not be parsed"):
        await client.invoke(msg, "http://agent-svc")


def test_a2a_error_carries_status_code() -> None:
    err = A2AError("failed", status_code=503)
    assert err.status_code == 503
    assert "failed" in str(err)
