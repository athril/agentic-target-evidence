# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""MCP gateway: composes every public ``mcp_servers/*`` FastMCP instance into one server.

This is a *second, additive* exposure of the existing per-source connectors for external
MCP hosts (Claude Desktop, Claude Code, other agents) that want to call them ad hoc, outside
a full validation run. The pipeline itself never imports this module — agents call
``mcp_servers/*/tools.py`` functions directly (see docs/components.md and
docs/mcp_gateway.md).

Sources are discovered by walking ``mcp_servers/`` and importing each ``<name>.server``
module's ``mcp`` instance, rather than a hand-maintained list — adding a 24th public source
needs no change here. The one hard rule, enforced below rather than just documented: a
source classified SENSITIVE (``core.routing.classify._SENSITIVE_AGENTS``) is refused even if
it grows a ``server.py`` in the future. Today only ``internal_data`` is SENSITIVE, and it has
no ``server.py`` at all (deleted — see docs/mcp_gateway.md "Security"), so this check is
defense-in-depth, not the only thing standing between proprietary data and this gateway.
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import time
from collections.abc import Callable
from typing import Literal, cast

import mcp.types as mt
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

from core.routing.classify import _SENSITIVE_AGENTS

load_dotenv()

logger = logging.getLogger(__name__)

_GATEWAY_NAME = "agentic-target-validation"

# Optional hygiene gates: skip mounting a source's tools when its own feature flag is off,
# so a connected client doesn't see tools that would just return "disabled" on every call.
# Calling the tool directly always remains safe either way — tools.py enforces the flag
# itself; this is purely about not cluttering tool-list. Keyed by mcp_servers folder name.
_OPTIONAL_GATES: dict[str, Callable[[], bool]] = {}


def _load_optional_gates() -> dict[str, Callable[[], bool]]:
    """Best-effort import of each gated source's own enabled-check.

    Falls back to "always mount" for a source if its tools module doesn't expose the
    function under the expected name — discovery hygiene degrading gracefully is fine;
    silently exposing SENSITIVE data is not (that path is blocked in `_discover_public_servers`
    regardless of this dict).
    """
    gates: dict[str, Callable[[], bool]] = {}
    try:
        from mcp_servers.omim.tools import _enabled as omim_enabled

        gates["omim"] = omim_enabled
    except ImportError:
        logger.debug("omim enabled-check unavailable; will always mount if present")
    try:
        from mcp_servers.scimago.tools import _sjr_enabled as scimago_enabled

        gates["scimago"] = scimago_enabled
    except ImportError:
        logger.debug("scimago enabled-check unavailable; will always mount if present")
    try:
        from mcp_servers.ttd.tools import _enabled as ttd_enabled

        gates["ttd"] = ttd_enabled
    except ImportError:
        logger.debug("ttd enabled-check unavailable; will always mount if present")
    return gates


_API_KEY_REQUIREMENTS: dict[str, str] = {
    "uspto": "USPTO_API_KEY",
    "omim": "OMIM_API_KEY",
}


def _discover_public_servers() -> dict[str, FastMCP]:
    """Import every ``mcp_servers.<name>.server`` module and collect its ``mcp`` instance.

    Raises if a SENSITIVE-classified name ever actually has an importable ``server.py`` —
    this is the fail-closed check described in the module docstring. A SENSITIVE name with
    *no* ``server.py`` (today's state for ``internal_data``) is silently skipped like any
    other source without one; the raise only guards against one being added later.
    """
    import mcp_servers

    gates = _load_optional_gates()
    servers: dict[str, FastMCP] = {}
    for module_info in pkgutil.iter_modules(mcp_servers.__path__):
        name = module_info.name
        try:
            server_module = importlib.import_module(f"mcp_servers.{name}.server")
        except ModuleNotFoundError:
            continue  # no server.py for this source (e.g. internal_data) — nothing to mount

        if name in _SENSITIVE_AGENTS:
            raise RuntimeError(
                f"Refusing to start MCP gateway: '{name}' is classified SENSITIVE "
                "(core.routing.classify._SENSITIVE_AGENTS) but has an importable server.py "
                "— SENSITIVE sources must never be exposed over MCP."
            )

        gate = gates.get(name)
        if gate is not None and not gate():
            logger.info("Skipping '%s': disabled via its own feature flag", name)
            continue

        servers[name] = server_module.mcp

        env_var = _API_KEY_REQUIREMENTS.get(name)
        if env_var and not os.getenv(env_var):
            logger.warning(
                "'%s' is mounted but %s is not set — its tools will error when called.",
                name,
                env_var,
            )
    return servers


class _ToolCallLoggingMiddleware(Middleware):
    """Structured log line per tool call: name, duration, outcome.

    These calls happen outside any pipeline run, so there is no Langfuse run-context to
    attach to — plain structured logging is the right level of observability here.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name = context.message.name
        started = time.monotonic()
        try:
            result = await call_next(context)
        except Exception:
            logger.exception(
                "tool=%s duration_ms=%.1f outcome=error",
                tool_name,
                (time.monotonic() - started) * 1000,
            )
            raise
        logger.info(
            "tool=%s duration_ms=%.1f outcome=ok",
            tool_name,
            (time.monotonic() - started) * 1000,
        )
        return result


def _build_auth() -> AuthProvider | None:
    """Static bearer-token auth from MCP_GATEWAY_TOKEN, or None when unset.

    Unset means no auth — correct for the stdio persona (Claude Desktop/Code owns the
    process, a token is meaningless) and for local HTTP dev. When set, every HTTP request
    must carry ``Authorization: Bearer <token>``; this is what stops the HTTP gateway from
    being an open proxy if its port is ever bound off-loopback. Service-to-service only, so
    a single static token is enough — no OAuth issuer needed.
    """
    token = os.getenv("MCP_GATEWAY_TOKEN", "").strip()
    if not token:
        return None
    return StaticTokenVerifier(tokens={token: {"client_id": "target-evidence-chat", "scopes": []}})


def build_gateway() -> FastMCP[None]:
    """Build the composed gateway: one FastMCP instance, every public source mounted.

    Mounted without a namespace: each source's tools already carry an explicit
    source-prefixed name (e.g. ``chembl_get_chemistry``) set on their ``@mcp.tool()``
    decorator, so a tool's name is identical whether a client connects to its
    standalone server.py or to this composed gateway. Namespacing here would double
    the prefix (``chembl_chembl_get_chemistry``).
    """
    gateway: FastMCP[None] = FastMCP(_GATEWAY_NAME, auth=_build_auth())
    gateway.add_middleware(_ToolCallLoggingMiddleware())
    for _name, sub_server in sorted(_discover_public_servers().items()):
        gateway.mount(sub_server)
    return gateway


_VALID_TRANSPORTS = ("stdio", "http", "sse", "streamable-http")
_Transport = Literal["stdio", "http", "sse", "streamable-http"]


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport not in _VALID_TRANSPORTS:
        raise ValueError(f"Unknown MCP_TRANSPORT '{transport}'; must be one of {_VALID_TRANSPORTS}")
    transport = cast("_Transport", transport)

    gateway = build_gateway()
    if transport == "stdio":
        if os.getenv("MCP_GATEWAY_TOKEN", "").strip():
            logger.warning(
                "MCP_GATEWAY_TOKEN is set but ignored: bearer auth applies to HTTP transports "
                "only. Over stdio the connecting client owns the process."
            )
        gateway.run(transport="stdio")
    else:
        host = os.getenv("MCP_HOST", "127.0.0.1")
        port = int(os.getenv("MCP_PORT", "8765"))
        gateway.run(transport=transport, host=host, port=port)


if __name__ == "__main__":
    main()
