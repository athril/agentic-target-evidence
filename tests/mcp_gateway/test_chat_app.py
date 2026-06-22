# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Gene Target Validation Assistant (chat client) helpers.

Covers the pure helper logic: bearer-header construction from MCP_GATEWAY_TOKEN, the
CHAT_AUTH login callback (plaintext and bcrypt secrets), and the per-turn tool-call
rendering shown in the chat UI.
"""

from __future__ import annotations

import bcrypt
import pytest
from langchain_core.messages import AIMessage, ToolMessage

# chat_app imports the optional `chat` dependency group (gradio, langchain-ollama), which is
# not installed in the default test environment. Skip this module unless it's present.
pytest.importorskip("gradio")
pytest.importorskip("langchain_ollama")

from mcp_gateway.chat_app import (  # noqa: E402  (after importorskip by design)
    _gateway_connection,
    _load_auth,
    _log_tool_detail,
    _tools_called_line,
)

# ── Gateway connection / bearer header ───────────────────────────────────────


def test_gateway_connection_has_no_header_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_GATEWAY_TOKEN", raising=False)
    conn = _gateway_connection()
    assert conn["transport"] == "streamable_http"
    assert "headers" not in conn


def test_gateway_connection_sends_bearer_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "s3cret-token")
    conn = _gateway_connection()
    assert conn["headers"] == {"Authorization": "Bearer s3cret-token"}


def test_gateway_connection_ignores_blank_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "   ")
    assert "headers" not in _gateway_connection()


# ── CHAT_AUTH login callback ─────────────────────────────────────────────────


def test_load_auth_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAT_AUTH", raising=False)
    assert _load_auth() is None


def test_load_auth_plaintext_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_AUTH", "alice:secret,bob:hunter2")
    check = _load_auth()
    assert check is not None
    assert check("alice", "secret") is True
    assert check("bob", "hunter2") is True
    assert check("alice", "wrong") is False
    assert check("carol", "anything") is False


def test_load_auth_bcrypt_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    hashed = bcrypt.hashpw(b"hunter2", bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setenv("CHAT_AUTH", f"bob:{hashed}")
    check = _load_auth()
    assert check is not None
    assert check("bob", "hunter2") is True
    assert check("bob", "wrong") is False


# ── Tool-call rendering ──────────────────────────────────────────────────────


def test_tools_called_line_lists_names() -> None:
    line = _tools_called_line(["depmap_get_dependency", "hgnc_resolve_symbol"])
    assert line.startswith("**Tools called:**")
    assert "`depmap_get_dependency`" in line
    assert "`hgnc_resolve_symbol`" in line


def test_log_tool_detail_pairs_calls_with_results(caplog: pytest.LogCaptureFixture) -> None:
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "c1", "name": "depmap_get_dependency", "args": {"gene_symbol": "TP53"}}],
    )
    tool = ToolMessage(content="essential", tool_call_id="c1")
    with caplog.at_level("INFO", logger="mcp_gateway.chat_app"):
        _log_tool_detail([ai, tool])
    logged = "\n".join(r.message for r in caplog.records)
    assert "depmap_get_dependency" in logged
    assert "TP53" in logged
    assert "essential" in logged


def test_log_tool_detail_marks_missing_result(caplog: pytest.LogCaptureFixture) -> None:
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "c9", "name": "hgnc_resolve_symbol", "args": {"symbol": "X"}}],
    )
    with caplog.at_level("INFO", logger="mcp_gateway.chat_app"):
        _log_tool_detail([ai])
    assert "(no result)" in "\n".join(r.message for r in caplog.records)


def test_log_tool_detail_silent_without_calls(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO", logger="mcp_gateway.chat_app"):
        _log_tool_detail([AIMessage(content="just an answer")])
    assert not any("tools called" in r.message for r in caplog.records)
