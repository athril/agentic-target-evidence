# Agentic Target Evidence

|              |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Testing**  | [![CI](https://github.com/athril/agentic-target-evidence/actions/workflows/ci.yml/badge.svg)](https://github.com/athril/agentic-target-evidence/actions/workflows/ci.yml)                                                                                                                                                                                                                                                                                                                                                                        |
| **Tooling**  | [![Python](https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white)](https://www.python.org/) [![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff) [![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue)](https://mypy-lang.org/)                  |
| **Stack**    | [![LangGraph](https://img.shields.io/badge/LangGraph-orchestration-1C3C3C?logo=langchain&logoColor=white)](https://github.com/langchain-ai/langgraph) [![MCP](https://img.shields.io/badge/MCP-data%20layer-000000)](https://modelcontextprotocol.io/) [![Langfuse](https://img.shields.io/badge/Langfuse-tracing-2563EB)](https://langfuse.com/) [![Postgres](https://img.shields.io/badge/Postgres-checkpointing-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)                                                             |
| **Meta**     | [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE) [![Contributions welcome](https://img.shields.io/badge/Contributions-welcome-brightgreen.svg)](CONTRIBUTING.md) [![Code of Conduct](https://img.shields.io/badge/Code%20of%20Conduct-Contributor%20Covenant-blue.svg)](CODE_OF_CONDUCT.md)                                                                                                                                                                                                                                                                                                                                                      |

A multi-agent system that gathers and interprets evidence on whether a gene is a viable
drug target for a disease. Given a `(gene, disease, direction)` triple — e.g. `BRCA1`,
*breast cancer*, `inhibit` — it retrieves evidence from ~two dozen biomedical sources,
screens and interprets it through **six independent lenses** (genetics, biology, safety,
clinical, commercial, regulatory), and produces a provenanced **dossier**: a consensus
verdict, a single 0–100 suitability score, per-lens narratives, and a categorized,
link-rich evidence list.

Every source connector — DepMap, gnomAD, ClinicalTrials.gov, OpenTargets, PubMed, FAERS, and
~20 more — lives once under [src/mcp_servers/](src/mcp_servers/) and is consumed two ways:
**in-process** by the pipeline's agents (fast, typed, no protocol tax), or through the
**MCP gateway**, which composes the same connectors into **one MCP server** exposing ~40
read-only tools to any MCP host — Claude Desktop, Claude Code, your own agent — for ad hoc
lookups outside a full run. A bundled **chat assistant** offers the same lookups from a
browser. See [§ MCP gateway & servers](#mcp-gateway--servers) below.

Built on LangGraph (orchestration) + MCP (the data layer), with full tracing (Langfuse +
OpenTelemetry), Postgres-backed checkpointing, and configurable local/cloud LLM routing.

> **Every verdict is LLM-generated over retrieved evidence — a preliminary research aid,
> not ground truth.** It is built to accelerate the evidence-gathering phase of target
> validation, not to replace expert review. See [NOTICE.md](NOTICE.md) for the full
> disclaimer, licenses, and data notices.

---

## Quickstart

```bash
uv sync                  # Python ≥ 3.12, via uv: https://docs.astral.sh/uv/
cp .env.example .env      # fill in any keys you want; most sources are keyless

make up                   # infra + Langfuse + OTEL + the app, as containers
make run GENE=BRCA1 DISEASE="breast cancer"
```

Output lands under `results/report/{gene}/{disease}/{direction}/report.md`. The Langfuse
trace UI is at `http://localhost:3000`. Windows: use `make.bat` instead of `make` — see
[docs/tutorial.md](docs/tutorial.md#2-prerequisites).

Don't want a full run? Ask one-off questions against the same connectors (e.g. *"What's
TRPC6's DepMap dependency score?"*) via the bundled chat UI or Claude Desktop/Code — see
[docs/mcp_tutorial.md](docs/mcp_tutorial.md).

---

## MCP gateway & servers

Every biomedical source connector lives under [src/mcp_servers/](src/mcp_servers/) as a
self-contained `tools.py` + MCP `server.py` pair — **27 source connectors, ~46 read-only
tools**, spanning **30+ named public sources** (some connector folders bundle more than one
upstream API — see [docs/data_sources.md](docs/data_sources.md)) plus your own internal data:

ChEMBL · ClinGen · ClinicalTrials.gov · ClinVar · DepMap · DGIdb · ENCODE · Expression Atlas ·
GBD (IHME) · GenCC · gnomAD · Google Patents · GTEx · GWAS Catalog · HGNC · HPA · IMPC ·
Monarch Initiative · MONDO · OMIM · OpenAlex · OpenFDA · OpenTargets · Orphanet · Project Score ·
PubMed · SCImago (SJR) · SPOKE · TTD · UniProt · USPTO · internal data (your org's private
tables)

Full per-source details (what each provides, licensing/gating status) in
[docs/data_sources.md](docs/data_sources.md). The
[MCP gateway](src/mcp_gateway/) ([src/mcp_gateway/server.py](src/mcp_gateway/server.py))
dynamically discovers and composes all of them into **one** MCP server, with no
hand-maintained registry — drop a new `src/mcp_servers/<name>/server.py` in and it's mounted
automatically (subject to feature gates; `internal_data` is never mounted).

Three ways to reach it, without running the full pipeline:

| Client | What it is |
|---|---|
| **Chat assistant** | A Gradio chat UI backed by a local Ollama model — `make chat` or see [docs/mcp_tutorial.md](docs/mcp_tutorial.md). |
| **Claude Desktop / Claude Code** | Connect over stdio alongside your other MCP servers. |
| **Any other MCP client** | Call the tools programmatically over HTTP (bearer-token auth via `MCP_GATEWAY_TOKEN`). |

For all bulk retrieval the pipeline never talks to the gateway — it imports each `tools.py`
directly, keeping the hot path free of protocol overhead. The gateway is a second, additive
surface onto the same connectors, for ad hoc use outside a full run. (The one in-pipeline
gateway client is the synthesis-phase **Investigator** agent, which calls retrieval tools
over MCP to close evidence gaps before the report; it degrades gracefully if the gateway is
down.) For the design — exposure model, security, transports, discovery internals — see
[docs/mcp_gateway.md](docs/mcp_gateway.md); for a step-by-step walkthrough, see
[docs/mcp_tutorial.md](docs/mcp_tutorial.md).

---

## Documentation

Full documentation lives in [docs/](docs/README.md). Start there — it has reading paths
for "I just want to run it," "I want to understand the design," and "I want to
contribute." A few entry points:

| Doc | What it covers |
|---|---|
| [docs/tutorial.md](docs/tutorial.md) | Run an analysis and read the resulting dossier. |
| [docs/mcp_tutorial.md](docs/mcp_tutorial.md) | Ad hoc tool access via the MCP gateway and chat assistant, step by step. |
| [docs/mcp_gateway.md](docs/mcp_gateway.md) | MCP gateway reference: exposure model, security, transports, discovery internals. |
| [docs/data_sources.md](docs/data_sources.md) | Every source connector, what it provides, and its licensing/gating status. |
| [docs/restricted.md](docs/restricted.md) | Step-by-step setup for gated sources (OMIM, SCImago, GBD, TTD): API keys, data downloads, verification. |
| [docs/architecture.md](docs/architecture.md) | The full design: pipeline graph, HITL, capabilities, observability. |
| [docs/developers.md](docs/developers.md) | Extension points and conventions for contributing. |
| [docs/faq.md](docs/faq.md) | Nuances and easy-to-get-wrong points, as Q&A. |

---

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, conventions,
and how to submit a change, and [docs/developers.md](docs/developers.md) for extension
points. Please also read our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## License

Apache-2.0. Some data sources carry narrower terms and are gated off by default — see
[docs/data_sources.md](docs/data_sources.md#licensing--commercial-gating) for the reference
table, [docs/restricted.md](docs/restricted.md) for step-by-step setup, and
[NOTICE.md](NOTICE.md) for the full disclaimer and per-source licenses.

---

## Contact

**Patryk Orzechowski**

[![Email](https://img.shields.io/badge/Email-D14836?style=flat&logo=gmail&logoColor=white)](mailto:patryk.orzechowski@gmail.com)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0A66C2?style=flat&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/patrykorzechowski/)
[![Google Scholar](https://img.shields.io/badge/Google%20Scholar-4285F4?style=flat&logo=google-scholar&logoColor=white)](https://scholar.google.com/citations?user=QVMy3JUAAAAJ&hl=en)
