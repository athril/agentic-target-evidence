# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for InvestigatorAgent — bounded ReAct loop over the retrieval MCP tools."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agents.synthesis.investigator.agent import InvestigatorAgent, _build_brief, _retrieval_tools
from tests.agents.conftest import make_task_msg


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _astream_yielding(*states, stall_after: bool = False):
    """Build a fake ``react.astream`` (stream_mode='values') that yields the given cumulative
    state dicts. With ``stall_after`` it then hangs, simulating a slow step the deadline cancels.
    """

    async def _astream(*_a, **_kw):
        for state in states:
            yield state
        if stall_after:
            await asyncio.sleep(10)

    return _astream


def test_retrieval_tools_keeps_only_allow_listed_prefixes():
    tools = [
        _tool("pubmed_search"),
        _tool("gwas_catalog_get_associations"),
        _tool("expression_atlas_get_baseline"),
        _tool("internal_data_get_target_summary"),  # not gateway-reachable, but also not allow-listed
        _tool("unrelated_tool_call"),
    ]
    kept = {t.name for t in _retrieval_tools(tools)}
    assert kept == {"pubmed_search", "gwas_catalog_get_associations", "expression_atlas_get_baseline"}


def test_gateway_connection_sets_per_tool_call_timeout():
    """Each MCP tool call must be bounded so one unresponsive tool can't stall the loop."""
    from datetime import timedelta

    from agents.synthesis.investigator.agent import _MCP_TOOL_TIMEOUT_S, _gateway_connection

    conn = _gateway_connection()
    assert conn["session_kwargs"]["read_timeout_seconds"] == timedelta(seconds=_MCP_TOOL_TIMEOUT_S)
    assert conn["sse_read_timeout"] == _MCP_TOOL_TIMEOUT_S


def test_build_brief_includes_gaps_and_conflicts():
    spec = {
        "target_gene": "BRCA1",
        "disease": "breast cancer",
        "direction": "inhibit",
        "lens_summary": "- genetics: support (confidence=0.80) — strong GWAS signal",
        "agreement_map": {
            "consensus_verdict": "support",
            "conflicts": [{"description": "genetics vs safety disagree on direction"}],
        },
        "review_gaps": [{"stage": "genetics", "missing_aspects": ["No GWAS data."]}],
    }
    brief = _build_brief(spec)
    assert "BRCA1" in brief
    assert "breast cancer" in brief
    assert "inhibit" in brief
    assert "genetics vs safety disagree" in brief
    assert "No GWAS data." in brief


@pytest.fixture()
def investigator_msg(run_id, trace_id):
    return make_task_msg(
        "investigator",
        {
            "target_gene": "BRCA1",
            "disease": "breast cancer",
            "direction": "inhibit",
            "review_gaps": [{"stage": "genetics", "missing_aspects": ["No GWAS data."]}],
            "agreement_map": {"consensus_verdict": "support", "conflicts": []},
            "lens_summary": "- genetics: support (confidence=0.80) — strong GWAS signal",
        },
        run_id,
        trace_id,
    )


async def test_investigator_agent_returns_summary_and_tools_used(ctx, investigator_msg):
    fake_tools = [_tool("pubmed_search"), _tool("internal_data_get_target_summary")]
    fake_messages = [
        SimpleNamespace(type="human", content="brief"),
        SimpleNamespace(type="tool", name="pubmed_search", content="3 hits"),
        SimpleNamespace(type="ai", content="Gap closed: GWAS signal confirmed via PMID:12345."),
    ]
    fake_react = SimpleNamespace(astream=_astream_yielding({"messages": fake_messages}))

    with (
        patch("agents.synthesis.investigator.agent.MultiServerMCPClient") as mock_client_cls,
        patch("agents.synthesis.investigator.agent.ChatOllama"),
        patch(
            "agents.synthesis.investigator.agent.create_react_agent", return_value=fake_react
        ) as mock_create_react,
    ):
        mock_client = mock_client_cls.return_value
        mock_client.get_tools = AsyncMock(return_value=fake_tools)

        result = await InvestigatorAgent().run(investigator_msg, ctx)

    assert result.intent == "result"
    assert result.payload["investigation_summary"] == (
        "Gap closed: GWAS signal confirmed via PMID:12345."
    )
    assert result.payload["tools_used"] == ["pubmed_search"]

    # internal_data is not allow-listed, so create_react_agent must only see pubmed_search
    tools_passed = mock_create_react.call_args.args[1]
    assert [t.name for t in tools_passed] == ["pubmed_search"]


async def test_investigator_agent_empty_messages_yields_empty_summary(ctx, investigator_msg):
    fake_react = SimpleNamespace(astream=_astream_yielding({"messages": []}))

    with (
        patch("agents.synthesis.investigator.agent.MultiServerMCPClient") as mock_client_cls,
        patch("agents.synthesis.investigator.agent.ChatOllama"),
        patch("agents.synthesis.investigator.agent.create_react_agent", return_value=fake_react),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[])

        result = await InvestigatorAgent().run(investigator_msg, ctx)

    assert result.payload["investigation_summary"] == ""
    assert result.payload["tools_used"] == []


async def test_investigator_agent_deadline_degrades_and_preserves_tools_used(
    ctx, investigator_msg, monkeypatch
):
    """A slow ReAct loop must not hang the run: it is bounded by a wall-clock deadline and
    degrades in place to an empty summary — while still reporting which tools were called."""
    import agents.synthesis.investigator.agent as inv

    monkeypatch.setattr(inv, "_INVESTIGATOR_DEADLINE_S", 0.05)

    # One step completes (a pubmed tool call) and is emitted before the loop stalls on the next
    # (slow) LLM generation, which the deadline cancels.
    streamed = {
        "messages": [
            SimpleNamespace(type="human", content="brief"),
            SimpleNamespace(type="tool", name="pubmed_search", content="3 hits"),
        ]
    }
    fake_react = SimpleNamespace(astream=_astream_yielding(streamed, stall_after=True))

    with (
        patch("agents.synthesis.investigator.agent.MultiServerMCPClient") as mock_client_cls,
        patch("agents.synthesis.investigator.agent.ChatOllama"),
        patch("agents.synthesis.investigator.agent.create_react_agent", return_value=fake_react),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[])

        result = await InvestigatorAgent().run(investigator_msg, ctx)

    # Run is not broken; summary is empty (no final assessment), but the tool trail survives.
    assert result.intent == "result"
    assert result.payload["investigation_summary"] == ""
    assert result.payload["tools_used"] == ["pubmed_search"]


async def test_investigator_agent_propagates_gateway_errors(ctx, investigator_msg):
    """The agent itself does not swallow failures — degradation is the workflow node's job."""
    with patch("agents.synthesis.investigator.agent.MultiServerMCPClient") as mock_client_cls:
        mock_client_cls.return_value.get_tools = AsyncMock(side_effect=ConnectionError("down"))

        with pytest.raises(ConnectionError):
            await InvestigatorAgent().run(investigator_msg, ctx)
