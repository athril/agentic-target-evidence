# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the MCP gateway that composes every public mcp_servers/* connector.

Two invariants matter most here, more than typical test coverage:

1. `internal_data` (SENSITIVE) must never be reachable through the gateway, under any
   flag or env var — this is the regression guard the security decision in
   docs/new/mcp_gateway.md depends on being enforced, not just documented.
2. The fail-closed check in `_discover_public_servers` actually fires when a SENSITIVE
   name has an importable `server.py` — exercised here by temporarily reclassifying an
   existing public source (`pubmed`) as sensitive, since `internal_data` itself has no
   `server.py` to import and so can't exercise that branch.

Representative tool calls patch the name bound inside each `server.py` module (e.g.
`mcp_servers.ontology.server._resolve_hgnc_symbol`), not `tools.py` — `server.py` does
`from .tools import x as _x`, a separate name binding that patching `tools.py` would not
intercept. This proves the gateway wires calls through FastMCP's `mount()` correctly,
on top of the already-tested `tools.py` logic in tests/mcp_servers/.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, patch

import pytest

import mcp_gateway.server as gateway_module
from mcp_gateway.server import _build_auth, _discover_public_servers, build_gateway

# Total tools across all 25 server.py modules (grep -c '@mcp.tool()'), minus ttd
# (gated off by default and never toggled on by these tests), and each always-gated
# source's own contribution — kept as named constants so the math in each assertion
# below is legible instead of a bare magic number.
_TOTAL_PUBLIC_TOOLS = 42  # 43 server.py tools - 1 ttd (stays off-by-default here)
_OMIM_TOOLS = 1
_SCIMAGO_TOOLS = 1
_ALWAYS_ON_TOOLS = _TOTAL_PUBLIC_TOOLS - _OMIM_TOOLS - _SCIMAGO_TOOLS


@pytest.fixture(autouse=True)
def _gates_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin the default/commercial posture so tool counts below don't depend on
    # whatever OMIM_ENABLED/SCIMAGO_SJR_ENABLED happen to be set to in the ambient
    # environment (mirrors the fixture pattern in test_omim.py/test_scimago.py).
    monkeypatch.setenv("OMIM_ENABLED", "false")
    monkeypatch.setenv("SCIMAGO_SJR_ENABLED", "false")


def test_discover_excludes_internal_data() -> None:
    servers = _discover_public_servers()
    assert "internal_data" not in servers


def test_discover_returns_only_always_on_sources_when_gated_off() -> None:
    servers = _discover_public_servers()
    assert "omim" not in servers
    assert "scimago" not in servers
    assert len(servers) == 22  # 25 server.py modules minus omim, scimago, ttd


def test_discover_includes_gated_sources_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMIM_ENABLED", "true")
    monkeypatch.setenv("SCIMAGO_SJR_ENABLED", "true")
    servers = _discover_public_servers()
    assert "omim" in servers
    assert "scimago" in servers
    assert len(servers) == 24  # 25 server.py modules minus ttd (still off-by-default)


def test_discover_raises_if_a_sensitive_name_gains_a_server_py() -> None:
    # internal_data has no server.py, so it can't exercise the raise branch itself.
    # Temporarily reclassify a real public source (pubmed) as SENSITIVE instead, to
    # prove the fail-closed check actually fires when a sensitive name *does* have an
    # importable server.py — the scenario the docstring says it guards against.
    gateway_module._SENSITIVE_AGENTS.add("pubmed")
    try:
        with pytest.raises(RuntimeError, match="SENSITIVE"):
            _discover_public_servers()
    finally:
        gateway_module._SENSITIVE_AGENTS.discard("pubmed")


async def test_gateway_total_tool_count_with_gates_off() -> None:
    gw = build_gateway()
    tools = await gw.list_tools()
    assert len(tools) == _ALWAYS_ON_TOOLS


async def test_gateway_total_tool_count_with_gates_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMIM_ENABLED", "true")
    monkeypatch.setenv("SCIMAGO_SJR_ENABLED", "true")
    gw = build_gateway()
    tools = await gw.list_tools()
    assert len(tools) == _TOTAL_PUBLIC_TOOLS


async def test_gateway_never_exposes_internal_data() -> None:
    gw = build_gateway()
    tools = await gw.list_tools()
    names = [t.name for t in tools]
    assert not any("internal_data" in n for n in names)
    assert "query_internal_db" not in names
    assert "internal_data_query_internal_db" not in names


async def test_omim_toggle_changes_visible_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMIM_ENABLED", "false")
    disabled_names = {t.name for t in await build_gateway().list_tools()}
    assert not any(n.startswith("omim_") for n in disabled_names)

    monkeypatch.setenv("OMIM_ENABLED", "true")
    enabled_names = {t.name for t in await build_gateway().list_tools()}
    assert any(n.startswith("omim_") for n in enabled_names)
    assert enabled_names - disabled_names == {"omim_get_omim_validity"}


async def test_call_tool_resolves_hgnc_symbol_through_gateway() -> None:
    import mcp_servers.ontology.server as ontology_server
    from mcp_servers.ontology.tools import HGNCResult

    fake = HGNCResult(symbol="BRCA1", hgnc_id="HGNC:1100", ensembl_gene_id="ENSG00000012048")
    gw = build_gateway()
    with patch.object(ontology_server, "_resolve_hgnc_symbol", AsyncMock(return_value=fake)):
        result = await gw.call_tool("ontology_resolve_hgnc_symbol", {"symbol": "BRCA1"})
    assert result.structured_content == {
        "symbol": "BRCA1",
        "hgnc_id": "HGNC:1100",
        "ensembl_gene_id": "ENSG00000012048",
        "aliases": [],
        "previous_symbols": [],
    }


async def test_call_tool_gets_depmap_dependency_through_gateway() -> None:
    import mcp_servers.depmap.server as depmap_server
    from mcp_servers.depmap.tools import DependencyBundle

    fake = DependencyBundle(gene_symbol="PNPLA3", gene_effect_mean=-0.1, is_common_essential=False)
    gw = build_gateway()
    with patch.object(depmap_server, "_get_dependency", AsyncMock(return_value=fake)):
        result = await gw.call_tool("depmap_get_dependency", {"gene_symbol": "PNPLA3"})
    assert result.structured_content["gene_symbol"] == "PNPLA3"
    assert result.structured_content["is_common_essential"] is False


async def test_call_tool_logs_outcome(caplog: pytest.LogCaptureFixture) -> None:
    import mcp_servers.ontology.server as ontology_server
    from mcp_servers.ontology.tools import HGNCResult

    fake = HGNCResult(symbol="BRCA1")
    gw = build_gateway()
    with (
        patch.object(ontology_server, "_resolve_hgnc_symbol", AsyncMock(return_value=fake)),
        caplog.at_level(logging.INFO, logger="mcp_gateway.server"),
    ):
        await gw.call_tool("ontology_resolve_hgnc_symbol", {"symbol": "BRCA1"})
    assert any(
        "ontology_resolve_hgnc_symbol" in r.message and "outcome=ok" in r.message
        for r in caplog.records
    )


def test_uspto_missing_api_key_warns_but_still_mounts(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch.dict(os.environ, {}, clear=False),
        caplog.at_level(logging.WARNING, logger="mcp_gateway.server"),
    ):
        os.environ.pop("USPTO_API_KEY", None)
        servers = _discover_public_servers()
    assert "uspto" in servers
    assert any("USPTO_API_KEY" in r.message for r in caplog.records)


# ── Bearer auth ──────────────────────────────────────────────────────────────


def test_build_auth_returns_none_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_GATEWAY_TOKEN", raising=False)
    assert _build_auth() is None


def test_build_auth_returns_none_for_blank_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "   ")
    assert _build_auth() is None


@pytest.mark.asyncio
async def test_build_auth_verifier_accepts_only_matching_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_GATEWAY_TOKEN", "s3cret-token")
    verifier = _build_auth()
    assert verifier is not None
    assert await verifier.verify_token("s3cret-token") is not None
    assert await verifier.verify_token("wrong-token") is None
    assert await verifier.verify_token("") is None
