# Components

> Part of the [docs/](README.md) documentation set. For the runtime flow these
> components implement, read [architecture.md](architecture.md) first.

A directory-by-directory tour of [src/](src/). Each section describes what a layer is
responsible for and which files matter. For the agents specifically, see
[agents.md](agents.md); for the data layer, [data_sources.md](data_sources.md).

---

## `src/schemas/` — the data contracts

Pydantic models and the LangGraph state type. These are the shared vocabulary every other
layer speaks.

| File | Defines | Notes |
|---|---|---|
| [evidence.py](src/schemas/evidence.py) | `CoreClaim` (claim substrate) and `Evidence` (document-level claim); enums `DataClass`, `Direction`, `EvidenceType`, `LensTopic`; `Provenance`; the `*_fingerprint()` cache-key helpers. | `Evidence` extends `CoreClaim`. The `*_fingerprint()` helpers key the DB caches. |
| [state.py](src/schemas/state.py) | `PipelineState` (the `TypedDict` threaded through every node) and its reducers `_append`, `replace_last`, `_merge_by_lens`, `_union`. | Reducers decide how a field merges across parallel/looping nodes. |
| [verdicts.py](src/schemas/verdicts.py) | `AxisVerdict`, `LensVerdict`, `AgreementMap`, `ValidationFlag`. | The interpretation-layer output. Detailed in [lenses.md](lenses.md). |
| [messages.py](src/schemas/messages.py) | `AgentMessage` — the envelope every agent task/result uses (`from_agent`, `to_agent`, `intent`, `task_spec`, `payload`, `trace_id`). | Used live in-process *and* appended to `state["messages"]` for audit. (The network transport that could also carry it is planned, not live.) |
| [knowledge_graph.py](src/schemas/knowledge_graph.py) | `GraphNode`, `GraphEdge`. | **Not wired into the pipeline** — used only by the `services/knowledge_graph/` scaffolding for a planned capability. |

### The `PipelineState` reducers (why they matter)

When several nodes write the same state key (parallel fan-in, or a replan loop), the
reducer resolves the merge:

- `_append` — evidence buckets accumulate across retries (no dedup; dedup happens on read).
- `replace_last` — stage outputs where the newest write fully replaces the prior one.
- `_merge_by_lens` — keep the most recent `LensVerdict` per lens name (so a replan
  overwrites that lens, not duplicates it).
- `_union` — set-union for `failed_lenses` / `failed_sources` tracking.

---

## `src/harness/` — the shared agent runtime

A deliberately thin runtime that gives every agent the same guarantees. ~5 small files.

| File | Role |
|---|---|
| [base_agent.py](src/harness/base_agent.py) | `BaseAgent` (ABC). Subclasses set a class-level `contract` and implement `act()`. |
| [contract.py](src/harness/contract.py) | `AgentContract` (name, `consumes`, `produces`, `max_loops`, `skills`); `validate_inbound`/`validate_outbound`. `ServiceContract = AgentContract`. |
| [context.py](src/harness/context.py) | `RunContext` — passed to every `act()`; `select_model(classification, task)` and `load_skill(name)`. |
| [loop_guard.py](src/harness/loop_guard.py) | `LoopGuard` — per-edge loop caps + global step budget; raises `LoopLimitExceeded`. |
| [skills.py](src/harness/skills.py) | `load_skill(name)` → contents of `skills/{name}.md` (cached). Raises `SkillNotFound`. |

**The harness guarantee.** `BaseAgent.run()` wraps every agent invocation in a fixed
order (see [base_agent.py](src/harness/base_agent.py)):

1. `validate_inbound` — reject any `task_spec` key the contract didn't declare.
2. open a telemetry span (OTel + Langfuse).
3. `loop_guard.check` — enforce the per-edge `max_loops` and the run's step budget.
4. `act()` — the agent's domain logic.
5. `validate_outbound` — reject any payload key the contract didn't declare.

This is why agents can't silently pass undeclared data: the contract whitelists I/O at
both boundaries. Crucially, `ServiceContract = AgentContract` — a "service" is a role
label, **not** an exemption. Any service that calls a model (claim extraction, screening,
source quality, …) carries a contract too, so it still routes through the `Router`, gets
a span, and counts against the step budget.

---

## `src/core/` — cross-cutting infrastructure

| Path | Responsibility |
|---|---|
| [routing/](src/core/routing/) | LLM selection. `policy.py` loads [config/routing.yaml](config/routing.yaml); `router.py` picks `(provider, model)` per `(classification, task, agent)`; `classify.py` derives `SENSITIVE`/`NON_SENSITIVE` (`_SENSITIVE_AGENTS = {"internal_data"}`); `providers/` holds the provider adapters (Ollama, Bedrock, …). |
| [persistence/](src/core/persistence/) | Postgres access: `db.py` (async session), `models.py` (`EvidenceRow`, `llm_cache`, runs), `repos/` (evidence/llm_cache/runs repositories), `migrations/` (Alembic), `artifact_store.py` (writes `results/…` archives + CSV). |
| [checkpoint/](src/core/checkpoint/) | `pg_checkpointer.py` — the async Postgres LangGraph checkpointer that makes HITL-pause, `--resume`, and partial reruns possible. |
| [telemetry/](src/core/telemetry/) | Langfuse + OpenTelemetry setup; `projects.py` provisions one Langfuse project per `(gene, disease)`. |
| [a2a/](src/core/a2a/) | The agent-to-agent transport (server/client/mTLS/runner). **Built but off the live path** — scaffolding for a distributed deployment. |
| `exceptions.py` | Typed errors the harness raises: `ContractViolation`, `LoopLimitExceeded`, `SkillNotFound`, … |
| `batching.py` | `pack_batches` — token-budget-aware batching used by screening and claim extraction. |
| `evidence_text.py` | `screenable_text` — normalizes evidence text for screening. |
| `http.py` | `get_with_retry` / `post_with_retry` — the shared retrying HTTP client used by `mcp_servers/*/tools.py`. |
| `json_utils.py` | `strip_json_fence` — unwraps ```` ```json ```` fences from LLM output before parsing. |

---

## `src/services/` — logic the graph calls directly

Services hold deterministic and model-op logic that the graph invokes without the full
agent ceremony (though model-op services still carry a contract — see the harness note).

| Path | Contents |
|---|---|
| [retrieval/](src/services/retrieval/) | The six data-acquisition services the graph calls directly: `fetch_patents`, `fetch_trials`, `fetch_opentargets`, `fetch_functional`, `fetch_druggability`, `fetch_openfda`. Each calls one or more `mcp_servers/*/tools.py` functions and stamps an `EvidenceType`. |
| [evidence/](src/services/evidence/) | Evidence processing: `claim_extraction.py` (atomic claims), `entity_resolution.py`, `constraint_interpret.py` (gnomAD LoF/missense reading), `clinical_trial_interpret.py`, `commercial_interpret.py`, `mouse_phenotype.py`, `disease_tissue.py`. Plus the disease-class generalization layer — `disease_class.py`, `disease_class_rules.py`, `evidence_hierarchy.py` (see [lenses.md](lenses.md#generalizing-across-disease-classes)). `claim_clustering.py` is island code used only by `knowledge_graph/`. |
| [decision/](src/services/decision/) | `reconciler.py` (deterministic cross-lens `AgreementMap`) and `suitability.py` (the Mendelian-causality score floor). Both detailed in [lenses.md](lenses.md). |
| [knowledge_graph/](src/services/knowledge_graph/) | `builder`/`ingest`/`query`/`export`. **Unwired** — scaffolding for the planned `target_prioritization` capability. |
| `_common.py` | `make_provenance(...)` and shared service helpers. |

---

## `src/mcp_servers/` — the data layer

One folder per source connector (the full list and licensing are in
[data_sources.md](data_sources.md)). Each folder has the same shape:

- `tools.py` — **the real fetch logic** (HTTP calls, parsing, typed records). This is what
  the pipeline imports and runs in-process.
- `server.py` — a `FastMCP` wrapper exposing those tools over the MCP protocol. Present for
  every public source (`internal_data` deliberately has none — see below).

### A note on MCP servers

`mcp_servers/` serves **two** consumers, and it's worth being precise about which speaks MCP:

- **The pipeline mostly does not.** During a run it imports the `tools.py` functions directly
  as in-process Python — e.g. `from mcp_servers.uspto.tools import search_patents` — never over
  a protocol. For the pipeline, `mcp_servers/` is just a typed data-access library. The lone
  exception is the synthesis-phase `investigator` node, which speaks MCP to the **gateway** (not
  to a `server.py` directly) because its ReAct loop chooses tools at reasoning time — see
  [agents.md](agents.md#synthesis--scoring-gap-gating-and-the-dossier) and
  [mcp_gateway.md](mcp_gateway.md).
- **The MCP gateway does.** `mcp_gateway/server.py` discovers every `server.py` wrapper and
  mounts it into one live MCP server ([mcp_gateway.md](mcp_gateway.md)). So the `server.py`
  wrappers are **not dead code** — they are the live MCP surface, just one reached by the
  gateway rather than the pipeline. (Discovery is dynamic, via `pkgutil` + `importlib`, so a
  plain `grep` for `server.py` references will wrongly report them unused.)

The one deliberate gap: `internal_data` has a `tools.py` (used in-process) but **no**
`server.py`, so it can never be exposed over MCP — see
[data_sources.md](data_sources.md#sensitivity-classification--data-safety) and
[mcp_gateway.md](mcp_gateway.md#security-internal_data-is-never-exposed).

---

## `src/mcp_gateway/` — the standalone MCP surface

The additive, out-of-pipeline exposure of the connectors above. Two files:

- [server.py](src/mcp_gateway/server.py) — the **gateway** (`target-evidence-mcp`): discovers and mounts
  every public `mcp_servers/*/server.py` into one MCP server, gating- and SENSITIVE-aware, over
  stdio or HTTP.
- [chat_app.py](src/mcp_gateway/chat_app.py) — the **Gene Target Evidence Assistant**
  (`target-evidence-chat`): a Gradio chat UI driving a LangGraph react agent over local Ollama that calls
  the gateway's tools.

Neither is imported by the pipeline. Full reference: [mcp_gateway.md](mcp_gateway.md).

---

## Where to go next

- [agents.md](agents.md) — the agents that use this harness and these services.
- [lenses.md](lenses.md) — the interpretation layer (`verdicts.py` + the lens agents).
- [data_sources.md](data_sources.md) — every `mcp_servers/` integration and its license.
- [mcp_gateway.md](mcp_gateway.md) / [mcp_tutorial.md](mcp_tutorial.md) — the gateway and
  chat assistant that expose these connectors outside the pipeline (reference / walkthrough).
