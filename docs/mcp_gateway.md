# The MCP gateway

> Part of the [docs/](README.md) documentation set. This is the **reference** doc — exposure
> model, security, transports, discovery internals. **For a step-by-step walkthrough, see
> [mcp_tutorial.md](mcp_tutorial.md) instead.** For why the per-source connectors are
> normally consumed in-process (not over MCP), see
> [components.md](components.md#a-note-on-mcp-servers); for the connectors themselves see
> [data_sources.md](data_sources.md).

The **MCP gateway** exposes the project's biomedical connectors as **one** MCP server that
any MCP host — Claude Desktop, Claude Code, another agent, or the bundled chat assistant —
can call for ad hoc work *outside* the validation pipeline, without running a full analysis.

The gateway is a **second, additive exposure**. For all bulk retrieval the pipeline keeps
importing each source's `tools.py` directly (fast, typed, no protocol tax) and never routes
through the gateway. Implemented in [src/mcp_gateway/server.py](src/mcp_gateway/server.py).

> **One pipeline node is a gateway client: the `investigator`** ([agents.md](agents.md#synthesis--scoring-gap-gating-and-the-dossier)).
> Because it runs a ReAct loop that *picks* tools at reasoning time, it consumes the gateway's
> tool schemas over MCP — exactly like the chat assistant — instead of importing `tools.py`.
> So the planner service depends on the `mcp-gateway` container and sets `MCP_GATEWAY_URL`;
> running the pipeline natively (`make run`) needs the gateway up too (`MCP_TRANSPORT=http make
> mcp-serve`). It still **degrades gracefully** if the gateway is unreachable — the node logs a
> warning, returns an empty summary, and the run completes.

---

## What it exposes

Each `src/mcp_servers/<name>/server.py` wraps its `tools.py` in `@mcp.tool()` definitions with
docstrings and Pydantic return schemas. Each tool's `@mcp.tool(name=...)` already carries an
explicit source prefix — e.g. DepMap's `get_dependency` function is registered as
`depmap_get_dependency` — so the name is identical whether a client connects to that source's
standalone `server.py` or to the composed gateway. The gateway therefore mounts each
per-source `FastMCP` instance with `mount(sub_server)` (no `namespace=`); namespacing there
would double the prefix. There are no tool-name collisions across sources. The handful of
tools in `ontology/server.py` are prefixed by their actual upstream database (`hgnc_`,
`mondo_`, `hpo_`) rather than the `ontology` folder name, since that one module wraps three
distinct sources.

Of the source connectors under `src/mcp_servers/`, **26 ship a `server.py` wrapper** exposing
**~44 read-only tools** in total. The exact set mounted at runtime depends on the feature
gates and the SENSITIVE exclusion below; `internal_data` is **never** mounted.

### Dynamic discovery

The gateway does **not** keep a hand-maintained list of sources. `_discover_public_servers()`
walks `mcp_servers/` with `pkgutil.iter_modules` and imports each `<name>.server` module,
skipping any source that has no `server.py`. Consequences:

- **Self-maintaining.** A new public source needs zero gateway edit — add
  `mcp_servers/<name>/server.py` and it is mounted on the next start.
- **Gating-aware.** A source whose feature flag is off (`OMIM_ENABLED`, `SCIMAGO_SJR_ENABLED`,
  `TTD_ENABLED`) is skipped so its tools don't clutter the tool list. (The underlying
  `tools.py` already no-ops safely when disabled — this is discovery hygiene, not the safety
  boundary.)
- **API-key preflight.** On mount the gateway logs a warning if a source's required key is
  unset (`USPTO_API_KEY` for `uspto`, `OMIM_API_KEY` for `omim`), so a client gets a clear log
  line instead of silent empty results.

Every tool call is logged (`tool=<name> duration_ms=<ms> outcome=ok|error`) by a FastMCP
middleware. These out-of-pipeline calls have no Langfuse run context, so they emit plain
structured logs rather than traces.

---

## Security: `internal_data` is never exposed

`internal_data` exposes `query_internal_db(sql)` — arbitrary read-only `SELECT` against the
internal Postgres, returning rows that are always classified `SENSITIVE`. In the pipeline this
is safe (in-process; results are forced to the local model, never a cloud LLM). Over MCP it
would be a data-exfiltration surface to whatever client connects. So:

- **`src/mcp_servers/internal_data/server.py` does not exist** — it was deleted. There is no
  `FastMCP("internal_data")` instance and no entry point to import or launch. (This also closed
  a footgun: that file's `__main__` block previously let anyone stand up an *unauthenticated*
  MCP server over the internal DB.)
- The gateway **never imports `mcp_servers.internal_data`** — discovery skips it because it has
  no `server.py`. Exclusion is by omission, with no opt-in flag or env var that could mount it.
- As defense-in-depth, before mounting *any* discovered source the gateway checks its name
  against `core.routing.classify._SENSITIVE_AGENTS` and raises `RuntimeError` if a SENSITIVE
  source ever grows an importable `server.py`. A regression test reclassifies a public source
  as SENSITIVE to prove this guard actually fires.
- `internal_data/tools.py` is untouched and continues to serve the pipeline in-process, where
  SENSITIVE evidence is correctly forced to the local model.

This keeps the "SENSITIVE never leaves the box" invariant
([data_sources.md](data_sources.md#sensitivity-classification--data-safety)) absolute at the
gateway boundary.

---

## Transports and authentication

The gateway runs over **stdio** (default) or **HTTP**, selected by `MCP_TRANSPORT`:

- **stdio** — the Claude Desktop / Claude Code persona, where the client owns the process.
- **HTTP** — `POST http://<host>:<port>/mcp` (FastMCP's streamable-HTTP path). Set `MCP_HOST` /
  `MCP_PORT` (default `8765`).

**Bearer-token auth** for HTTP is controlled by `MCP_GATEWAY_TOKEN`:

- unset → no auth (the stdio persona and local HTTP dev);
- set → a FastMCP `StaticTokenVerifier` requiring `Authorization: Bearer <token>` on every HTTP
  request.

A static token (not a full OAuth issuer) is the right weight for a service-to-service link; it
closes the open-proxy gap if `MCP_HOST` is ever bound off-loopback. Generate one with
`openssl rand -hex 32` and set the same value on the gateway and any client (the chat service
does this automatically). Even with auth off, all exposed tools are read-only public-data
fetchers (`internal_data` is excluded), so this is abuse/rate-limit control, not data
confidentiality.

---

## Running and connecting

The gateway is a packaged entry point: `target-evidence-mcp = "mcp_gateway.server:main"`. Launch it with
`make mcp-serve` (or `uv run target-evidence-mcp`).

### Claude Desktop / Claude Code (stdio)

```json
{
  "mcpServers": {
    "agentic-target-validation": {
      "command": "uv",
      "args": ["run", "target-evidence-mcp"],
      "cwd": "/absolute/path/to/agentic-target-validation"
    }
  }
}
```

### HTTP client

```bash
MCP_TRANSPORT=http MCP_HOST=0.0.0.0 MCP_PORT=8765 make mcp-serve
```

```json
{
  "mcpServers": {
    "agentic-target-validation": { "url": "http://127.0.0.1:8765/mcp" }
  }
}
```

---

## The chat assistant (a gateway client)

[src/mcp_gateway/chat_app.py](src/mcp_gateway/chat_app.py) — the **Gene Target Evidence
Assistant** — is a user-facing Gradio chat UI backed by a LangGraph react agent over local
Ollama (`OLLAMA_CHAT_MODEL`, default `qwen2.5:7b-instruct-q4_K_M`, matching
[config/routing.yaml](config/routing.yaml)). It is a *client* of the gateway, not part of the
pipeline: it connects over MCP (via `langchain_mcp_adapters`, streamable HTTP) and lets a human
ask questions, watch the agent pick and call `mcp_servers/*` tools, and read the answer. Each
reply shows a **Tools called:** block (also logged to the terminal) so you can see exactly which
tools fired.

**Persistence & multi-user.** Conversation state is checkpointed in Postgres
([core.checkpoint.pg_checkpointer](src/core/checkpoint/pg_checkpointer.py)) keyed per
authenticated user + browser session (`thread_id = "<user>:<session_hash>"`), so history
survives restarts. Login is controlled by `CHAT_AUTH` (`user:secret` pairs; the secret may be a
bcrypt hash or plaintext) — unset means open access, fine for localhost only.

### Run it locally (two terminals)

```bash
MCP_TRANSPORT=http make mcp-serve   # terminal 1 — the gateway over HTTP
make mcp-chat                       # terminal 2 — prints a local Gradio URL
```

### Run it as Docker services

`make chat` starts two compose services: **`mcp-gateway`** (the gateway over HTTP, internal to
`gene-net`, no host port) and **`chat`** (the Gradio UI on `http://localhost:7860`). The chat
service reaches the gateway at `http://mcp-gateway:8765/mcp` and sends the shared
`MCP_GATEWAY_TOKEN` as a bearer token. Both are also started by `make up`. The `chat` Docker
stage installs the `chat` dependency group (`gradio` — the Gradio UI is chat-only), which the
base image's `--no-dev` build omits. `langchain-ollama` is **not** chat-only — it's a main
dependency, because the pipeline's `InvestigatorAgent` uses it too — so it ships in every
service image.

---

## See also

- [mcp_tutorial.md](mcp_tutorial.md) — the step-by-step walkthrough: start the gateway,
  connect a client, run the chat assistant.
- [components.md](components.md#a-note-on-mcp-servers) — how the same connectors are consumed
  in-process by the pipeline.
- [data_sources.md](data_sources.md) — the connectors, classification, and licensing gates.
- [faq.md](faq.md) — nuances and easy-to-get-wrong points.
