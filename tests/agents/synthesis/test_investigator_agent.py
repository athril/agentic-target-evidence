# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for InvestigatorAgent — bounded ReAct loop over the retrieval MCP tools."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agents.synthesis.investigator.agent import (
    InvestigatorAgent,
    _build_brief,
    _retrieval_tools,
    check_summary,
)
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
        _tool(
            "internal_data_get_target_summary"
        ),  # not gateway-reachable, but also not allow-listed
        _tool("unrelated_tool_call"),
    ]
    kept = {t.name for t in _retrieval_tools(tools)}
    assert kept == {
        "pubmed_search",
        "gwas_catalog_get_associations",
        "expression_atlas_get_baseline",
    }


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


def test_build_brief_gives_the_identifiers_for_id_keyed_tools():
    """TRPC6: without ids the model queried Open Targets with another gene's Ensembl id."""
    brief = _build_brief(
        {"target_gene": "TRPC6", "gene_id": "ENSG00000137672", "disease_id": "MONDO_0100313"}
    )
    assert "Ensembl gene id ENSG00000137672" in brief
    assert "disease id MONDO_0100313" in brief


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
        patch("agents.synthesis.investigator.agent.ChatOllama") as mock_llm,
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
    # The ephemeral loop must not inherit (and write through) the pipeline's checkpointer.
    assert mock_create_react.call_args.kwargs["checkpointer"] is False
    # Each model turn is bounded, so a runaway generation cannot read as a silent stall.
    assert mock_llm.call_args.kwargs["num_predict"] > 0


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


_TRPC6_SPEC = {
    "target_gene": "TRPC6",
    "disease": "FSGS",
    "trial_facts": [
        {
            "nct_id": "NCT05213624",
            "phase": "PHASE2",
            "status": "COMPLETED",
            "phase_numbers": [2],
            "interventions": ["BI 764198", "Placebo"],
            "arm_results": [
                {"arm": "BI 764198 20 mg", "p_value": 0.0024, "p_text": "0.0024"},
                {"arm": "BI 764198 40 mg", "p_value": 0.7906, "p_text": "0.7906"},
                {"arm": "BI 764198 80 mg", "p_value": 0.1325, "p_text": "0.1325"},
            ],
        }
    ],
}


def test_brief_carries_the_trial_facts():
    brief = _build_brief(_TRPC6_SPEC)
    assert "Run facts (authoritative" in brief
    assert "BI 764198 20 mg p=0.0024 (significant)" in brief


def test_check_summary_annotates_a_misstated_outcome():
    """TRPC6: the investigator wrote 'significant proteinuria responses in participants
    receiving different doses' when only the 20 mg arm reached p<0.05."""
    text = (
        "This trial reported significant proteinuria responses in participants receiving "
        "different doses of BI 764198 compared to placebo."
    )
    out = check_summary(text, _TRPC6_SPEC)
    assert out.startswith(text)
    assert "TRIAL OUTCOME GUARD" in out


def test_check_summary_leaves_accurate_text_alone():
    text = "In NCT05213624 only the 20 mg arm of BI 764198 reached significance (p=0.0024)."
    assert check_summary(text, _TRPC6_SPEC) == text


async def test_step_limit_yields_a_final_answer_from_gathered_results(ctx, investigator_msg):
    """PCSK9 × MI: the loop hit its step limit and the whole summary was lost."""
    from langgraph.errors import GraphRecursionError

    gathered = [
        SimpleNamespace(type="human", content="brief"),
        SimpleNamespace(type="tool", name="clinicaltrials_search_trials", content="FOURIER"),
        SimpleNamespace(type="ai", content="", tool_calls=[{"name": "pubmed_search"}]),
    ]

    def astream(*_args, **_kwargs):
        async def gen():
            yield {"messages": gathered}
            raise GraphRecursionError("Recursion limit of 11 reached")

        return gen()

    fake_react = SimpleNamespace(astream=astream)
    with (
        patch("agents.synthesis.investigator.agent.MultiServerMCPClient") as mock_client_cls,
        patch("agents.synthesis.investigator.agent.ChatOllama") as mock_llm_cls,
        patch("agents.synthesis.investigator.agent.create_react_agent", return_value=fake_react),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[])
        mock_llm_cls.return_value.ainvoke = AsyncMock(
            return_value=SimpleNamespace(content="FOURIER confirms the MACE reduction.")
        )
        result = await InvestigatorAgent().run(investigator_msg, ctx)

    assert result.payload["investigation_summary"] == "FOURIER confirms the MACE reduction."
    assert result.payload["tools_used"] == ["clinicaltrials_search_trials"]
    sent = mock_llm_cls.return_value.ainvoke.call_args.args[0]
    assert all(not getattr(m, "tool_calls", None) for m in sent)  # cut-off call dropped


async def test_model_transport_failure_degrades_with_a_reason(ctx, investigator_msg):
    """A model server that stops answering (httpx.ReadTimeout has an empty message) must
    not escape as an unexplained node failure: the tool trail is kept and the payload says
    why the investigation stopped."""
    import httpx

    def _astream_then_fail(*_a, **_kw):
        async def _gen():
            yield {
                "messages": [
                    SimpleNamespace(type="human", content="brief"),
                    SimpleNamespace(type="tool", name="pubmed_search", content="3 hits"),
                ]
            }
            raise httpx.ReadTimeout("")

        return _gen()

    fake_react = SimpleNamespace(astream=_astream_then_fail)
    with (
        patch("agents.synthesis.investigator.agent.MultiServerMCPClient") as mock_client_cls,
        patch("agents.synthesis.investigator.agent.ChatOllama"),
        patch("agents.synthesis.investigator.agent.create_react_agent", return_value=fake_react),
    ):
        mock_client_cls.return_value.get_tools = AsyncMock(return_value=[])
        result = await InvestigatorAgent().run(investigator_msg, ctx)

    assert result.payload["investigation_summary"] == ""
    assert result.payload["tools_used"] == ["pubmed_search"]
    assert result.payload["error"].startswith("ReadTimeout after")
    assert "generating after tool results" in result.payload["error"]


def test_check_summary_flags_gwas_called_absent_when_present():
    spec = {"target_gene": "GENE-A", "disease": "DISEASE-A", "gwas_traits_in_scope": 2}
    text = "There are no direct GWAS associations for GENE-A in DISEASE-A."
    assert "MENDELIAN FLOOR GUARD" in check_summary(text, spec)
    assert check_summary(text, {**spec, "gwas_traits_in_scope": 0}) == text


def test_brief_states_the_gwas_fact():
    brief = _build_brief(
        {"target_gene": "GENE-A", "disease": "DISEASE-A", "gwas_traits_in_scope": 3}
    )
    assert "GWAS Catalog traits matched to this disease: 3" in brief
