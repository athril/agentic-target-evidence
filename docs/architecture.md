# Architecture

> Part of the [docs/](README.md) documentation set. Start at the [index](README.md) if you're new here.

This document is the full-picture view of **Agentic Target Evidence** — a multi-agent
system that gathers and interprets evidence about whether a gene is a viable drug
target for a disease. Everything below is grounded in the current code under
[src/](src/); where a piece of infrastructure exists but is *not* on the live path, it
is called out explicitly so you don't build on a wrong assumption.

---

## 1. What the system does

Given a `(gene, disease, direction)` triple — for example `PNPLA3`, *Metabolic
Dysfunction-Associated Steatohepatitis*, `inhibit` — the system:

1. **Retrieves** evidence from 30+ biomedical data sources (literature, patents,
   clinical trials, genetics, omics, functional genomics, druggability, regulatory).
2. **Screens** that evidence with an LLM (keep / drop / uncertain), extracts atomic
   claims, and scores each source's quality.
3. **Interprets** the kept evidence through **six independent lenses** (genetics,
   biology, safety, clinical, commercial, regulatory), each emitting a structured
   verdict.
4. **Challenges** the result (a critic, a reviewer, and a deterministic cross-lens
   reconciler) and optionally **replans** once if gaps are found.
5. **Synthesizes** a human-readable **dossier** — a consensus verdict, a single 0–100
   suitability score, per-lens narratives, and a categorized evidence list.

The output is explicitly a **preliminary research aid**, not ground truth — every
verdict is LLM-generated and meant to accelerate expert review, not replace it (see
[NOTICE.md](../NOTICE.md) for the full disclaimer).

---

## 2. How it runs — entry surfaces

The pipeline runs **single-process**: an entry point builds **one in-process LangGraph** and
calls every agent as a direct Python coroutine. The graph is assembled by `build_graph()` in
[src/capabilities/target_validation/workflow.py](src/capabilities/target_validation/workflow.py);
agents are instantiated and awaited in the same process, and data-source calls go straight to
in-process Python functions (see §5).

The validation pipeline has two entry points:

- **CLI** — [src/run_analysis.py](src/run_analysis.py) (`make run`). Runs the whole pipeline
  end-to-end and **auto-approves** the human-in-the-loop gate.
- **Planner service** — [src/agents/planner/main.py](src/agents/planner/main.py)
  (`uvicorn agents.planner.main:app`, the single exposed port `8000`). A FastAPI app that
  drives the same graph but exposes **real HITL endpoints**, partial-rerun endpoints, and
  report retrieval.

Two further surfaces reuse the same source connectors **outside** a full run (see §5 and
[mcp_gateway.md](mcp_gateway.md)):

- **MCP gateway** — `target-evidence-mcp` ([src/mcp_gateway/server.py](src/mcp_gateway/server.py),
  `make mcp-serve`). Composes every public source connector into one MCP server for ad hoc
  use by an external MCP host (Claude Desktop/Code) or an agent.
- **Chat assistant** — `target-evidence-chat`
  ([src/mcp_gateway/chat_app.py](src/mcp_gateway/chat_app.py), `make mcp-chat` / `make chat`).
  A Gradio chat UI that talks to the gateway over MCP and lets a human query the sources
  conversationally.

> A **distributed (agent-to-agent) deployment** is scaffolded in the tree but is *not* how the
> system runs today — the live graph never sends a network message. See
> [faq.md](faq.md#is-the-distributed-a2a-deployment-actually-running). The `AgentMessage` schema that layer would carry
> **is** used everywhere in-process, though — see §9.

---

## 3. The pipeline graph (verified)

This is the actual node topology, read from the `add_node` / `add_edge` wiring at the
bottom of
[workflow.py](src/capabilities/target_validation/workflow.py) (not from a design doc):

```
START
  │
  ▼
restart_router ──(fresh run: fan out)──┐         (--resume: jump straight to any node)
  │                                     │
  ├─ literature        (agent)          │
  ├─ patent            (service)        │
  ├─ clinical_trial    (service)        │
  ├─ opentargets       (service)        │  ── all 10 acquisition nodes run in parallel,
  ├─ genetics          (agent)          │     then fan in to screening_first
  ├─ omics             (agent)          │
  ├─ functional        (service)        │
  ├─ druggability      (service)        │
  ├─ openfda           (service)        │
  └─ gbd               (service)        │
            │                           │
            ▼ (fan-in)                  │
      screening_first        (LLM keep/drop/uncertain, pass 1)
            │
      knowledge_extraction   (embed kept items, record PMC full-text URL, flag re-screens)
            │
      screening_second       (fetch PMC full text for "uncertain" items, then re-screen, pass 2)
            │
      claim_extraction        (atomic CoreClaims from kept evidence)
            │
      source_quality          (per-source SJR/quality map)  ← runs AFTER claim_extraction
            │
      hitl_gate  ◄─────────────────────────────────┐  (interrupt(); human review)
            │ (fan-out)                             │
            ├─ genetics_lens                        │
            ├─ biology_lens                         │
            ├─ safety_lens                          │  6 lenses run in parallel,
            ├─ clinical_lens                        │  then fan in to experiment
            ├─ commercial_lens                      │
            └─ regulatory_lens                      │
                  │ (fan-in)                        │
                  ▼                                 │
            experiment        (scored experiments)  │
                  │ (fan-out)                        │
            ┌─────┼─────────────┐                   │
            ▼     ▼             ▼                   │
         critic reviewer   reconciler               │  (all three in parallel)
            └─────┼─────────────┘                   │
                  ▼ (fan-in)                         │
            gap_detection                            │
                  │                                  │
          replan? ├── yes (≤1 replan) ──────────────┘
                  │
                  └── proceed
                  ▼
            investigator   (bounded ReAct loop; calls retrieval tools over MCP
                  │         via the gateway to close named gaps — enrichment only)
                  ▼
                report
                  │
                  ▼
                 END
```

Notes that are easy to get wrong (short version — full answers in [faq.md](faq.md)):

- **`source_quality` runs after `claim_extraction`**, even though `SourceQualityAgent`
  lives in the *screening* role group; **`experiment` runs mid-pipeline**, between the
  lenses and the challenge nodes, even though it lives in the *synthesis* role group.
  Role group ≠ pipeline position — see
  [faq.md](faq.md#why-does-source_quality-run-after-claim-extraction-and-experiment-run-before-challenge).
- **`hitl_gate` is the loop-back point.** `gap_detection` may route back to `hitl_gate`
  at most **once** (`replan_count <= 1`); after that it always proceeds — via the
  `investigator` node — to `report`.
- **`investigator` is the one pipeline node that speaks MCP.** On the `proceed` branch it
  runs a bounded ReAct loop that calls retrieval tools *over the MCP gateway* to resolve the
  specific gaps the review named, emitting an `investigation_summary` the report appends. It
  is **conclusion-enrichment only** (no evidence flows back into screening/lenses) and
  **degrades gracefully** — gateway down or recursion-limit hit ⇒ warn, empty summary,
  proceed. This is the lone exception to "the pipeline never speaks the MCP protocol" in §5.
- **The two screening passes target different *items*, and pass 2 sees genuinely new
  text** — see
  [faq.md](faq.md#why-are-there-two-screening-passes-and-whats-different-about-the-second-one)
  for what `knowledge_extraction` and `screening_second` actually do between the two passes.

---

## 4. The human-in-the-loop (HITL) gate

The gate is implemented in `hitl_gate_node` using LangGraph's `interrupt()`. When the
graph reaches it without `hitl_approved` set, execution **pauses** and the checkpoint
records the screened evidence awaiting review. How the pause is resolved depends on the
entry point:

| Entry point | HITL behavior |
|---|---|
| CLI ([run_analysis.py](src/run_analysis.py)) | `run_pipeline()` runs to the interrupt, then **auto-approves** (`hitl_approved=True`) and resumes — no human in the loop by default. |
| Planner service ([main.py](src/agents/planner/main.py)) | Pauses at the interrupt and sets run status `hitl_wait`. A human calls `GET /runs/{id}/hitl` to review and `POST /runs/{id}/hitl/approve` (with per-evidence `overrides`) to resume. |

---

## 5. Data acquisition: ten nodes over 30+ sources

The acquisition phase has **ten nodes**, but they draw on **27 source connectors**
under [src/mcp_servers/](src/mcp_servers/) covering 30+ named biomedical sources — so "ten
acquisition sources" is the wrong mental model. A node is a *unit of orchestration*; behind it
sit one or more source connectors. The full source-to-node mapping is in
[data_sources.md](data_sources.md).

The ten nodes split into two implementation styles by **how much orchestration each needs**:

- **Retrieval agents** (3): `literature`, `genetics`, `omics`. These do heavy work — fan out
  across **many** source connectors in parallel, merge and normalize heterogeneous records,
  resolve entities/MeSH terms, interpret constraint metrics, and emit several `EvidenceType`s
  into several state buckets. The genetics node alone orchestrates ~10 connectors (ClinGen,
  GenCC, gnomAD/ClinVar, GWAS Catalog, OMIM, Orphanet, SPOKE, ontology, Open Targets, internal
  data). That justifies the full `BaseAgent` runtime (contract, skill loading, telemetry span,
  loop guard), so they are `LiteratureAgent` / `GeneticsAgent` / `OmicsAgent`, awaited in the
  node.
- **Retrieval services** (7): `patent`, `clinical_trial`, `opentargets`, `functional`,
  `druggability`, `openfda`, `gbd`. These are thinner — a deterministic fetch-and-normalize
  over a single connector (e.g. `patent` → USPTO) or a tight fixed chain (e.g. `druggability` →
  UniProt → ChEMBL/DGIdb/TTD), with no model reasoning or skill at fetch time. A plain async
  function under [src/services/retrieval/](src/services/retrieval/) (`fetch_patents`,
  `fetch_trials`, …) is enough; the agent ceremony would be overhead. The graph calls these
  functions directly.

> Each node is **fault-isolated**: a failing source is logged in `failed_sources` and the run
> continues without it.

**On the word "MCP."** Each source folder under [src/mcp_servers/](src/mcp_servers/) holds a
`tools.py` (the real fetch logic) and usually a `server.py` (a `FastMCP` wrapper). **For all
bulk retrieval the pipeline never speaks the MCP protocol** — it imports the `tools.py`
functions directly as in-process Python (e.g. `from mcp_servers.uspto.tools import
search_patents`). The `server.py` wrappers are not dead, though: they are the units the **MCP
gateway** composes into one server for ad hoc use outside a run (§2). So `mcp_servers/` is two
things at once — an in-process data-access library for the pipeline, and an MCP surface for
the gateway. See [components.md](components.md#a-note-on-mcp-servers) and
[mcp_gateway.md](mcp_gateway.md).

> **The one exception: the `investigator` node (§3).** It needs to *choose* tools dynamically
> at reasoning time (a bounded ReAct loop), not call a fixed set, so it consumes the gateway's
> tool *schemas* over MCP — the same way the chat assistant does — rather than importing
> `tools.py`. The planner service therefore depends on the `mcp-gateway` container (it sets
> `MCP_GATEWAY_URL`); if the gateway is down the node degrades gracefully and the rest of the
> pipeline is unaffected, since nothing else routes through it.

---

## 6. Two "graphs" — don't confuse them

- **Orchestration graph** — the LangGraph `StateGraph` in `target_validation/workflow.py`.
  This *is* the pipeline (§3).
- **Knowledge graph** — [src/services/knowledge_graph/](src/services/knowledge_graph/) plus the
  [src/schemas/knowledge_graph.py](src/schemas/knowledge_graph.py) schema. **No pipeline node
  builds, queries, or ingests it.** It is scaffolding for the planned `target_prioritization`
  capability, not part of the graph in §3.

(Separately, one external data source — **SPOKE** — is itself a precomputed knowledge graph,
but it is consumed as an ordinary data source via its `tools.py`, not through
`services/knowledge_graph/`.)

---

## 7. Capabilities (workflows)

A *capability* is a top-level question the system can answer; each lives under
[src/capabilities/](src/capabilities/). **Only `target_validation` is implemented** — it is
the pipeline in §3, built from all retrieval + screening + the six lenses + challenge +
synthesis. The other three capabilities (`indication_expansion`, `competitor_monitoring`,
`target_prioritization`) are `NotImplementedError` stubs that show the intended pattern.

---

## 8. Repository structure at a glance

```
src/
├── run_analysis.py        # CLI entry point (auto-approves HITL)
├── capabilities/          # top-level workflows; target_validation/workflow.py = the graph
├── agents/                # the agents, grouped by role (planner, retrieval, screening,
│                          #   interpretation, challenge, synthesis)
├── services/              # deterministic + model-op logic the graph calls directly
│                          #   (retrieval, evidence, decision, knowledge_graph)
├── schemas/               # Pydantic/TypedDict contracts (evidence, state, verdicts, messages)
├── harness/               # the thin shared agent runtime (contract, base_agent, context, skills)
├── core/                  # cross-cutting infra (routing, persistence, checkpoint, a2a, telemetry)
├── mcp_servers/           # per-source data access (tools.py = logic; server.py = MCP wrapper)
└── mcp_gateway/           # the MCP gateway (server.py) and chat assistant (chat_app.py)
config/                    # routing.yaml, scoring.yaml, disease_tissue.yaml, disease_class.yaml,
│                          #   disease_class_rules.yaml, evidence_hierarchy.yaml
skills/                    # the LLM prompts/domain knowledge each agent loads (incl. the 6 lenses)
results/                  # all run output: results/data/… (raw) and results/report/… (dossiers)
```

For a file-by-file tour see [components.md](components.md); for the agents see
[agents.md](agents.md).

---

## 9. Observability & state

- **State.** A single `PipelineState` TypedDict
  ([src/schemas/state.py](src/schemas/state.py)) flows through every node, with LangGraph
  *reducers* deciding how each field merges across parallel/looping nodes (`_append` for
  evidence buckets, `_merge_by_lens` for verdicts, `replace_last` for stage outputs,
  `_union` for failure tracking). Every inter-agent `AgentMessage` is also appended to
  `state["messages"]` for a full audit trail — the `AgentMessage` schema is always used
  in-process, independent of the (planned) network transport.
- **Checkpointing.** State is persisted to Postgres via an async LangGraph checkpointer
  ([src/core/checkpoint/](src/core/checkpoint/)), which is what makes `--resume`
  /partial-rerun (§3, [tutorial.md](tutorial.md)) and the HITL pause possible.
- **Caching.** Evidence and LLM decisions are cached at the **database** layer
  (`EvidenceRow` + `llm_cache`), keyed by deterministic fingerprints
  ([src/schemas/evidence.py](src/schemas/evidence.py)). Re-running the same
  `(gene, disease, direction)` reuses them; `force_refresh` bypasses both.
  `results/` is **never** consulted as a cache.
- **Tracing.** Every LLM call, tool call, and agent message is recorded as a **Langfuse**
  trace (default `http://localhost:3000`), one project per `(gene, disease)`, keyed by
  `trace_id` = `run_id`. Spans nest via OpenTelemetry context propagation. See
  [src/core/telemetry/](src/core/telemetry/).
- **Routing & data safety.** The `Router` ([src/core/routing/](src/core/routing/)) picks
  an LLM per task from [config/routing.yaml](config/routing.yaml). Evidence is classified
  `SENSITIVE` / `NON_SENSITIVE`; anything from the `internal_data` source is `SENSITIVE`
  and is **always** routed to the local Ollama model, never to a cloud provider (enforced
  in `core/routing/classify.py` + `policy.py`).

---

## 10. What to read next

- [tutorial.md](tutorial.md) — run the tool and read a dossier (conceptual walkthrough).
- [components.md](components.md) — the code, directory by directory.
- [agents.md](agents.md) — every agent, its role, and its contract.
- [lenses.md](lenses.md) — what a lens is, the verdict schema, per-lens axes, and
  cross-lens reconciliation (including the single suitability score).
- [data_sources.md](data_sources.md) — the data sources, sensitivity classification, and
  licensing gates.
- [mcp_gateway.md](mcp_gateway.md) — the MCP gateway and chat assistant (reference); see
  [mcp_tutorial.md](mcp_tutorial.md) for the step-by-step walkthrough.
- [developers.md](developers.md) — extension points, testing, and the dead-code you
  should not build on.
- [faq.md](faq.md) — nuances and easy-to-get-wrong points.
