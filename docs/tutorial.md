# Tutorial — running an analysis and reading the result

> Part of the [docs/](README.md) documentation set. This is the conceptual,
> end-to-end walkthrough. For the internal design see [architecture.md](architecture.md);
> for what the verdicts mean see [lenses.md](lenses.md).

This page takes you from a cold checkout to reading a finished dossier for one
`(gene, disease, direction)` target.

---

## 1. What you'll get

You give the system a gene, a disease, and (optionally) a therapeutic direction — e.g.
`PNPLA3`, *Metabolic Dysfunction-Associated Steatohepatitis*, `inhibit`. It gathers
evidence from 30+ biomedical sources, screens and interprets it through six lenses,
and writes a **dossier** under `results/`: a consensus verdict, a single 0–100 suitability
score, six per-lens narratives, and a categorized, link-rich evidence list.

> **Read this first:** the dossier is an LLM-generated **research aid**, not ground truth.
> Treat every verdict as a starting point for expert review and always check the underlying
> evidence. The full disclaimer is in [NOTICE.md](../NOTICE.md).

---

## 2. Prerequisites

- **Docker + Docker Compose** (Postgres, Langfuse, OTEL, Ollama, and the app run as
  containers).
- **[uv](https://docs.astral.sh/uv/)** for Python dependency management (Python ≥ 3.12).
- An **`.env`** file — copy [.env.example](.env.example) and fill in any keys you want
  (e.g. `USPTO_API_KEY` for patents). Most sources are keyless. Cloud LLMs are optional;
  by default everything runs against the local **Ollama** model.

```bash
uv sync          # or: make install
cp .env.example .env
```

> **Windows users:** there's no `make` on Windows, so use the bundled
> [make.bat](make.bat) wrapper instead — it mirrors every target. Just drop the word
> `make` and call `make.bat` from `cmd.exe` or PowerShell: `make.bat up`, `make.bat ps`,
> `make.bat test`, `make.bat help`. The only difference is the `run` target, which takes
> **positional** arguments instead of `GENE=`/`DISEASE=`:
> `make.bat run BRCA1 "breast cancer"` (see [§4](#4-run-an-analysis)). Requires Docker
> Desktop and `uv` on your `PATH`. (See also [faq.md](faq.md#does-this-work-on-windows).)

---

## 3. Start the stack

```bash
make up           # starts infra + Langfuse + OTEL + app containers
make ps           # check everything is healthy
```

`make up` brings up Postgres/Redis/ClickHouse, the Langfuse trace UI, the OTEL collector,
Ollama, and the application containers. The Langfuse UI is at **http://localhost:3000**
(default login `admin@gtv.local` / `admin`). Selective targets exist too — `make infra`,
`make langfuse`, `make otel` — see `make help`.

> **First `make up` auto-pulls the local models.** A one-shot `ollama-pull` service downloads
> the chat/agent model and the embedding model (`OLLAMA_CHAT_MODEL` + `OLLAMA_EMBED_MODEL`,
> ~5 GB total) into a Docker volume before the `chat` and `planner` services start — so the
> first bring-up takes a few extra minutes, but no manual `ollama pull` is ever needed. The
> models are cached in the volume, so later `make up` runs are instant (the pull is a no-op).
> Override the model names in `.env`; only `make clean-volumes` (or `docker compose down -v`)
> wipes the cache.

> If a run later complains about telemetry export timeouts, it's usually "the stack isn't
> fully up yet," not a code bug — give Langfuse/ClickHouse a moment and re-check `make ps`.
> See also [faq.md](faq.md#why-did-my-run-complain-about-telemetryexport-timeouts).

---

## 4. Run an analysis

The simplest path is the CLI:

```bash
# via Make (defaults: GENE=PTPN1, DISEASE="pancreatic cancer")
make run GENE=BRCA1 DISEASE="breast cancer"

# Windows: positional args via the make.bat wrapper
make.bat run BRCA1 "breast cancer"

# or directly, with more control
uv run python run_analysis.py PNPLA3 "Metabolic Dysfunction-Associated Steatohepatitis" \
  --direction inhibit
```

Useful flags ([run_analysis.py](src/run_analysis.py)):

| Flag | Meaning |
|---|---|
| `--direction {inhibit,activate,degrade,modulate,unspecified}` | The therapeutic hypothesis; part of the run identity and stamped on every claim. |
| `--tissue`, `--population` | Optional refinements. |
| `--force-refresh` | Bypass **both** caches (evidence + LLM decisions) and re-fetch/re-reason from scratch. |
| `--resume <thread-id>` `--from-node <node>` | Restart a prior run from a specific stage (see §7). |

On start, the CLI prints a **thread ID** — save it; it's how you `--resume` later (it's also
recorded in `results/runs.json`).

### What happens during a run

The run executes the pipeline in [architecture.md §3](architecture.md): parallel
**acquisition** → **screening** (keep/drop/uncertain) → claim extraction → source-quality
scoring → the **HITL gate** → six **lenses** in parallel → an **experiment** scoring step →
**challenge** (critic + reviewer + reconciler) → an optional single **replan** → the
**report**.

> **The HITL gate via the CLI is auto-approved.** The pipeline pauses at the human-review
> gate structurally, but `run_analysis.py` approves it automatically and continues. To put
> a real human in the loop, use the planner service's `/runs/{id}/hitl/approve` endpoint
> instead ([agents.md](agents.md#orchestration--the-planner);
> [faq.md](faq.md#why-didnt-the-pipeline-pause-for-human-review-hitl-when-i-ran-it-via-the-cli)).

---

## 5. Read the dossier

All output lands under `results/`, organized by `{gene}/{disease}/{direction}`:

```
results/
├── data/{gene}/{disease}/{direction}/      # raw evidence archive + summary.csv
└── report/{gene}/{disease}/{direction}/
    ├── report.md          # the short dossier (start here)
    ├── full_report.md     # categorized kept evidence with external links
    └── lenses/            # one markdown file per lens verdict
```

`report.md` opens with the executive summary — the two headline numbers side by side:

```
✅ Overall consensus: Support (confidence 84%) | Suitability score: 75/100
```

- **Consensus verdict** — the majority view across the six lenses, with a conservative
  tie-break. It is *not* an average; where lenses disagree, the conflict is named.
- **Suitability score (0–100)** — a single number from the experiment/scoring path
  (separately from the consensus).

Below that, a **Lens Summary** gives one line per lens (`✅` support / `⚖️` neutral / `❓`
insufficient evidence) with its confidence and a one-sentence rationale, then the evidence
is split into **Literature** (with journal-quality stars, year, first author) and
**Empirical** (Open Targets, genetics, omics, functional, regulatory). To understand what
each lens evaluated and why two lenses can disagree on the same evidence, read
[lenses.md](lenses.md).

> The raw, per-source evidence (abstracts, patent records, trial records, genetics/omics
> data) is under `results/data/…` with a `summary.csv`. Nothing under `results/` is ever
> deleted or used as a cache — it's your audit trail.

---

## 6. Audit how a conclusion was reached

Every LLM call, tool call, and agent message is a **Langfuse** trace at
**http://localhost:3000**, grouped into one project per `(gene, disease)` and keyed by the
run's `trace_id` (= the thread ID). Open the trace to see exactly which evidence the model
saw and what each agent produced.

---

## 7. Re-running without starting over

Runs are checkpointed in Postgres, so you can restart from any stage instead of repeating
the expensive acquisition phase:

```bash
# re-render just the report from a finished run
uv run python run_analysis.py --resume <thread-id> --from-node report

# re-run the whole reasoning phase (lenses onward)
uv run python run_analysis.py --resume <thread-id> --from-node hitl_gate
```

Valid `--from-node` targets include `report`, `gap_detection`, `experiment`, `hitl_gate`
(re-runs all six lenses), `claim_extraction`, and `screening_first`. Evidence and prior LLM
decisions are reused from the database cache unless you add `--force-refresh`. (The planner
service additionally exposes targeted `/rerun` and `/rerun-acquisition` endpoints.)

---

## 8. Alternative: ad hoc tool access via the MCP gateway

The walkthrough above runs the **full pipeline** — acquisition through six lenses to a
dossier. Sometimes you just want to poke at one source (e.g. "what does DepMap say about
this gene?") without a full run. The [MCP gateway](mcp_gateway.md) exposes every
`mcp_servers/*` connector as tools on one MCP server, additive to (not a replacement for)
the pipeline. For the step-by-step walkthrough — starting the gateway, connecting Claude
Desktop/Code, running the chat assistant, example prompts — see
**[mcp_tutorial.md](mcp_tutorial.md)**. The short version:

```bash
# terminal 1 — the gateway over HTTP
MCP_TRANSPORT=http make mcp-serve

# terminal 2 — the Gene Target Evidence Assistant (Gradio chat over Ollama)
make mcp-chat
```

`make mcp-chat` prints a local URL. Ask it something that forces a tool call, e.g. *"What's
the HGNC ID and Ensembl gene ID for BRCA1?"* or *"Is TRPC6 a common essential gene per
DepMap?"* — each reply lists the tools it called, and terminal 1's logs
(`tool=<name> duration_ms=<ms> outcome=ok`) confirm which fired. To run it as a Docker service
instead (Gradio UI on `http://localhost:7860`, talking to an internal `mcp-gateway` service),
use `make chat`.

---

## Next steps

- [lenses.md](lenses.md) — what each lens evaluates and how disagreement is surfaced.
- [architecture.md](architecture.md) — the full pipeline and design.
- [mcp_tutorial.md](mcp_tutorial.md) — the gateway and chat assistant, step by step.
- [faq.md](faq.md) — nuances and easy-to-get-wrong points.
- [data_sources.md](data_sources.md) — where the evidence comes from and licensing gates.
- [mcp_gateway.md](mcp_gateway.md) — ad hoc tool access outside the pipeline.
- [developers.md](developers.md) — extending the system.
