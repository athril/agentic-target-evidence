# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Gene Target Evidence Assistant: a Gradio chat backed by a local Ollama model that can
call every tool exposed by the MCP gateway (``src/mcp_gateway/server.py``).

This is a user-facing client of the gateway, not part of the validation pipeline — the
pipeline calls ``mcp_servers/*/tools.py`` directly. The assistant lets a human (or several,
when auth is configured) explore the same biomedical connectors interactively: ask a
question, watch the agent pick and call tools, get an answer.

Production deployment is a standalone Docker service (``chat`` in docker-compose.yml) that
talks to a separate ``mcp-gateway`` HTTP service over the internal network. Conversation
state is persisted in Postgres via LangGraph's checkpointer, keyed per authenticated user
and browser session, so history survives restarts.

Requires the gateway reachable over HTTP (set by ``MCP_GATEWAY_URL``). Locally:

    MCP_TRANSPORT=http make mcp-serve   # terminal 1
    make chat                           # terminal 2 — prints a local Gradio URL

Configuration (all optional; sensible local defaults):
    MCP_GATEWAY_URL    gateway MCP endpoint (default http://127.0.0.1:8765/mcp)
    MCP_GATEWAY_TOKEN  bearer token sent to the gateway; must match the gateway's own
                       MCP_GATEWAY_TOKEN. Unset → no Authorization header (local/no-auth).
    OLLAMA_BASE_URL    Ollama endpoint (default http://localhost:11434)
    OLLAMA_CHAT_MODEL  chat model (default qwen2.5:7b-instruct-q4_K_M)
    CHAT_AUTH          comma-separated user:secret pairs enabling login; secret may be a
                       bcrypt hash ($2...) or plaintext. Unset → open access (local dev).
    CHAT_HOST/CHAT_PORT  bind address (default 0.0.0.0:7860)
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections.abc import Callable

import bcrypt
import gradio as gr
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from core.checkpoint.pg_checkpointer import get_checkpointer

load_dotenv()

logger = logging.getLogger(__name__)

_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "http://127.0.0.1:8765/mcp")
_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:7b-instruct-q4_K_M")

_SYSTEM_PROMPT = (
    "You are the Gene Target Evidence Assistant. Answer using the MCP tools available to "
    "you. Always cite the source of every factual claim and include a link to the original "
    "record (e.g. the database entry, paper, or trial page) whenever the tool result "
    "provides one. If a tool result has no source link, say so explicitly rather than "
    "presenting the claim as unsourced fact."
)

# Lazily-built singletons. The LangGraph Postgres checkpointer opens a psycopg pool bound to
# the event loop it is created in, so we must build it inside the loop Gradio serves requests
# on — not in a throwaway startup loop. Hence first-request initialization under a lock.
_agent = None
_tools: list = []
_init_lock = asyncio.Lock()
# Holds the checkpointer's async context manager so its underlying connection isn't closed
# by garbage collection once _ensure_agent's local scope exits (see `__aenter__` call below).
_checkpointer_cm = None


def _gateway_connection() -> dict:
    conn: dict = {"transport": "streamable_http", "url": _GATEWAY_URL}
    token = os.getenv("MCP_GATEWAY_TOKEN", "").strip()
    if token:
        conn["headers"] = {"Authorization": f"Bearer {token}"}
    return conn


async def _ensure_agent() -> None:
    """Build the react agent + open the checkpointer pool, once, in the serving loop."""
    global _agent, _tools, _checkpointer_cm
    if _agent is not None:
        return
    async with _init_lock:
        if _agent is not None:
            return
        # Enter the checkpointer context manager and keep it open for the process lifetime
        # (a long-running service); there is no clean shutdown hook to pair an exit with.
        # The context manager itself must be kept alive in `_checkpointer_cm` — otherwise it
        # gets garbage-collected, which finalizes its generator and closes the connection out
        # from under `saver`.
        _checkpointer_cm = get_checkpointer()
        saver = await _checkpointer_cm.__aenter__()
        await saver.setup()

        client = MultiServerMCPClient({"gateway": _gateway_connection()})
        tools = await client.get_tools()
        # num_ctx/timeout mirror OllamaProvider (src/core/routing/providers/ollama.py) — tool
        # results (e.g. full GTEx tissue panels) can be large, and the default num_ctx is too
        # small to hold them without the model silently grinding through truncated context.
        llm = ChatOllama(
            model=_OLLAMA_MODEL,
            base_url=_OLLAMA_BASE_URL,
            temperature=0,
            num_ctx=16384,
            client_kwargs={"timeout": 600},
            async_client_kwargs={"timeout": 600},
        )
        _agent = create_react_agent(llm, tools, prompt=_SYSTEM_PROMPT, checkpointer=saver)
        _tools = tools
        logger.info("connected to %s — %d tools available", _GATEWAY_URL, len(tools))


def _load_auth() -> Callable[[str, str], bool] | None:
    """Parse CHAT_AUTH into a Gradio auth callback, or None for open access.

    Format: ``user:secret,user2:secret2``. A secret beginning with ``$2`` is treated as a
    bcrypt hash (preferred for production); anything else is compared as plaintext.
    """
    raw = os.getenv("CHAT_AUTH", "").strip()
    if not raw:
        return None
    creds: dict[str, str] = {}
    for pair in raw.split(","):
        user, sep, secret = pair.partition(":")
        if sep and user.strip():
            creds[user.strip()] = secret.strip()
    if not creds:
        return None

    def check(username: str, password: str) -> bool:
        stored = creds.get(username)
        if stored is None:
            return False
        if stored.startswith("$2"):
            return bcrypt.checkpw(password.encode(), stored.encode())
        return secrets.compare_digest(stored, password)

    return check


def _log_tool_detail(turn_messages: list) -> None:
    """Log this turn's full tool args + results — detail that stays out of the chat UI."""
    calls = {
        call["id"]: f"{call['name']}({call['args']})"
        for m in turn_messages
        if isinstance(m, AIMessage)
        for call in m.tool_calls
    }
    if not calls:
        return
    results = {m.tool_call_id: m.content for m in turn_messages if isinstance(m, ToolMessage)}
    detail_lines = [f"- `{calls[cid]}` → {results.get(cid, '(no result)')}" for cid in calls]
    logger.info("tools called:\n%s", "\n".join(detail_lines))


def _tools_called_line(names: list[str]) -> str:
    return "**Tools called:** " + ", ".join(f"`{name}`" for name in names)


async def respond(message: str, history: list[dict[str, str]], request: gr.Request):
    await _ensure_agent()
    assert _agent is not None

    # One persisted conversation per authenticated user + browser session. The checkpointer
    # reloads prior turns by thread_id, so we send only the new message, not `history`.
    user = request.username or "anon"
    config = {"configurable": {"thread_id": f"{user}:{request.session_hash}"}}

    turn_messages: list = []
    tool_names: list[str] = []
    answer = ""

    # stream_mode="updates" yields one event per graph node (here: "agent" then "tools",
    # alternating). A tool call is visible the moment the "agent" node emits it — before the
    # "tools" node actually runs it — so we can show "Tools called" ahead of execution rather
    # than only after the full ReAct loop finishes.
    async for update in _agent.astream(
        {"messages": [{"role": "user", "content": message}]}, config=config, stream_mode="updates"
    ):
        for node_output in update.values():
            new_messages = node_output.get("messages", [])
            turn_messages.extend(new_messages)
            for m in new_messages:
                if not isinstance(m, AIMessage):
                    continue
                for call in m.tool_calls:
                    if call["name"] not in tool_names:
                        tool_names.append(call["name"])
                if not m.tool_calls and m.content:
                    answer = m.content

        tool_log = _tools_called_line(tool_names) if tool_names else None
        yield f"{tool_log}\n\n{answer}" if tool_log else answer

    _log_tool_detail(turn_messages)


_GREETING = (
    "Hi! I'm the Gene Target Evidence Assistant. I call live data sources and cite "
    "them in my answers, which can take a little while. Try something like:\n\n"
    "- What tissue is BRCA1 expressed in the most?\n"
    "- Is TRPC6 a common essential gene per DepMap?\n"
    "- What's the HGNC ID and Ensembl gene ID for PCSK9?\n"
    "- Are there any approved drugs targeting PTPN1?\n"
    "- What clinical trials are recruiting for TP53-related cancers?\n"
    "- Is KRAS considered druggable?\n"
    "- What's the gnomAD population frequency of a given BRCA1 variant?\n"
    "- What subcellular location and protein class is EGFR annotated with in UniProt?\n"
    "- What rare diseases is SMN1 associated with per Orphanet?\n"
    "- What GWAS associations exist for FTO?\n"
    "- What phenotypes does knocking out a gene cause in IMPC mouse models?\n"
    "- What's the development stage of a drug targeting a given gene per TTD?\n"
    "- Are there safety signals for a drug in the FDA adverse event database (openFDA)?\n"
    "- What patents mention a given gene or target, per USPTO?"
)


def build_ui() -> gr.ChatInterface:
    return gr.ChatInterface(
        fn=respond,
        chatbot=gr.Chatbot(value=[{"role": "assistant", "content": _GREETING}], scale=1),
        title="Gene Target Evidence Assistant",
        description=(
            "Ask about genes, variants, druggability, trials, and more. · "
            f"Model: {_OLLAMA_MODEL} · Gateway: {_GATEWAY_URL} · Developed by Patryk Orzechowski."
        ),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    demo = build_ui()
    demo.launch(
        server_name=os.getenv("CHAT_HOST", "0.0.0.0"),
        server_port=int(os.getenv("CHAT_PORT", "7860")),
        auth=_load_auth(),
    )


if __name__ == "__main__":
    main()
