# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""InvestigatorAgent — bounded ReAct loop over the retrieval MCP tools.

Runs once, after gap_detection, to resolve the specific lens gaps/conflicts the
review surfaced. Unlike the acquisition agents (which fetch broadly, by source),
this agent calls retrieval tools on demand, driven by an LLM reasoning over the
named gaps — conclusion-enrichment only, no evidence-flowback into
screening/lenses (see ``capabilities.target_validation.workflow.investigator_node``,
which never breaks the run on failure).

Connects to the same MCP gateway the chat assistant uses
(``src/mcp_gateway/chat_app.py``) rather than calling ``mcp_servers/*/tools.py``
directly, because the ReAct loop needs tool *schemas* to pick from, not typed
Python calls. ``internal_data`` is never reachable here — it has no server.py
mounted on the gateway — and the explicit source allow-list further restricts
the loop to the sources that actually feed ``review_gaps`` stages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import timedelta

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from agents.synthesis.investigator.contract import CONTRACT
from core.routing.policy import get_policy
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.messages import AgentMessage

logger = logging.getLogger(__name__)


def _investigator_num_ctx() -> int:
    """Context window for the investigator's local ReAct loop.

    Resolved from the ollama provider's ``task_num_ctx["investigator"]`` in
    config/routing.yaml (falling back to that provider's ``num_ctx``, then 16384) so
    it is tuned alongside the other per-task windows — even though this agent drives
    ``ChatOllama`` directly rather than through ``OllamaProvider``.

    Why 16384 by default: the loop accumulates several tool results in one context,
    but a 32768 window made generation slow enough to blow the wall-clock deadline on
    the local 7B model; 16k keeps it fast while still fitting the bounded tool budget.
    """
    ollama = get_policy().providers.get("ollama")
    if ollama is None:
        return 16384
    return ollama.task_num_ctx.get("investigator", ollama.num_ctx)


_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "http://127.0.0.1:8765/mcp")
_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:7b-instruct-q4_K_M")
_INVESTIGATOR_MAX_TOOL_CALLS = 5
# create_react_agent alternates agent/tool super-steps, so N tool-calling turns plus the final
# answer is 2*N + 1 steps. Exceeding this raises GraphRecursionError (handled by the node).
_INVESTIGATOR_RECURSION_LIMIT = 2 * _INVESTIGATOR_MAX_TOOL_CALLS + 1  # 5 tool calls -> 11 steps
# Per-MCP-request timeout — the primary defense against a stall. It bounds each individual tool
# call, so an unresponsive tool fails fast (surfaced to the loop as a tool error the model can
# route around) instead of blocking the whole loop. ``get_tools()`` opens a fresh session per
# tool invocation here, so this applies to every call. Capped at 5 minutes.
_MCP_TOOL_TIMEOUT_S = float(os.getenv("MCP_TOOL_TIMEOUT_S", "300"))
# Hard wall-clock cap on the whole ReAct loop — the backstop. ``recursion_limit`` only bounds
# graph super-steps and the per-call timeout only bounds one call, so without this the worst
# case (5 tool calls each stalling near their 5-min cap) still ties up the run for many minutes.
# The investigator is enrichment-only (workflow never breaks the run on its failure), so timing
# out — degrading in place to an empty summary while preserving the tools-used trail — beats
# hanging the pipeline. Sized to cover the budget: 5 tool calls * 5 min + LLM/headroom.
_INVESTIGATOR_DEADLINE_S = float(
    os.getenv("INVESTIGATOR_DEADLINE_S", str(_INVESTIGATOR_MAX_TOOL_CALLS * _MCP_TOOL_TIMEOUT_S + 180))
)

# Allow-listed retrieval source prefixes (the gateway's @mcp.tool(name=...) values are
# always "<source>_<verb>...", e.g. "pubmed_search", "gwas_catalog_get_associations").
# "ontology" and "druggability" are logical groupings over several real prefixes:
# ontology -> hgnc/mondo/hpo resolvers; druggability -> ttd (chembl/dgidb/uniprot are
# named directly). internal_data has no server.py on the gateway, so it is already
# unreachable here regardless of this list.
_RETRIEVAL_SOURCES = frozenset(
    {
        "pubmed",
        "opentargets",
        "gwas_catalog",
        "gnomad",
        "clinicaltrials",
        "clingen",
        "gencc",
        "omim",
        "orphanet",
        "spoke",
        "hgnc",
        "mondo",
        "hpo",
        "openfda",
        "chembl",
        "dgidb",
        "ttd",
        "uniprot",
        "gtex",
        "expression_atlas",
    }
)


def _gateway_connection() -> dict:
    conn: dict = {
        "transport": "streamable_http",
        "url": _GATEWAY_URL,
        # Bound each tool call at the MCP-session level so one unresponsive tool can't stall
        # the ReAct loop; ``sse_read_timeout`` caps a silent/idle response stream the same way.
        "session_kwargs": {"read_timeout_seconds": timedelta(seconds=_MCP_TOOL_TIMEOUT_S)},
        "sse_read_timeout": _MCP_TOOL_TIMEOUT_S,
    }
    token = os.getenv("MCP_GATEWAY_TOKEN", "").strip()
    if token:
        conn["headers"] = {"Authorization": f"Bearer {token}"}
    return conn


def _retrieval_tools(tools: list) -> list:
    """Keep only the allow-listed retrieval tools (by source-prefixed tool name)."""
    return [t for t in tools if any(t.name.startswith(f"{src}_") for src in _RETRIEVAL_SOURCES)]


def _tools_used(messages: list) -> list[str]:
    return [m.name for m in messages if getattr(m, "type", "") == "tool"]


def _where_stalled(messages: list) -> str:
    """Describe what the loop was doing when the deadline hit, for the timeout log line.

    With ``stream_mode="values"`` the last emitted message is the most recent *completed*
    step; the cancelled work is whatever comes next. A trailing tool message means we were
    waiting on the model to reason over results (slow LLM generation); a trailing AI message
    with tool calls means we were waiting on those tools.
    """
    if not messages:
        return "no steps completed (initial LLM call)"
    last = messages[-1]
    last_type = getattr(last, "type", "")
    if last_type == "tool":
        return "generating after tool results (LLM call)"
    if last_type == "ai" and getattr(last, "tool_calls", None):
        return f"awaiting tool call(s): {[tc.get('name') for tc in last.tool_calls]}"
    return f"after {last_type} message"


def _build_brief(spec: dict) -> str:
    agreement_map = spec.get("agreement_map") or {}
    return (
        f"Target gene: {spec.get('target_gene', '')}\n"
        f"Disease: {spec.get('disease', '')}\n"
        f"Direction under evaluation: {spec.get('direction') or 'unspecified'}\n"
        f"Lens summary: {spec.get('lens_summary', '')}\n"
        f"Cross-lens consensus: {agreement_map.get('consensus_verdict', 'unknown')}\n"
        f"Lens conflicts: {json.dumps(agreement_map.get('conflicts') or [], indent=2)}\n\n"
        f"Open review gaps:\n{json.dumps(spec.get('review_gaps') or [], indent=2)}"
    )


class InvestigatorAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}

        client = MultiServerMCPClient({"gateway": _gateway_connection()})
        tools = _retrieval_tools(await client.get_tools())
        llm = ChatOllama(
            model=_OLLAMA_MODEL,
            base_url=_OLLAMA_BASE_URL,
            temperature=0,
            num_ctx=_investigator_num_ctx(),
            client_kwargs={"timeout": 600},
            async_client_kwargs={"timeout": 600},
        )
        react = create_react_agent(llm, tools, prompt=ctx.load_skill("investigator"))

        brief = _build_brief(spec)
        # Stream (rather than ``ainvoke``) so the message list survives a deadline cancellation:
        # ``stream_mode="values"`` emits the cumulative state after every super-step, so
        # ``messages`` always holds everything completed up to the last step — letting us report
        # which tools ran and where the loop stalled even when it times out.
        #
        # ``recursion_limit`` bounds the number of super-steps; the per-call MCP timeout bounds a
        # single tool call; this ``asyncio.wait_for`` is the wall-clock backstop. On timeout we
        # degrade in place (empty summary, but tools_used preserved) instead of propagating, so
        # the report still sees what the investigation reached.
        messages: list = []

        async def _drive() -> None:
            nonlocal messages
            async for state in react.astream(
                {"messages": [{"role": "user", "content": brief}]},
                config={"recursion_limit": _INVESTIGATOR_RECURSION_LIMIT},
                stream_mode="values",
            ):
                messages = state.get("messages", messages)

        timed_out = False
        try:
            await asyncio.wait_for(_drive(), timeout=_INVESTIGATOR_DEADLINE_S)
        except TimeoutError:
            timed_out = True
            logger.warning(
                "[investigator] timed out after %.0fs — stalled %s; tools_used=%s",
                _INVESTIGATOR_DEADLINE_S,
                _where_stalled(messages),
                _tools_used(messages),
            )

        last = messages[-1] if messages else None
        # A clean finish ends on the model's AI assessment; on timeout the final assessment was
        # never produced, so leave the summary empty rather than surfacing partial reasoning.
        summary = (
            last.content if (not timed_out and last is not None and getattr(last, "type", "") == "ai") else ""
        )
        tools_used = _tools_used(messages)
        logger.info("[investigator] tools_used=%s (timed_out=%s)", tools_used, timed_out)

        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"investigation_summary": summary, "tools_used": tools_used},
            trace_id=msg.trace_id,
        )
