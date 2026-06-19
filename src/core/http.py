# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""HTTP retry helpers for transient connection errors in MCP server tools."""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0  # seconds; multiplied by attempt number (1s, 2s, 3s)


_RETRYABLE_EXCEPTIONS = (httpx.RemoteProtocolError, httpx.TimeoutException)


async def get_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """GET with retries on transient connection errors (disconnects, timeouts)."""
    for attempt in range(_MAX_RETRIES):
        try:
            return await client.get(url, **kwargs)
        except _RETRYABLE_EXCEPTIONS as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = _RETRY_BACKOFF * (attempt + 1)
            logger.debug(
                "%s on GET %s, retry %d/%d in %.1fs",
                type(exc).__name__,
                url,
                attempt + 1,
                _MAX_RETRIES,
                wait,
            )
            await asyncio.sleep(wait)
    raise RuntimeError("unreachable")


async def post_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """POST with retries on transient connection errors (disconnects, timeouts)."""
    for attempt in range(_MAX_RETRIES):
        try:
            return await client.post(url, **kwargs)
        except _RETRYABLE_EXCEPTIONS as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = _RETRY_BACKOFF * (attempt + 1)
            logger.debug(
                "%s on POST %s, retry %d/%d in %.1fs",
                type(exc).__name__,
                url,
                attempt + 1,
                _MAX_RETRIES,
                wait,
            )
            await asyncio.sleep(wait)
    raise RuntimeError("unreachable")
