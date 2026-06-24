# Agents

> Part of the [docs/](README.md) documentation set. The pipeline that sequences these
> agents is in [architecture.md §3](architecture.md); the runtime they share is in
> [components.md](components.md#srcharness--the-shared-agent-runtime).

Agents live under [src/agents/](src/agents/), grouped by **role**. Every agent subclasses
`BaseAgent`, declares an `AgentContract` (whitelisting the `task_spec` keys it consumes and
the payload keys it produces), and implements `act()`. The harness wraps each call with
inbound/outbound validation, a telemetry span, and the loop guard.

> **Role group ≠ pipeline position.** The folder an agent lives in names its *role*, not
> when it runs — `source_quality` and `experiment` both run out of their group's apparent
> order. See [faq.md](faq.md#why-does-source_quality-run-after-claim-extraction-and-experiment-run-before-challenge);
> the authoritative order is the graph in [architecture.md §3](architecture.md).

---

## Orchestration — the Planner

The planner is not a `BaseAgent`; it's the thing that *builds and drives* the graph. There
are two entry points, both assembling the same graph via `build_graph()` in
[workflow.py](src/capabilities/target_validation/workflow.py):

| Entry point | File | HITL | Use |
|---|---|---|---|
| CLI | [run_analysis.py](src/run_analysis.py) | **auto-approved** | Local runs (`make run`), reruns via `--resume`/`--from-node`. |
| Service | [agents/planner/main.py](src/agents/planner/main.py) | **human endpoints** | FastAPI app (`uvicorn agents.planner.main:app`, port 8000). Exposes `/runs`, `/runs/{id}/hitl[/approve]`, `/runs/{id}/rerun`, `/runs/{id}/rerun-acquisition`, `/runs/{id}/report`. |

The planner seeds `PipelineState` (resolving gene→Ensembl and disease→EFO/MONDO IDs first
via the ontology and Open Targets tools), then runs the graph to completion (or to the
HITL interrupt).

> The planner's request models and state helpers (`RunRequest`, `HitlApproveRequest`,
> `_make_initial_state`, `_resolve_ontology_context`) live in
> [agents/planner/agent.py](src/agents/planner/agent.py); `main.py` imports them. That's all
> that file is — the older `create_app` factory it once held has been removed.

---

## Retrieval — 10 acquisition nodes over 30+ sources

These run in parallel as the first phase. "Ten sources" would be misleading: there are **ten
acquisition nodes**, and behind them sit 27 source connectors covering 30+ named biomedical
sources (the full source-to-node mapping is in [data_sources.md](data_sources.md)). The ten nodes split by **how
much orchestration each needs** — three are full `BaseAgent`s, seven are plain services the graph
calls directly (the rationale is in [architecture.md §5](architecture.md)):

| Node | Impl | Draws on | Produces (`EvidenceType`) | State bucket |
|---|---|---|---|---|
| `literature` | `LiteratureAgent` (agent) | PubMed (+ MeSH resolution) | `ARTICLE` / `ABSTRACT` | `literature_evidence` |
| `genetics` | `GeneticsAgent` (agent) | ~10 connectors (ClinGen, GenCC, gnomAD/ClinVar, GWAS, OMIM, Orphanet, SPOKE, ontology, Open Targets, internal) | `GENETICS`, `CONSTRAINT` | `genetics_evidence` |
| `omics` | `OmicsAgent` (agent) | GTEx, Expression Atlas, ENCODE, SPOKE, internal | `OMICS`, `EXPRESSION`, `REGULATORY_ELEMENT` | `omics_evidence` |
| `patent` | `fetch_patents` (service) | USPTO | `PATENT` | `patent_evidence` |
| `clinical_trial` | `fetch_trials` (service) | ClinicalTrials.gov | `CLINICAL_TRIAL` | `trial_evidence` |
| `opentargets` | `fetch_opentargets` (service) | Open Targets | `GENETICS` (+ a rich `extra` dict: tractability, safety liabilities, mouse phenotypes, known drugs) | `opentargets_evidence` |
| `functional` | `fetch_functional` (service) | DepMap, Project Score, IMPC, internal | `FUNCTIONAL_GENOMICS` | `functional_evidence` |
| `druggability` | `fetch_druggability` (service) | UniProt → ChEMBL / DGIdb / TTD | `DRUGGABILITY` | `druggability_evidence` |
| `openfda` | `fetch_openfda` (service) | OpenFDA | `REGULATORY` | `openfda_evidence` |
| `gbd` | `fetch_gbd` (service) | GBD / IHME | `EPIDEMIOLOGY` | `gbd_evidence` |

Each node is **fault-isolated**: a failing source is logged and recorded in
`failed_sources`, and the pipeline continues without it. Each also checks the per-evidence
DB cache first and skips the API on a hit (unless `force_refresh`).

### How retrieval connects to the lenses (it's via `EvidenceType`, not a direct wire)

There is **no agent→lens reference**. A retrieval source stamps every row with an
`EvidenceType`; a claim later reaches a lens iff its type is in that lens's
`LENS_EVIDENCE_TYPES` tuple (`claim_matches_lens`,
[_lens_base.py:94-111](src/agents/interpretation/_lens_base.py#L94-L111)). So the routing is
implicit and can fan one source out to several lenses:

- `FUNCTIONAL_GENOMICS` (from `functional`) → **biology + safety**
- `DRUGGABILITY` (from `druggability`) → **biology**
- `REGULATORY` (from `openfda`) → **safety + commercial + regulatory**

The full routing table is in [lenses.md](lenses.md#what-each-lens-reads). (Literature types
route differently — by per-claim `topics` tags — also covered there.)

---

## Screening — turning raw evidence into typed, scored claims

Four steps, three of them agents, between acquisition and the HITL gate:

| Node | Agent / service | Does |
|---|---|---|
| `screening_first` | `ScreeningAgent` (pass 1) | LLM verdict **keep / drop / uncertain** per evidence row, judged on the retrieved text (for literature, the abstract is already fetched at acquisition, so pass 1 sees the full abstract); writes verdicts to the LLM cache. |
| `knowledge_extraction` | `KnowledgeExtractionAgent` | For each kept item: chunks and **embeds** it (for later semantic search); for items with a freely available PubMed Central full text, upgrades `scope` to `full_text` and records the URL; and re-flags an item `uncertain` if its abstract carries a retraction/erratum/wrong-population marker. |
| `screening_second` | `ScreeningAgent` (pass 2) | For every row still `uncertain` with a PMID, fetches the PMC Open Access full-text body (`_enrich_uncertain_with_full_text` in `workflow.py`) and upgrades `scope` to `full_text`; then re-screens **only** rows that are `uncertain` **and** `scope == "full_text"`. |
| `claim_extraction` | `extract_claims` (service) | Produces atomic `CoreClaim`s from `keep` evidence — the typed substrate the lenses reason over. |
| `source_quality` | `SourceQualityAgent` | Scores each kept source's journal quality (SJR / OpenAlex), once, **after** claim extraction. Result is the `source_quality` map every lens reads. |

**Why screen twice.** The two passes target different *items*, not new evidence text. Pass 1
judges every item once. The split exists to give a **focused second decision** to the items
left `uncertain` — both those the pass-1 LLM was unsure about and those `knowledge_extraction`
re-flagged for a retraction/erratum/wrong-population marker — while leaving everything already
decided keep/drop alone.

The second look genuinely sees more than the first: before re-screening, `screening_second`
downloads the PMC Open Access body text for each `uncertain` PMID item (skipping items with no
PMID or no OA full text — those stay on the abstract and are not re-screened) and injects a
capped excerpt (`SCREEN_FULLTEXT_MAX_CHARS`, default 8 000 chars) into the pass-2 prompt as a
`<full_text>` block alongside the abstract. So pass 2 reasons over real full-text content for
the items most worth a second look, not a re-run of pass 1 on the same abstract.
(`ScreeningAgent` is a single agent invoked for both passes; `knowledge_extraction`'s own
`scope` upgrade — for `keep` items with a PMC link — is metadata-only and unrelated to this
content fetch.)

---

## Interpretation — the six lenses

Six `BaseAgent` lenses run in parallel after the HITL gate, each emitting one `LensVerdict`:
`genetics`, `biology`, `safety`, `clinical`, `commercial`, `regulatory`. They share
`run_lens()` in [_lens_base.py](src/agents/interpretation/_lens_base.py) (deserialize claims
→ filter to this lens's evidence types/topics → load the lens skill → call the LLM → parse a
`LensVerdict`). Each lens loads its domain prompt from `skills/{lens}_lens.md`.

This layer has its own document: **[lenses.md](lenses.md)** covers what each lens reads, its
axes, the verdict schema, the per-lens conventions (e.g. the safety-lens toxicity polarity),
and cross-lens reconciliation.

---

## Challenge — adversarial review

Three things run in parallel after `experiment`:

- **`CriticAgent`** — a **three-pass** audit merged into one `critiques` output
  ([critic/agent.py](src/agents/challenge/critic/agent.py)):
  1. *source-QA* — re-emits the precomputed `source_quality` per kept source (a lookup, no
     LLM call).
  2. *claim-QA* — LLM review of `extracted_claims` for contradictions, near-duplicates,
     low-confidence, and missing direction.
  3. *verdict-QA* — LLM review of the `lens_verdicts` for cross-lens inconsistency,
     overconfidence, and bias (uses the `verdict_qa` skill).
- **`ReviewerAgent`** — generates a per-stage **gap report** (`{stage, missing_aspects,
  completeness_score}`) over the stages `literature, genetics, clinical, screening,
  extraction, lenses, experiment`. It deliberately does **not** assess source quality —
  that's the Critic's job ([reviewer/agent.py](src/agents/challenge/reviewer/agent.py)).
- **`reconcile()`** — a deterministic service (not an agent) that builds the cross-lens
  `AgreementMap`. Detailed in [lenses.md](lenses.md#cross-lens-reconciliation).

---

## Synthesis — scoring, gap-gating, and the dossier

| Node | Agent | Role |
|---|---|---|
| `experiment` | `ExperimentAgent` | Proposes/scoring validating experiments from the lens summaries + kept evidence → `experiment_results` (carries the numeric score; a Mendelian-grade genetics floor may clamp it up, via `services/decision/suitability.py`). Runs *before* challenge. |
| `gap_detection` | `GapDetectionAgent` | Reads the reviewer gaps + `AgreementMap` and decides `proceed` or `replan`. A `replan` loops back to `hitl_gate` **at most once** (`replan_count <= 1`); `proceed` falls through to `investigator`. |
| `investigator` | `InvestigatorAgent` | Runs once on the `proceed` branch. A bounded **ReAct loop** (`create_react_agent`, `recursion_limit=11`) that calls retrieval tools **on demand over MCP via the gateway** to resolve the *specific* gaps/conflicts named in `review_gaps`/`agreement_map`, then emits an evidence-grounded `investigation_summary` that sharpens the final report. **Conclusion-enrichment only** — nothing flows back into screening or the lenses. Degrades gracefully: if the gateway is unreachable it logs a warning and proceeds with an empty summary, so it can never break a run. It is the **one pipeline node that speaks MCP** (everything else imports `tools.py` directly — see [architecture.md §5](architecture.md)). |
| `report` | `ReportAgent` | Renders the dossier (`report.md` + `full_report.md`) under `results/report/{gene}/{disease}/{direction}/`, plus per-lens files under `lenses/`. Adds an **Investigation** section when `investigation_summary` is non-empty. |

The report's headline numbers — the **consensus verdict** (from the reconciler) and the
single **suitability score** (from the experiment/scoring path) — and its Literature-vs-
Empirical evidence split are described in [lenses.md](lenses.md) and
[tutorial.md](tutorial.md).

---

## See also

- [lenses.md](lenses.md) — the interpretation layer in depth.
- [architecture.md](architecture.md) — how these agents are sequenced.
