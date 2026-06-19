# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import ssl

import httpx
from pydantic import ValidationError

from schemas.messages import AgentMessage


class A2AError(Exception):
    """Raised when an A2A call returns a non-200 response or fails to parse."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _build_ssl_context() -> ssl.SSLContext | None:
    """Return an SSL context using the agent cert/key if env vars are set.

    httpx's `verify=` accepts a stdlib `ssl.SSLContext`, not an httpx-native type.
    """
    cert_path = os.environ.get("AGENT_CERT_PATH")
    key_path = os.environ.get("AGENT_KEY_PATH")
    ca_path = os.environ.get("AGENT_CA_PATH")

    if not (cert_path and key_path):
        return None

    ctx = ssl.create_default_context(cafile=ca_path) if ca_path else ssl.create_default_context()
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return ctx


_UNSET = object()


class A2AClient:
    """Async HTTP client for calling A2A agent endpoints over mTLS."""

    def __init__(self, *, ssl_context: ssl.SSLContext | None = _UNSET) -> None:  # type: ignore[assignment]
        self._ssl = ssl_context if ssl_context is not _UNSET else _build_ssl_context()

    async def invoke(self, msg: AgentMessage, url: str) -> AgentMessage:
        """POST msg to url/a2a/invoke and return the parsed AgentMessage reply.

        Raises A2AError if the response is non-200 or the body cannot be parsed
        as a valid AgentMessage.
        """
        # Ensure the endpoint path is present
        endpoint = url if url.endswith("/a2a/invoke") else url.rstrip("/") + "/a2a/invoke"

        async with httpx.AsyncClient(
            verify=self._ssl if self._ssl is not None else True,
            timeout=30.0,
        ) as client:
            response = await client.post(
                endpoint,
                content=msg.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )

        if response.status_code != 200:
            try:
                error_msg = AgentMessage.model_validate_json(response.text)
                detail = (error_msg.task_spec or {}).get("detail", response.text)
            except (ValidationError, Exception):
                detail = response.text
            raise A2AError(
                f"A2A call to {url!r} failed with HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
            )

        try:
            return AgentMessage.model_validate_json(response.text)
        except ValidationError as exc:
            raise A2AError(
                f"A2A response from {url!r} could not be parsed as AgentMessage: {exc}"
            ) from exc
