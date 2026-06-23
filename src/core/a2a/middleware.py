# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


def _extract_cn(peer_cert: dict[str, Any]) -> str | None:
    """Extract the CN value from an ssl.getpeercert() dict."""
    for field_set in peer_cert.get("subject", ()):
        for key, value in field_set:
            if key == "commonName":
                return str(value)
    return None


class MTLSVerificationMiddleware(BaseHTTPMiddleware):
    """Verify that the TLS client certificate CN matches AgentMessage.from_agent.

    Requests without a peer certificate are rejected with 403 when the route
    is /a2a/invoke.  All other routes are passed through unchanged so health
    and readiness probes continue to work.

    In test environments where TLS is not in use, inject the CN via the
    ``X-Agent-CN`` header (only honoured when no ssl_object is present in
    the ASGI scope).
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path != "/a2a/invoke":
            return await call_next(request)

        # Try to get CN from live TLS peer cert first
        ssl_object = request.scope.get("ssl_object")
        cn: str | None = None
        if ssl_object is not None:
            peer_cert = ssl_object.getpeercert()
            if not peer_cert:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "mTLS peer certificate is required"},
                )
            cn = _extract_cn(peer_cert)
        else:
            # Fallback: honour X-Agent-CN header (test environments only).
            # Production traffic always arrives over mTLS via uvicorn, which
            # populates ssl_object; this branch is never reached in prod.
            cn = request.headers.get("X-Agent-CN")

        if cn is None:
            return JSONResponse(
                status_code=403,
                content={"detail": "Could not extract CN from peer certificate"},
            )

        # Read body without consuming the stream so the route handler can also read it
        body = await request.body()
        try:
            payload = json.loads(body)
            from_agent = payload.get("from_agent", "")
        except (json.JSONDecodeError, AttributeError):
            return JSONResponse(
                status_code=400,
                content={"detail": "Request body is not valid JSON"},
            )

        if cn != from_agent:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": f"Certificate CN {cn!r} does not match from_agent {from_agent!r}"
                },
            )

        return await call_next(request)
