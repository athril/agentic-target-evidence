# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the mTLS CN verification middleware (MP-20)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from core.a2a import server as a2a_server
from core.a2a.middleware import MTLSVerificationMiddleware
from core.a2a.server import create_app, register_handler
from schemas.messages import AgentMessage


def _make_msg(from_agent: str = "planner") -> dict:
    return {
        "schema_version": "1.0",
        "message_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "from_agent": from_agent,
        "to_agent": "literature",
        "intent": "task",
        "task_spec": {},
        "payload": None,
        "trace_id": "trace-mw",
    }


@pytest.fixture(autouse=True)
def _reset_handler():
    original = a2a_server._handler
    yield
    a2a_server._handler = original


@pytest.fixture()
def app_with_middleware():
    app = create_app()
    app.add_middleware(MTLSVerificationMiddleware)

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
    return app


def test_matching_cn_header_passes(app_with_middleware) -> None:
    client = TestClient(app_with_middleware, raise_server_exceptions=False)
    response = client.post(
        "/a2a/invoke",
        json=_make_msg(from_agent="planner"),
        headers={"X-Agent-CN": "planner"},
    )
    assert response.status_code == 200


def test_mismatched_cn_header_returns_403(app_with_middleware) -> None:
    client = TestClient(app_with_middleware, raise_server_exceptions=False)
    response = client.post(
        "/a2a/invoke",
        json=_make_msg(from_agent="planner"),
        headers={"X-Agent-CN": "rogue-agent"},
    )
    assert response.status_code == 403
    assert "does not match" in response.json()["detail"]


def test_missing_cn_header_returns_403(app_with_middleware) -> None:
    client = TestClient(app_with_middleware, raise_server_exceptions=False)
    response = client.post(
        "/a2a/invoke",
        json=_make_msg(from_agent="planner"),
        # No X-Agent-CN header, no ssl_object in scope
    )
    assert response.status_code == 403


def test_non_invoke_route_bypasses_middleware(app_with_middleware) -> None:
    """Routes other than /a2a/invoke are not checked."""
    client = TestClient(app_with_middleware, raise_server_exceptions=False)
    # The health/docs route should not be blocked by the middleware
    response = client.get("/openapi.json")
    assert response.status_code == 200


def test_invalid_json_body_returns_400(app_with_middleware) -> None:
    client = TestClient(app_with_middleware, raise_server_exceptions=False)
    response = client.post(
        "/a2a/invoke",
        content=b"not json",
        headers={"Content-Type": "application/json", "X-Agent-CN": "planner"},
    )
    assert response.status_code == 400
