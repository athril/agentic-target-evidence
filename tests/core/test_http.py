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
