# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""HTTP helpers for MCP server tools: retries on transient errors, and payload checks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from core.exceptions import MCPToolError

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0  # seconds; multiplied by attempt number (1s, 2s, 3s)


_RETRYABLE_EXCEPTIONS = (httpx.RemoteProtocolError, httpx.TimeoutException)


async def get_with_retry(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
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


async def post_with_retry(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
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


_HTML_SNIFF = (b"<!doctype html", b"<html")


def looks_like_html(content_type: str, head: bytes) -> bool:
    """True when a response declared or sniffed as HTML — a page, not a data payload."""
    if "html" in (content_type or "").lower():
        return True
    return head.lstrip()[:15].lower().startswith(_HTML_SNIFF)


def require_data_payload(resp: httpx.Response, source: str) -> None:
    """Raise MCPToolError when a data endpoint answered HTTP 200 with an HTML page.

    Bot-verification interstitials (e.g. Cloudflare "Verification" pages) and login
    or maintenance pages come back as 200 + HTML. Parsed as CSV they yield zero rows,
    which downstream code cannot tell apart from "the gene has no data" — the failure
    must surface as a tool error instead.
    """
    if looks_like_html(resp.headers.get("content-type", ""), resp.content[:64]):
        raise MCPToolError(
            f"{source} returned an HTML page instead of data (HTTP {resp.status_code}); "
            "the endpoint is likely behind a bot-verification or maintenance page"
        )
