# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ssl
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from schemas.messages import AgentMessage

# Type alias for a registered handler coroutine
Handler = Callable[[AgentMessage], Coroutine[Any, Any, AgentMessage]]

_handler: Handler | None = None


def register_handler(fn: Handler) -> None:
    """Register the single dispatch handler for POST /a2a/invoke.

    Each agent service registers exactly one handler at startup.  Call this
    before starting uvicorn.
    """
    global _handler
    _handler = fn


def create_ssl_context(
    *,
    certfile: str,
    keyfile: str,
    cafile: str,
) -> ssl.SSLContext:
    """Build a server-side SSLContext that requires a valid client certificate."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    ctx.load_verify_locations(cafile=cafile)
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def create_app() -> FastAPI:
    """Return the FastAPI application.  Mount middleware before serving."""
    app = FastAPI(title="A2A Agent Service")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/a2a/invoke")
    async def invoke(request: Request) -> Response:
        try:
            body = await request.json()
            msg = AgentMessage.model_validate(body)
        except (ValidationError, Exception) as exc:
            error_body = {
                "schema_version": "1.0",
                "message_id": "00000000-0000-0000-0000-000000000000",
                "run_id": "00000000-0000-0000-0000-000000000000",
                "from_agent": "server",
                "to_agent": "unknown",
                "intent": "error",
                "task_spec": {"detail": str(exc)},
                "payload": None,
                "trace_id": "unknown",
            }
            return JSONResponse(status_code=400, content=error_body)

        if _handler is None:
            reply = msg.error_reply("No handler registered on this agent service")
            return JSONResponse(status_code=503, content=reply.model_dump(mode="json"))

        try:
            result = await _handler(msg)
        except Exception as exc:
            reply = msg.error_reply(str(exc))
            return JSONResponse(status_code=500, content=reply.model_dump(mode="json"))

        return JSONResponse(content=result.model_dump(mode="json"))

    return app


class A2AServer:
    """Convenience wrapper that pairs the FastAPI app with its SSL context."""

    def __init__(self, app: FastAPI, ssl_context: ssl.SSLContext | None = None) -> None:
        self.app = app
        self.ssl_context = ssl_context
