# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the A2A FastAPI server (MP-18)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from core.a2a import server as a2a_server
from core.a2a.server import create_app, register_handler
from schemas.messages import AgentMessage


def _make_msg(**overrides) -> dict:
    base = {
        "schema_version": "1.0",
        "message_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "from_agent": "planner",
        "to_agent": "literature",
        "intent": "task",
        "task_spec": {"target_gene": "BRCA1"},
        "payload": None,
        "trace_id": "trace-001",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_handler():
    """Ensure the module-level handler is clean before each test."""
    original = a2a_server._handler
    yield
    a2a_server._handler = original


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_invoke_returns_200_with_registered_handler(client: TestClient) -> None:
    async def echo(msg: AgentMessage) -> AgentMessage:
        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            trace_id=msg.trace_id,
        )

    register_handler(echo)
    response = client.post("/a2a/invoke", json=_make_msg())
    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "result"


def test_invoke_returns_400_on_invalid_body(client: TestClient) -> None:
    response = client.post("/a2a/invoke", json={"not": "a valid message"})
    assert response.status_code == 400
    data = response.json()
    assert data["intent"] == "error"
    assert "detail" in data["task_spec"]


def test_invoke_returns_503_when_no_handler_registered(client: TestClient) -> None:
    a2a_server._handler = None
    response = client.post("/a2a/invoke", json=_make_msg())
    assert response.status_code == 503
    assert response.json()["intent"] == "error"


def test_invoke_returns_500_when_handler_raises(client: TestClient) -> None:
    async def boom(msg: AgentMessage) -> AgentMessage:
        raise RuntimeError("something went wrong")

    register_handler(boom)
    response = client.post("/a2a/invoke", json=_make_msg())
    assert response.status_code == 500
    assert response.json()["intent"] == "error"


def test_invoke_propagates_run_id(client: TestClient) -> None:
    run_id = str(uuid.uuid4())

    async def reflect(msg: AgentMessage) -> AgentMessage:
        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            trace_id=msg.trace_id,
        )

    register_handler(reflect)
    response = client.post("/a2a/invoke", json=_make_msg(run_id=run_id))
    assert response.json()["run_id"] == run_id
