# MCP tutorial — gateway, clients, and the chat assistant

> Part of the [docs/](README.md) documentation set. This is the conceptual, step-by-step
> walkthrough. For the *why* — exposure model, security, transports, discovery internals —
> see the reference doc, [mcp_gateway.md](mcp_gateway.md). For the full evidence-gathering
> pipeline (a different, additive thing), see [tutorial.md](tutorial.md).

This page takes you from a cold checkout to asking the **MCP gateway** a one-off question
about a gene — without running the full validation pipeline — either through the bundled
chat assistant or through Claude Desktop/Code directly.

---

## 1. What you'll get

Sometimes you don't want a full dossier — you want one answer: *"What's the HGNC ID for
BRCA1?"*, *"Is TRPC6 a common essential gene per DepMap?"*, *"What does ClinVar say about
PNPLA3?"* The **MCP gateway** composes every public source connector under
[src/mcp_servers/](src/mcp_servers/) — ~26 sources, ~44 read-only tools — into **one** MCP
server that any MCP client can call. Three ways to use it:

| Client | What it is | When to use it |
|---|---|---|
| **Chat assistant** | A Gradio chat UI (the "Gene Target Evidence Assistant") backed by a local Ollama model | No separate MCP client to set up — just a browser tab. Best for quick, conversational lookups. |
| **Claude Desktop / Claude Code** | Your existing Claude client, connected over stdio | You're already in Claude and want gene/disease tools alongside your other MCP servers. |
| **Any other MCP client** | Your own code, another agent | You want to call the tools programmatically over HTTP. |

This is **additive** to the full pipeline, not a replacement for it — the validation
pipeline never goes through the gateway (see
[faq.md](faq.md#does-the-pipeline-talk-to-the-mcp-gateway) if that distinction is new to
you).

---

## 2. Prerequisites

- **[uv](https://docs.astral.sh/uv/)** (Python ≥ 3.12) and a `.env` file:

  ```bash
  uv sync          # or: make install
  cp .env.example .env
  ```

- The gateway itself is **stateless** — no database, no Langfuse. If all you want is
  Claude Desktop/Code talking to the gateway over **stdio**, that's all you need; skip to
  [§4](#4-connect-a-client).
- The **chat assistant** additionally needs:
  - **Postgres**, for conversation checkpointing (`make infra`, then `make db-migrate`
    once against a fresh database).
  - **Ollama** running locally (`make up` starts it as a container; or point
    `OLLAMA_BASE_URL` at your own instance) with the chat model pulled — default
    `OLLAMA_CHAT_MODEL=qwen2.5:7b-instruct-q4_K_M` ([.env.example](.env.example)).

If you've already run `make up` for the full pipeline (see [tutorial.md
§3](tutorial.md#3-start-the-stack)), all of this is already satisfied.

---

## 3. Start the gateway

```bash
# stdio (default) — for Claude Desktop/Code, which owns the process itself.
# Nothing to "start" separately; see §4a.

# HTTP — for the chat assistant or your own client
MCP_TRANSPORT=http make mcp-serve
```

`make mcp-serve` runs `target-evidence-mcp` ([src/mcp_gateway/server.py](src/mcp_gateway/server.py)).
Over HTTP it listens on `MCP_HOST`/`MCP_PORT` (default `8765`) and serves
`POST /mcp`. Leave this terminal running — the next steps connect to it.

> Discovery is dynamic: every source under `src/mcp_servers/` with a `server.py` is
> mounted automatically, gating-aware (a source whose flag is off, e.g. `OMIM_ENABLED`,
> is skipped). `internal_data` is never mounted, by design — see
> [faq.md](faq.md#why-cant-i-reach-internal_data-over-the-mcp-gateway).

---

## 4. Connect a client

### 4a. Claude Desktop / Claude Code (stdio)

No separate terminal needed — the client launches the gateway process itself. Add to your
MCP config (`claude_desktop_config.json` for Desktop, or your project's `.mcp.json` for
Code):

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

Restart the client, then ask it something that forces a tool call, e.g. *"What's the HGNC
ID and Ensembl gene ID for BRCA1?"* — you should see it list and call a tool like
`hgnc_lookup_gene` before answering.

### 4b. Your own HTTP client

With the gateway already running over HTTP (§3):

```json
{
  "mcpServers": {
    "agentic-target-validation": { "url": "http://127.0.0.1:8765/mcp" }
  }
}
```

If you set `MCP_GATEWAY_TOKEN` (recommended once you bind off `localhost` — see §6), add
`"headers": {"Authorization": "Bearer <token>"}` alongside `"url"`.

---

## 5. Run the chat assistant

### Locally (two terminals)

```bash
# terminal 1 — the gateway over HTTP (§3)
MCP_TRANSPORT=http make mcp-serve

# terminal 2 — the Gene Target Evidence Assistant
make mcp-chat
```

`make mcp-chat` runs `target-evidence-chat` ([src/mcp_gateway/chat_app.py](src/mcp_gateway/chat_app.py))
and prints a local Gradio URL. Open it and try:

- *"What's the HGNC ID and Ensembl gene ID for BRCA1?"*
- *"Is TRPC6 a common essential gene per DepMap?"*
- *"What does ClinVar say about pathogenic variants in PNPLA3?"*

Each reply shows a **Tools called:** block listing exactly which tools fired, and
terminal 1's logs (`tool=<name> duration_ms=<ms> outcome=ok`) confirm the same thing from
the gateway side. Conversation history is checkpointed in Postgres per user/session, so it
survives a restart.

### As Docker services

```bash
make chat   # starts mcp-gateway + chat; UI → http://localhost:7860
```

This starts two compose services: `mcp-gateway` (HTTP, internal to the `gene-net`
network, no host port) and `chat` (the Gradio UI, exposed on `http://localhost:7860`). Both
are also started by `make up`.

---

## 6. Securing the HTTP gateway

By default (`MCP_GATEWAY_TOKEN` unset) the HTTP gateway has **no auth** — fine for local
dev on `localhost`, not fine if `MCP_HOST` is ever bound off-loopback. To require a bearer
token:

```bash
openssl rand -hex 32   # generate a token
```

Set the same value as `MCP_GATEWAY_TOKEN` in `.env` for **both** the gateway and any
client (the bundled `chat` Docker service picks it up automatically). Restart the gateway
for it to take effect.

---

## 7. Troubleshooting

- **A tool call returns nothing for `uspto` or `omim`.** Check the gateway's startup logs
  for an API-key warning — `uspto` needs `USPTO_API_KEY`, `omim` needs `OMIM_API_KEY` *and*
  `OMIM_ENABLED=true`. See [data_sources.md §API keys](data_sources.md#api-keys).
- **A source you expected isn't in the tool list at all.** It's probably behind a
  disabled feature flag (`OMIM_ENABLED`, `SCIMAGO_SJR_ENABLED`, `TTD_ENABLED`) — discovery
  skips disabled sources so they don't clutter the list. See
  [data_sources.md §Licensing](data_sources.md#licensing--commercial-gating).
- **You expected to query your internal database through the gateway.** You can't —
  `internal_data` is deliberately never exposed over MCP. See
  [faq.md](faq.md#why-cant-i-reach-internal_data-over-the-mcp-gateway).
- For anything else, check [faq.md](faq.md) first, then the deeper reference in
  [mcp_gateway.md](mcp_gateway.md).

---

## Next steps

- [mcp_gateway.md](mcp_gateway.md) — the reference doc: exposure model, security,
  transports, dynamic discovery.
- [data_sources.md](data_sources.md) — every source the gateway exposes, and its license.
- [tutorial.md](tutorial.md) — the full evidence-gathering pipeline (a different, additive
  thing from the gateway).
- [faq.md](faq.md) — nuances and easy-to-get-wrong points.
