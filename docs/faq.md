# FAQ — nuances and easy mistakes

> Part of the [docs/](README.md) documentation set. This page collects the gotchas,
> "easy to get wrong" notes, and common-confusion points that used to be scattered as
> asides across the other docs. Each answer is a short version; the linked section is
> the authoritative, detailed source — if the two ever disagree, trust the linked doc.

---

## Architecture & the pipeline

### What's the difference between the "orchestration graph" and the "knowledge graph"?

Two unrelated things share the word "graph":

- The **orchestration graph** is the LangGraph `StateGraph` in
  [target_validation/workflow.py](src/capabilities/target_validation/workflow.py). This
  *is* the pipeline — see [architecture.md §3](architecture.md).
- The **knowledge graph** ([src/services/knowledge_graph/](src/services/knowledge_graph/) +
  [src/schemas/knowledge_graph.py](src/schemas/knowledge_graph.py)) is **unwired**
  scaffolding for the planned `target_prioritization` capability. No pipeline node
  builds, queries, or ingests it.

A third thing is sometimes confused with both: **SPOKE**, an *external* precomputed
biomedical knowledge graph, but it's consumed as an ordinary data source via its
`tools.py` — it has nothing to do with `services/knowledge_graph/`.

### Is the distributed (A2A) deployment actually running?

No. The pipeline runs every agent **in-process** — the planner builds one LangGraph and
`await`s each agent as a direct Python coroutine. The agent-to-agent transport
(`src/core/a2a/`: FastAPI server, `A2AClient`, mTLS, the `agents-knowledge` /
`agents-reasoning` / `report` containers) is real, built code, but the live graph never
constructs an `A2AClient` or sends a network message — it's scaffolding for a planned
distributed deployment, exercised only by unit tests.

One thing *is* live despite that: the `AgentMessage` schema
([src/schemas/messages.py](src/schemas/messages.py)) that the A2A transport would carry.
Every agent's task and result is already an `AgentMessage` in-process, appended to
`state["messages"]` for the audit trail. The schema and the network transport are
independent — the schema isn't future work, only the wire transport is.

### Why does `source_quality` run after claim extraction, and `experiment` run before challenge?

Because **role group ≠ pipeline position**. The folder an agent lives in names its role,
not when it runs:

- `SourceQualityAgent` lives in the *screening* role group, but the node runs **after**
  `claim_extraction` in the actual graph.
- `ExperimentAgent` lives in the *synthesis* role group, but the `experiment` node runs
  **before** the challenge nodes (critic/reviewer/reconciler).

The authoritative order is the graph diagram in
[architecture.md §3](architecture.md), not the directory an agent's code happens to sit in.

### How many times can the pipeline replan / loop back to the HITL gate?

At most **once**. `gap_detection` may route back to `hitl_gate` when it finds gaps, but
`replan_count <= 1` is enforced — after one replan it always proceeds (via the `investigator`
node) to `report`. Note the `proceed` branch is *not* a loop: the `investigator` runs a single
bounded tool-calling pass to close named gaps, then falls straight through to `report`. See
[architecture.md §3](architecture.md).

### Why are there two screening passes, and what's different about the second one?

The two passes target different *items*, not new evidence text in general. Pass 1
(`screening_first`) judges every item once, on whatever text was fetched at acquisition
(for literature, that's already the full abstract). Pass 2 (`screening_second`)
re-screens **only** the items still `uncertain` after pass 1 (plus any
`knowledge_extraction` re-flagged for a retraction/erratum/wrong-population marker) —
and for those, it first downloads the PMC Open Access full-text body (capped at
`SCREEN_FULLTEXT_MAX_CHARS`, default 8 000 chars) and injects it into the pass-2 prompt
alongside the abstract. So pass 2 is a genuinely richer second look at the items most
worth one, not a re-run of pass 1. Items with no PMID or no OA full text stay on the
abstract and aren't re-screened; items already decided keep/drop in pass 1 are untouched.
Full detail: [architecture.md §3](architecture.md), [agents.md](agents.md#screening--turning-raw-evidence-into-typed-scored-claims).

### Does the pipeline talk to the MCP gateway?

No — and this is the single most common misreading of the codebase. The pipeline
imports `mcp_servers/*/tools.py` functions **directly as in-process Python**; it never
speaks the MCP protocol. The MCP gateway is a **second, additive** path that composes
the same sources' `server.py` wrappers into one server for ad hoc use *outside* a run
(an external MCP host, or the bundled chat assistant). See
[components.md#a-note-on-mcp-servers](components.md#a-note-on-mcp-servers) for the full
distinction, and [mcp_tutorial.md](mcp_tutorial.md) to actually use the gateway.

### What's the difference between a "connector folder" and a "named source"?

The folder count, the named-source count, and the acquisition-node count are all
different numbers, and that's by design, not an inconsistency:

- A connector **folder** under `src/mcp_servers/` sometimes bundles several upstream
  APIs — e.g. `ontology` = HGNC + Mondo + Monarch; `gnomad` = gnomAD + ClinVar.
- `uniprot` and `chembl` are separate folders (distinct upstream APIs) chained together
  by the single `druggability` service.
- The ten **acquisition nodes** are fewer than the connector folders, because one node
  (e.g. `genetics`) draws on roughly ten connectors at once.
- `internal_data` is the one connector with no `server.py` — it's `SENSITIVE` and can
  never be reached over MCP (see below).

Full mapping: [data_sources.md](data_sources.md).

---

## MCP gateway, chat assistant & using the tools

### I don't want to run the full pipeline — how do I just ask the tools a one-off question?

Use the **MCP gateway**: it composes every public source connector under
`src/mcp_servers/` (26 connector folders, 30+ named sources, ~44 read-only tools) into one
MCP server, separate from the validation pipeline. Three ways in — the bundled chat
assistant (no MCP client needed, just a browser tab), Claude Desktop/Code over stdio, or
any other MCP client over HTTP. Full walkthrough: [mcp_tutorial.md](mcp_tutorial.md).

### How can my own agents call these tools programmatically?

Point any MCP client at the gateway over HTTP — start it with `MCP_TRANSPORT=http make
mcp-serve`, then connect with `{"url": "http://127.0.0.1:8765/mcp"}` (add a bearer-token
`headers` entry if `MCP_GATEWAY_TOKEN` is set). This project's own `investigator` node
does exactly that: it runs a ReAct loop that picks tools at reasoning time, so it consumes
the gateway's tool schemas over MCP instead of importing `tools.py` directly — a working
example to copy. See [mcp_gateway.md#the-chat-assistant-a-gateway-client](mcp_gateway.md#the-chat-assistant-a-gateway-client)
and [mcp_tutorial.md §4b](mcp_tutorial.md#4b-your-own-http-client).

### Why can't I reach `internal_data` over the MCP gateway?

By design. `internal_data` exposes arbitrary read-only SQL against the internal
Postgres, and everything from it is classified `SENSITIVE`. It has no `server.py` at
all, so gateway discovery skips it structurally — there's no flag or env var that turns
it on. As defense-in-depth, the gateway also raises if any discovered source's name ever
matches the `SENSITIVE` set. See
[mcp_gateway.md#security-internal_data-is-never-exposed](mcp_gateway.md#security-internal_data-is-never-exposed).

### Where do I find a step-by-step walkthrough of the gateway and chat assistant?

[mcp_tutorial.md](mcp_tutorial.md) — start the gateway, connect Claude Desktop/Code or
the chat assistant, and run example prompts. [mcp_gateway.md](mcp_gateway.md) is the
reference doc (security model, transports, discovery internals) if you need the "why."

---

## Lenses, verdicts & scoring

### The lens markdown files under `results/.../lenses/` look incomplete mid-run — is that a bug?

No. Each per-lens file is drafted and then **revised in place** as the critic/reviewer
agents act on it, so its content can legitimately change between writes during a run.
Only the version present **after the run has fully completed** is authoritative — don't
read or act on a lens file while a run is still in progress. See
[lenses.md](lenses.md#how-it-surfaces-in-the-dossier).

### Does `insufficient_evidence` mean the lens found evidence against the target?

No. `insufficient_evidence` is a distinct, first-class outcome meaning "no evidence of
this lens's type passed screening" — it is **not** a negative finding, and shouldn't be
read as the lens leaning `oppose`. See [lenses.md](lenses.md#what-a-verdict-contains).

### Why does the safety lens's `toxicity` axis look inverted?

Polarity on that one axis is intentionally flipped: `verdict=true` means the safety
profile is **acceptable** (not a liability); `verdict=false` means there **is** a safety
concern. Every other axis across all six lenses follows the opposite convention
(`true` = favourable). See [lenses.md](lenses.md#per-lens-axes-and-conventions).

---

## Running the system

### Why did my run complain about telemetry/export timeouts?

Almost always "the stack isn't fully up yet," not a code bug. Give Langfuse/ClickHouse a
moment after `make up` and re-check with `make ps` before re-running. See
[tutorial.md §3](tutorial.md#3-start-the-stack).

### Why didn't the pipeline pause for human review (HITL) when I ran it via the CLI?

It did pause structurally — the graph always hits the `hitl_gate` interrupt — but
`run_analysis.py` (the CLI path, `make run`) **auto-approves** it immediately and
resumes, so there's no visible pause. To get a real human-in-the-loop gate, use the
planner service instead: `GET /runs/{id}/hitl` to review, `POST
/runs/{id}/hitl/approve` (with optional per-evidence `overrides`) to resume. See
[tutorial.md §4](tutorial.md#4-run-an-analysis) and
[agents.md](agents.md#orchestration--the-planner).

### Does this work on Windows?

Yes, via the bundled [make.bat](make.bat) wrapper, which mirrors every `make` target —
drop the word `make` and call `make.bat` instead (`make.bat up`, `make.bat test`, …).
The one difference is the `run` target, which takes positional arguments instead of
`GENE=`/`DISEASE=`: `make.bat run BRCA1 "breast cancer"`. Requires Docker Desktop and
`uv` on your `PATH`. See [tutorial.md §2](tutorial.md#2-prerequisites).

### Where can I find the full list of sources, and where does a run's output actually land?

| Path | What's there | Notes |
|---|---|---|
| `results/report/{gene}/{disease}/{direction}/report.md` | The short dossier: consensus verdict, suitability score, one-line-per-lens summary | Start here |
| `.../report/.../full_report.md` | Every *kept* evidence item, categorized (Literature / Empirical) with external links | |
| `.../report/.../lenses/` | One markdown file per lens verdict (six files) | Only authoritative once the run has fully completed — see [the lens-file question above](#the-lens-markdown-files-under-resultslenses-look-incomplete-mid-run--is-that-a-bug) |
| `results/data/{gene}/{disease}/{direction}/{source_type}/` | Archived raw fetched files per source — e.g. `pubmed/` holds the raw PubMed records/abstracts, `opentargets/`, `clinicaltrials/`, etc. | One subfolder per `source_type`; this is where to look for what a specific source actually returned |
| `.../data/.../summary.csv` | Every evidence row: `source`, classification, screening verdict, and a path back to its archived raw file | The index that ties a row to its file in the `source_type/` folder above |

See [tutorial.md §5](tutorial.md#5-read-the-dossier) for the full walkthrough.

### Is anything under `results/` ever deleted or used as a cache?

No. Caching happens at the **database** layer only (`EvidenceRow` + `llm_cache`, keyed
by deterministic fingerprints) — `results/` is purely an audit trail of every run's raw
evidence and rendered dossiers, never consulted or pruned by the pipeline itself. See
[tutorial.md §5](tutorial.md#5-read-the-dossier).

---

## See also

- [README.md](README.md) — the documentation index and reading paths.
- [architecture.md](architecture.md), [developers.md](developers.md) — the authoritative
  docs most answers above link back into.
