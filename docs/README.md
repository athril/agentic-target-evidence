# Documentation — Agentic Target Evidence

**Agentic Target Evidence** is a multi-agent system that gathers and interprets evidence on
whether a gene is a viable drug target for a disease. Given a `(gene, disease, direction)`
triple it retrieves evidence from 30+ biomedical sources, screens and interprets it
through **six independent lenses**, and produces a provenanced **dossier** — a consensus
verdict, a single 0–100 suitability score, per-lens narratives, and a categorized evidence
list. The same connectors are also exposed as a standalone **MCP gateway** and a **chat
assistant** for ad hoc use outside a full run.

> Every verdict is **LLM-generated over retrieved evidence** — a preliminary research aid,
> not ground truth. See [NOTICE.md](../NOTICE.md) for the full disclaimer, licenses, and data
> notices.

This doc set is grounded in the current code under [src/](src/). It describes how the system
works today; infrastructure that exists but is **not** on the live path is called out where it
appears rather than mixed into how the system works today.

---

## The documents

| Doc | What it covers |
|---|---|
| [tutorial.md](tutorial.md) | **Start here as a user.** Set up, run an analysis, and read the resulting dossier and traces. |
| [architecture.md](architecture.md) | The full picture: what the system does, process topology, the verified pipeline graph, HITL, capabilities, and observability. |
| [components.md](components.md) | A directory-by-directory tour of `src/` — schemas, harness, core, services, and the MCP data layer. |
| [agents.md](agents.md) | Every agent by role — orchestration, retrieval, screening, interpretation, challenge, synthesis — and how retrieval routes to lenses. |
| [lenses.md](lenses.md) | The interpretation layer: what each lens reads, the verdict schema, per-lens axes/conventions, cross-lens reconciliation, and the suitability score. |
| [data_sources.md](data_sources.md) | The biomedical source connectors, the node each feeds, sensitivity classification, and commercial licensing gates. |
| [restricted.md](restricted.md) | Step-by-step setup for the four gated/restricted sources (OMIM, SCImago SJR, GBD, TTD) — API keys, data downloads, verification. |
| [mcp_gateway.md](mcp_gateway.md) | Reference for the MCP gateway and chat assistant — exposure model, security, transports, discovery internals. |
| [mcp_tutorial.md](mcp_tutorial.md) | **Start here for the gateway/chat as a user.** Step-by-step: stand up the gateway, connect a client, run the chat assistant. |
| [developers.md](developers.md) | Extension points (new provider / source / lens / capability / skill), testing conventions, and the "don't build on this" list. |
| [faq.md](faq.md) | Nuances and easy-to-get-wrong points, collected as Q&A — start here if something about the system surprised you. |

---

## Reading paths

- **"I just want to run it"** → [tutorial.md](tutorial.md) → [lenses.md](lenses.md) (to
  understand the verdict).
- **"I want to understand the design"** → [architecture.md](architecture.md) →
  [components.md](components.md) → [agents.md](agents.md) → [lenses.md](lenses.md).
- **"I want to contribute"** → [developers.md](developers.md) (+ the layer docs it links).
- **"I just want to poke at one source"** → [mcp_tutorial.md](mcp_tutorial.md).
- **"I want to enable OMIM/SCImago/GBD/TTD"** → [restricted.md](restricted.md).
- **"Something about this surprised me"** → [faq.md](faq.md).

---

## Three things that are easy to get wrong

These come up across the docs — short version here, full answer in [faq.md](faq.md):

1. **Two graphs, different things** — the live orchestration graph vs. the unwired
   knowledge-graph scaffolding. [faq.md](faq.md#whats-the-difference-between-the-orchestration-graph-and-the-knowledge-graph).
2. **The pipeline consumes its connectors in-process; the gateway is a second, additive
   path** that never touches the live run. [faq.md](faq.md#does-the-pipeline-talk-to-the-mcp-gateway).
3. **A distributed (A2A) deployment is scaffolded but not how it runs today.**
   [faq.md](faq.md#is-the-distributed-a2a-deployment-actually-running).

---

## Conventions in this doc set

- Links to code are workspace-relative and clickable, e.g.
  [workflow.py](src/capabilities/target_validation/workflow.py); links between these docs are
  sibling files, e.g. [architecture.md](architecture.md).
- "Verified" means checked against the source in this repository, not inferred from a design
  note. (Design notes under `legacy/` are intentionally **not** used as a source of truth.)
