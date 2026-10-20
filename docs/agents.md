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
| `screening_first` | `ScreeningAgent` (pass 1) | LLM verdict **keep / drop / uncertain** per evidence row, judged on the retrieved text (for literature, the abstract is already fetched at acquisition, so pass 1 sees the full abstract); writes verdicts to the LLM cache. Three groups skip the LLM and are kept deterministically: pre-curated structured database records, **phased (Phase 1–4) clinical trials whose record names the target gene** (`is_target_drug_trial` in [evidence_text.py](../src/core/evidence_text.py)), and **FDA labels whose mechanism of action names the target or whose indication names the disease** (`is_on_target_fda_label`). Other FDA labels go to the LLM on their rendered `text`; each FAERS bundle inherits its label's verdict. For other trials, `screenable_text` puts interventions and conditions first and, when the gene's first mention sits past the eligibility cut-off, appends a window around it. |
| `knowledge_extraction` | `KnowledgeExtractionAgent` | For each kept item: chunks and **embeds** it (for later semantic search); for items with a freely available PubMed Central full text, upgrades `scope` to `full_text` and records the URL; and re-flags an item `uncertain` if its abstract carries a retraction/erratum/wrong-population marker. |
| `screening_second` | `ScreeningAgent` (pass 2) | For every row still `uncertain` with a PMID, fetches the PMC Open Access full text (`_enrich_uncertain_with_full_text` in `workflow.py`), keeps its sections and upgrades `scope` to `full_text`; then re-screens **only** rows that are `uncertain` **and** `scope == "full_text"`, on a focused excerpt (below). Its verdicts are cached under their own key, and only a `keep` or `drop` is ever reused. |
| `claim_extraction` | `extract_claims` (service) | Produces atomic `CoreClaim`s from `keep` evidence — the typed substrate the lenses reason over. |
| `source_quality` | `SourceQualityAgent` | Scores each kept source's journal quality (SJR / OpenAlex), once, **after** claim extraction. Result is the `source_quality` map every lens reads. |

**Why screen twice.** The two passes target different *items*, not new evidence text. Pass 1
judges every item once. The split exists to give a **focused second decision** to the items
left `uncertain` — both those the pass-1 LLM was unsure about and those `knowledge_extraction`
re-flagged for a retraction/erratum/wrong-population marker — while leaving everything already
decided keep/drop alone.

**What pass 2 reads.** Before re-screening, `screening_second` downloads the PMC Open Access
body for each `uncertain` PMID item. Items with no PMID or no OA full text stay `uncertain`,
are not re-screened and never reach claim extraction. The body keeps its sections (JATS
`sec-type`, else the usual section titles), and the prompt gets an excerpt of at most
`SCREEN_FULLTEXT_MAX_CHARS` (default 8 000 characters) beside the abstract, in this order:

1. a one-line count of where the target is named, per section (e.g. "results 12,
   discussion 5, methods 3, introduction 2");
2. the conclusions section, or else the last two discussion paragraphs;
3. every paragraph naming the target (its symbol, aliases, and the drugs acting on it found in
   the run), cut to the mention plus one sentence either side. Results come first, then figure
   and table captions, discussion, methods; within each, paragraphs that also name the disease
   come first.

The introduction is left out. It reviews earlier work, often on other genes, so a mention
there says little about what the paper studied; a target named only in the introduction shows
up in the count line, itself a sign the paper is not about it. A paper without sections gets
its target-naming paragraphs in order plus its last two paragraphs. All names come from the
run (gene symbols and aliases, drug list, disease scope); none are built in. So pass 2 is a
real second look at the items most worth one, not a re-run of pass 1 on the same abstract.
(`ScreeningAgent` is one agent invoked for both passes; `knowledge_extraction`'s own `scope`
upgrade, for `keep` items with a PMC link, records the URL only and is unrelated to this fetch.)

**Replies and caching.** Each item's verdict comes back as `{"id", "verdict", "rationale"}`, the
rationale at most 12 words, with a reply budget of 80 tokens per item. When a reply is cut off
anyway, the complete entries are kept and only the missing items are asked again. Pass 1 caches
its verdicts per source; a non-answer is never reused. Pass 2 caches under a separate key
(it judged different text) and reuses only a decisive verdict, so a pass-1 `uncertain` can
never stand in for the full-text judgement. When more than a fifth of the items of any evidence kind end
without a verdict, the reviewer adds a `screening.no_verdict` note to the report's gap analysis;
the run goes on.

Full text is located through NCBI elink with `linkname=pubmed_pmc`: the PMC id comes from
`linksetdbs[].links` ([pubmed/tools.py](../src/mcp_servers/pubmed/tools.py), `fetch_pmc_record`).
Two defects kept pass 2 from working in earlier releases:
- up to 0.1.4 the lookup parsed the wrong response field and always came back empty, so pass 2
  never saw full text;
- up to and including 0.1.4, pass 2 looked up its verdict under pass 1's cache key and
  accepted any hit, including pass 1's own `uncertain`. Whenever the LLM cache was on, it
  re-screened nothing (it did run under `--force-refresh`).

Runs affected by either defect dropped every pass-1 `uncertain` item.

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
