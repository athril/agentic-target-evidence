# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the data-payload check in core.http."""

from __future__ import annotations

import httpx
import pytest

from core.exceptions import MCPToolError
from core.http import looks_like_html, require_data_payload


def test_looks_like_html_by_content_type_or_sniff():
    assert looks_like_html("text/html; charset=utf-8", b"")
    assert looks_like_html("text/plain", b"  <!DOCTYPE html><html>")
    assert not looks_like_html("text/csv", b"Gene,Dataset\n")
    assert not looks_like_html("application/json", b'{"data": []}')


def test_require_data_payload_raises_on_html_page():
    resp = httpx.Response(
        200,
        text="<!DOCTYPE html><title>DepMap — Verification</title>",
        headers={"content-type": "text/html"},
    )
    with pytest.raises(MCPToolError, match="HTML page instead of data"):
        require_data_payload(resp, "DepMap")


def test_require_data_payload_accepts_csv():
    resp = httpx.Response(200, text="Gene,Dataset\nKRAS,x\n", headers={"content-type": "text/csv"})
    require_data_payload(resp, "DepMap")


# ---------------------------------------------------------------------------
# Rate-limit retries (429, 503 + Retry-After)
# ---------------------------------------------------------------------------

import core.http as http_mod  # noqa: E402
from core.http import get_with_retry, post_with_retry  # noqa: E402


@pytest.fixture
def sleeps(monkeypatch):
    waits: list[float] = []

    async def _sleep(s: float) -> None:
        waits.append(s)

    monkeypatch.setattr(http_mod.asyncio, "sleep", _sleep)
    return waits


def _client(*responses: httpx.Response) -> tuple[httpx.AsyncClient, list[int]]:
    calls: list[int] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_429_is_retried_after_the_requested_wait(sleeps) -> None:
    client, calls = _client(
        httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={"ok": 1})
    )
    async with client:
        resp = await get_with_retry(client, "https://api.example.org/x")
    assert resp.status_code == 200
    assert len(calls) == 2
    assert 2.0 <= sleeps[0] <= 3.0  # Retry-After, with up to 50% jitter


async def test_429_without_retry_after_uses_backoff_and_returns_last(sleeps) -> None:
    client, calls = _client(httpx.Response(429))
    async with client:
        resp = await post_with_retry(client, "https://api.example.org/x", json={})
    assert resp.status_code == 429  # callers keep their own status handling
    assert len(calls) == http_mod._MAX_RETRIES
    assert len(sleeps) == http_mod._MAX_RETRIES - 1


async def test_a_wait_beyond_the_cap_fails_fast(sleeps) -> None:
    """A spent daily quota (Retry-After of hours) cannot be waited out: return at once
    instead of stalling every caller of a fan-out on capped sleeps."""
    client, calls = _client(
        httpx.Response(429, headers={"Retry-After": "76078"}), httpx.Response(200, json={})
    )
    async with client:
        resp = await get_with_retry(client, "https://api.example.org/x")
    assert resp.status_code == 429
    assert len(calls) == 1
    assert sleeps == []


async def test_503_retried_only_when_server_says_temporary(sleeps) -> None:
    client, calls = _client(httpx.Response(503))
    async with client:
        resp = await get_with_retry(client, "https://api.example.org/x")
    assert resp.status_code == 503
    assert len(calls) == 1  # a bare 503 is surfaced immediately

    client, calls = _client(
        httpx.Response(503, headers={"Retry-After": "1"}), httpx.Response(200, json={})
    )
    async with client:
        resp = await get_with_retry(client, "https://api.example.org/x")
    assert resp.status_code == 200
    assert len(calls) == 2


async def test_other_errors_are_not_retried(sleeps) -> None:
    client, calls = _client(httpx.Response(404))
    async with client:
        resp = await get_with_retry(client, "https://api.example.org/x")
    assert resp.status_code == 404
    assert len(calls) == 1
    assert sleeps == []


async def test_gateway_errors_retried_for_get_only(sleeps) -> None:
    client, calls = _client(httpx.Response(504), httpx.Response(200, json={}))
    async with client:
        resp = await get_with_retry(client, "https://api.example.org/x")
    assert resp.status_code == 200
    assert len(calls) == 2

    client, calls = _client(httpx.Response(502))
    async with client:
        resp = await post_with_retry(client, "https://api.example.org/x", json={})
    assert resp.status_code == 502
    assert len(calls) == 1  # a POST may already have taken effect
