# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""HTTP helpers for MCP server tools: retries on transient errors, and payload checks."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from core.exceptions import MCPToolError

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0  # seconds; multiplied by attempt number (1s, 2s, 3s)
# Longest server-requested wait honoured before retrying a rate-limited request. A longer
# Retry-After (a spent daily quota, hours away) is not retried at all: waiting part of it
# cannot succeed and would stall every caller of a fan-out.
_MAX_RETRY_AFTER_S = 30.0


_RETRYABLE_EXCEPTIONS = (httpx.RemoteProtocolError, httpx.TimeoutException)


# Gateway errors from a proxy in front of the API: transient, and safe to retry for an
# idempotent GET (never for a POST, which may already have taken effect).
_GATEWAY_ERRORS = frozenset({502, 504})


def _retry_after(resp: httpx.Response, method: str = "GET") -> float | None:
    """Seconds a retryable response asks the client to wait, or None if it is not one.

    Every 429 is retryable (the server is shedding a burst, e.g. a concurrent fan-out);
    a 503 only when it carries ``Retry-After``, i.e. the server says it is temporary —
    a bare 503 is surfaced to the caller unchanged; a 502/504 gateway error only on GET.
    """
    header = (resp.headers.get("Retry-After") or "").strip()
    try:
        wait = float(header) if header else None
    except ValueError:  # an HTTP-date: fall back to the backoff schedule
        wait = None
    if resp.status_code == 429:
        return wait if wait is not None else 0.0
    if resp.status_code == 503 and header:
        return wait if wait is not None else 0.0
    if resp.status_code in _GATEWAY_ERRORS and method == "GET":
        return 0.0
    return None


async def _send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]], method: str, url: str
) -> httpx.Response:
    """Send with retries on transient transport errors and on rate limiting.

    A rate-limited (429, or 503 with ``Retry-After``) response is retried after the
    server's ``Retry-After`` or the backoff schedule, with jitter so a burst of callers
    does not retry in lockstep; the last response is returned when retries run out, or
    at once when the server asks for a wait longer than ``_MAX_RETRY_AFTER_S``, so
    callers keep their own status handling.
    """
    for attempt in range(_MAX_RETRIES):
        last = attempt == _MAX_RETRIES - 1
        try:
            resp = await send()
        except _RETRYABLE_EXCEPTIONS as exc:
            if last:
                raise
            wait = _RETRY_BACKOFF * (attempt + 1)
            logger.debug(
                "%s on %s %s, retry %d/%d in %.1fs",
                type(exc).__name__,
                method,
                url,
                attempt + 1,
                _MAX_RETRIES,
                wait,
            )
            await asyncio.sleep(wait)
            continue
        requested = _retry_after(resp, method)
        if requested is None or last or requested > _MAX_RETRY_AFTER_S:
            return resp
        wait = max(requested, _RETRY_BACKOFF * (attempt + 1))
        wait *= random.uniform(1.0, 1.5)
        logger.debug(
            "HTTP %d on %s %s, retry %d/%d in %.1fs",
            resp.status_code,
            method,
            url,
            attempt + 1,
            _MAX_RETRIES,
            wait,
        )
        await asyncio.sleep(wait)
    raise RuntimeError("unreachable")


async def get_with_retry(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    """GET with retries on transient connection errors, rate limiting and gateway errors."""
    return await _send_with_retry(lambda: client.get(url, **kwargs), "GET", url)


async def post_with_retry(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    """POST with retries on transient connection errors and rate limiting (429)."""
    return await _send_with_retry(lambda: client.post(url, **kwargs), "POST", url)


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
