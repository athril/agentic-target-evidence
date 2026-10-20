# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""InvestigatorAgent — bounded ReAct loop over the retrieval MCP tools.

Runs once, after the challenge stage, to resolve the specific lens gaps/conflicts the
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
import ipaddress
import json
import logging
import os
import re
import time
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StreamableHttpConnection
from langchain_ollama import ChatOllama
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from mcp.shared._httpx_utils import create_mcp_http_client

from agents.interpretation._lens_base import guard_violations, known_drug_approval
from agents.interpretation._lens_confidence import fact_check_violations
from agents.synthesis.investigator.contract import CONTRACT
from core.exceptions import describe_exception
from core.routing.policy import get_policy
from harness.base_agent import BaseAgent
from harness.context import RunContext
from schemas.evidence import CoreClaim
from schemas.messages import AgentMessage
from services.evidence.clinical_trial_interpret import (
    TrialFact,
    target_drugs,
    trial_count_prompt,
    trial_outcome_prompt,
    trial_reported_values,
)
from services.evidence.fact_checks import fact_checks
from services.evidence.guard_core import annotate
from services.evidence.guard_rules import GuardFacts, GuardFamily

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


def _investigator_think() -> bool | None:
    """The ollama provider's ``think`` switch in config/routing.yaml (None = model default)."""
    ollama = get_policy().providers.get("ollama")
    return ollama.think if ollama is not None else None


_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "http://127.0.0.1:8765/mcp")
_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen3.5:9b-q4_K_M")
_INVESTIGATOR_MAX_TOOL_CALLS = 5
# Tokens per model turn. Unbounded, a small model that starts repeating itself generates
# until the context is full; with tools bound, Ollama returns nothing until the turn ends,
# so the turn reads as a silent stall until the client timeout (LRRK2 × PD: ~15 min, twice).
# Matches the cap every other model call uses (CompletionRequest.max_tokens).
_INVESTIGATOR_NUM_PREDICT = int(os.getenv("INVESTIGATOR_NUM_PREDICT", "4096"))
# create_react_agent alternates agent/tool super-steps, so N tool-calling turns plus the final
# answer is 2*N + 1 steps. Exceeding this raises GraphRecursionError, after which the agent asks
# for a final answer over what the loop gathered (``_final_answer``).
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
# Bound on the one tool-less call that turns a step-limited loop's tool results into the
# final assessment (PCSK9 × MI: the loop used its budget and the summary was lost).
_FINAL_ANSWER_S = float(os.getenv("INVESTIGATOR_FINAL_ANSWER_S", "300"))
_WRAP_UP = (
    "Your tool budget is used up. Do not call any more tools. Write the final assessment now "
    "from the tool results above only, ending with the short evidence-grounded assessment "
    "your instructions ask for."
)
_INVESTIGATOR_DEADLINE_S = float(
    os.getenv(
        "INVESTIGATOR_DEADLINE_S", str(_INVESTIGATOR_MAX_TOOL_CALLS * _MCP_TOOL_TIMEOUT_S + 180)
    )
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


class GatewayUnreachableError(ConnectionError):
    """The MCP gateway could not be reached; the message names the URL and the cause."""


def _is_local_host(url: str) -> bool:
    """Loopback or a single-label host (a Docker service name): never behind a proxy."""
    host = (urlsplit(url).hostname or "").lower()
    if not host or host == "localhost" or "." not in host:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _gateway_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """The MCP SDK's client, minus the environment's proxy for a local gateway.

    httpx honours HTTP(S)_PROXY unless NO_PROXY names the host. With NO_PROXY covering
    "localhost" but not "127.0.0.1", every gateway call went to the proxy, which could not
    resolve, and the investigator failed in every run ("Name or service not known" from
    inside the proxy transport) while the Ollama calls to localhost worked.
    """
    client = create_mcp_http_client(headers=headers, timeout=timeout, auth=auth)
    if not _is_local_host(_GATEWAY_URL):
        return client
    kwargs: dict[str, Any] = {
        "follow_redirects": False,
        "timeout": client.timeout,
        "headers": client.headers,
        "trust_env": False,
    }
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


def _gateway_connection() -> StreamableHttpConnection:
    conn: StreamableHttpConnection = {
        "transport": "streamable_http",
        "url": _GATEWAY_URL,
        "httpx_client_factory": _gateway_http_client,
        # Bound each tool call at the MCP-session level so one unresponsive tool can't stall
        # the ReAct loop; ``sse_read_timeout`` caps a silent/idle response stream the same way.
        "session_kwargs": {"read_timeout_seconds": timedelta(seconds=_MCP_TOOL_TIMEOUT_S)},
        "sse_read_timeout": _MCP_TOOL_TIMEOUT_S,
    }
    token = os.getenv("MCP_GATEWAY_TOKEN", "").strip()
    if token:
        conn["headers"] = {"Authorization": f"Bearer {token}"}
    return conn


def _retrieval_tools(tools: list[Any]) -> list[Any]:
    """Keep only the allow-listed retrieval tools (by source-prefixed tool name)."""
    return [t for t in tools if any(t.name.startswith(f"{src}_") for src in _RETRIEVAL_SOURCES)]


def _tools_used(messages: list[BaseMessage]) -> list[str]:
    return [m.name for m in messages if getattr(m, "type", "") == "tool" and m.name]


def _where_stalled(messages: list[BaseMessage]) -> str:
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
    if last_type == "ai" and isinstance(last, AIMessage) and last.tool_calls:
        return f"awaiting tool call(s): {[tc.get('name') for tc in last.tool_calls]}"
    return f"after {last_type} message"


# The checks the clinical lens's trial prose passes, applied to the investigation too.
_SUMMARY_GUARDS: tuple[GuardFamily, ...] = (
    "clinical_trial",
    "trial_outcome",
    "quantity",
    "drug_approval",
    "trial_readout",
    "trial_attribution",
    "mendelian_floor",  # with mendelian_grade unset, only the GWAS-presence check runs
)


def _trial_facts(spec: dict[str, Any]) -> list[TrialFact]:
    return [TrialFact.model_validate(f) for f in spec.get("trial_facts") or []]


def _facts_block(spec: dict[str, Any]) -> str:
    """The run's registry trial facts and GWAS count, so the summary does not contradict them."""
    facts = _trial_facts(spec)
    parts = [p for p in (trial_count_prompt(facts), trial_outcome_prompt(facts)) if p]
    gwas = int(spec.get("gwas_traits_in_scope") or 0)
    if gwas:
        parts.append(
            f"GWAS Catalog traits matched to this disease: {gwas} (GWAS signal is present)."
        )
    return (
        "\n\nRun facts (authoritative — do not contradict them):\n" + "\n".join(parts)
        if parts
        else ""
    )


def trim_off_target(text: str, gene: str, disease: str, drugs: list[str]) -> str:
    """Drop answer blocks that name neither the gene, its drugs nor the disease.

    The investigator's tools are keyword searches; a hyperlipidemia trial search returned
    omega-3, apolipoprotein-mimetic and psyllium studies that it then summarised as PCSK9
    findings. Blocks are split on blank lines and markdown headings; a heading is kept
    only with a kept body.
    """
    terms = [x.lower() for x in (gene, disease, *drugs) if x and len(x) >= 3]
    if not text or not terms:
        return text
    blocks = re.split(r"\n\s*\n", text.strip())

    def on_target(block: str) -> bool:
        low = block.lower()
        return any(t in low for t in terms) or low.lstrip().startswith(
            ("**assessment", "### assessment", "assessment")
        )

    kept = [b for b in blocks if on_target(b)]
    dropped = len(blocks) - len(kept)
    # Only a drifted answer is trimmed: most of several blocks off-target. A short
    # answer citing a trial by acronym ("FOURIER confirms …") is left alone.
    if len(blocks) < 3 or dropped * 2 <= len(blocks):
        return text
    note = (
        f"[⚠ RELEVANCE TRIM: {dropped} block(s) of the investigation named neither {gene}, "
        f"its known drugs nor {disease} and were removed.]"
    )
    return "\n\n".join([*kept, note]) if kept else note


def check_summary(text: str, spec: dict[str, Any]) -> str:
    """Annotate summary sentences that contradict the run's trial facts or claims (the
    clinical guards and the fact checks) — annotated, never rewritten."""
    if not text:
        return text
    gene, disease = spec.get("target_gene", ""), spec.get("disease", "")
    names = {str(d.get("drug_name") or "") for d in spec.get("ot_known_drugs") or []}
    trials = _trial_facts(spec)
    names |= {f.nct_id for f in trials} | set(target_drugs(trials))
    text = trim_off_target(text, gene, disease, sorted(n for n in names if n))
    trial_facts = _trial_facts(spec)
    facts = GuardFacts(
        gene=gene,
        disease=disease,
        trial_facts=trial_facts,
        gwas_traits_in_scope=int(spec.get("gwas_traits_in_scope") or 0),
        trial_values=trial_reported_values(
            trial_facts,
            [
                (str(c.get("source_evidence_id") or ""), str(c.get("claim_text") or ""))
                for c in spec.get("extracted_claims") or []
            ],
        ),
        drug_approval=known_drug_approval(spec.get("ot_known_drugs") or []),
    )
    claims = [CoreClaim.model_validate(c) for c in spec.get("extracted_claims") or []]
    found = guard_violations(text, facts, _SUMMARY_GUARDS)
    found += [v for _, v in fact_check_violations(text, claims, fact_checks(), gene, disease)]
    if found:
        logger.info("[investigator] %d summary sentence(s) annotated", len(found))
    return annotate(text, found)


def _build_brief(spec: dict[str, Any]) -> str:
    agreement_map = spec.get("agreement_map") or {}
    return (
        f"Target gene: {spec.get('target_gene', '')}\n"
        f"Disease: {spec.get('disease', '')}\n"
        f"Direction under evaluation: {spec.get('direction') or 'unspecified'}\n"
        # Without these the model invents identifiers for ID-keyed tools (an Open Targets
        # call for TRPC6 went out with another gene's Ensembl id).
        f"Identifiers — use exactly these in tool calls: Ensembl gene id "
        f"{spec.get('gene_id') or 'unknown'}, disease id {spec.get('disease_id') or 'unknown'}\n"
        f"Lens summary: {spec.get('lens_summary', '')}\n"
        f"Cross-lens consensus: {agreement_map.get('consensus_verdict', 'unknown')}\n"
        f"Lens conflicts: {json.dumps(agreement_map.get('conflicts') or [], indent=2)}\n\n"
        f"Open review gaps:\n{json.dumps(spec.get('review_gaps') or [], indent=2)}"
        + (
            f"\n\n{(spec.get('drug_precedent') or {}).get('text')}"
            if (spec.get("drug_precedent") or {}).get("text")
            else ""
        )
        + _facts_block(spec)
    )


async def _final_answer(llm: Any, system: str, messages: list[BaseMessage]) -> str:
    """One tool-less call: the assessment from the tool results gathered so far."""
    history = list(messages)
    while history and getattr(history[-1], "tool_calls", None):
        history.pop()  # a tool call the limit cut off has no result to read
    reply = await asyncio.wait_for(
        llm.ainvoke([SystemMessage(content=system), *history, HumanMessage(content=_WRAP_UP)]),
        timeout=_FINAL_ANSWER_S,
    )
    content = getattr(reply, "content", "")
    return content if isinstance(content, str) else str(content)


class InvestigatorAgent(BaseAgent):
    contract = CONTRACT

    async def act(self, msg: AgentMessage, ctx: RunContext) -> AgentMessage:
        spec = msg.task_spec or {}

        client = MultiServerMCPClient({"gateway": _gateway_connection()})
        try:
            tools = _retrieval_tools(await client.get_tools())
        except Exception as exc:  # name the gateway, not anyio's TaskGroup wrapper
            raise GatewayUnreachableError(
                f"MCP gateway unreachable at {_GATEWAY_URL} ({describe_exception(exc)}); "
                "start it with `MCP_TRANSPORT=http make mcp-serve` or set MCP_GATEWAY_URL"
            ) from exc
        llm = ChatOllama(
            model=_OLLAMA_MODEL,
            base_url=_OLLAMA_BASE_URL,
            temperature=0,
            # Same switch as OllamaProvider: a thinking trace is spent from num_predict.
            reasoning=_investigator_think(),
            num_ctx=_investigator_num_ctx(),
            num_predict=_INVESTIGATOR_NUM_PREDICT,
            client_kwargs={"timeout": 600},
            async_client_kwargs={"timeout": 600},
        )
        skill = ctx.load_skill("investigator")
        # checkpointer=False: the loop is ephemeral. A subgraph otherwise inherits the
        # pipeline's checkpointer and writes every intermediate step (tool outputs,
        # ~23 MB per run) through the pipeline's connection, so a persistence failure
        # inside the loop could break the run's own checkpointing.
        react = create_react_agent(llm, tools, prompt=skill, checkpointer=False)

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
        messages: list[BaseMessage] = []

        async def _drive() -> None:
            nonlocal messages
            async for state in react.astream(
                {"messages": [{"role": "user", "content": brief}]},
                config={"recursion_limit": _INVESTIGATOR_RECURSION_LIMIT},
                stream_mode="values",
            ):
                messages = state.get("messages", messages)

        timed_out = False
        hit_limit = False
        # Why the loop stopped without an assessment ("" on a clean finish), for the report.
        error = ""
        started = time.monotonic()
        try:
            await asyncio.wait_for(_drive(), timeout=_INVESTIGATOR_DEADLINE_S)
        except GraphRecursionError:
            hit_limit = True
            logger.warning(
                "[investigator] step limit reached after tools %s — asking for a final answer",
                _tools_used(messages),
            )
        except TimeoutError:
            timed_out = True
            error = (
                f"timed out after {_INVESTIGATOR_DEADLINE_S:.0f} s — stalled "
                f"{_where_stalled(messages)}"
            )
            logger.warning("[investigator] %s; tools_used=%s", error, _tools_used(messages))
        except Exception as exc:
            # A model or tool transport failure inside the loop (e.g. the model server not
            # answering within the client timeout) degrades like a timeout: the tool trail
            # is kept and the reason is reported, instead of the node failing silently.
            timed_out = True
            error = (
                f"{describe_exception(exc)} after {time.monotonic() - started:.0f} s — "
                f"stalled {_where_stalled(messages)}"
            )
            logger.warning(
                "[investigator] failed: %s; tools_used=%s",
                error,
                _tools_used(messages),
                exc_info=True,
            )

        last = messages[-1] if messages else None
        # A clean finish ends on the model's AI assessment; on timeout the final assessment was
        # never produced, so leave the summary empty rather than surfacing partial reasoning.
        summary = (
            last.content
            if (not timed_out and last is not None and getattr(last, "type", "") == "ai")
            else ""
        )
        if hit_limit:
            try:
                summary = await _final_answer(llm, skill, messages)
            except Exception as exc:  # degrade: keep the tool trail, no summary
                error = f"final answer after the step limit failed: {describe_exception(exc)}"
                logger.warning("[investigator] %s", error)
                summary = ""
        summary = check_summary(summary if isinstance(summary, str) else str(summary), spec)
        tools_used = _tools_used(messages)
        logger.info("[investigator] tools_used=%s (timed_out=%s)", tools_used, timed_out)

        return AgentMessage(
            message_id=uuid.uuid4(),
            run_id=msg.run_id,
            from_agent=msg.to_agent,
            to_agent=msg.from_agent,
            intent="result",
            payload={"investigation_summary": summary, "tools_used": tools_used, "error": error},
            trace_id=msg.trace_id,
        )
