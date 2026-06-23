# CHANGELOG


## v0.1.0 (2026-06-23)

### Bug Fixes

- Resolve all mypy strict-mode errors across src/
  ([`f41295b`](https://github.com/athril/agentic-target-evidence/commit/f41295b01c3b9b228513f89ab91fbc6ead8390db))

Adds missing generic type arguments, explicit return/parameter annotations, and narrows union types
  (RunnableConfig, BaseMessage subtypes, StateSnapshot.values) so `uv run mypy src/` passes cleanly
  under strict mode. Also fixes a genuine BedrockProvider call missing required model/region kwargs
  in core/a2a/run_service.py.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Stop source_quality test from depending on gitignored scimago data
  ([`412b583`](https://github.com/athril/agentic-target-evidence/commit/412b5830e592cc8ebc6e49dd5ebe8aff45d56f91))

The bundled SJR index is non-commercial-licensed and gitignored, so it never exists in CI. The test
  enabled SCIMAGO_SJR_ENABLED and relied on the real file to resolve "The Lancet" deterministically;
  when the file is missing, resolution falls through to the LLM path with an unconfigured AsyncMock,
  crashing strip_json_fence on a coroutine. Mock resolve_sjr directly instead, matching the sibling
  test's pattern.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **a2a**: Correct mTLS CA env var name and disable TLS hostname check
  ([`79c3616`](https://github.com/athril/agentic-target-evidence/commit/79c3616d91c590a6869ee1f23b8219a23fb50a0a))

_build_ssl_context() read AGENT_CA_PATH but every other config (and .env.example) defines
  CA_CERT_PATH, so the CA was silently never loaded. All services share one cert (CN=gtv-service)
  regardless of their docker-network hostname, so identity is verified at the app layer
  (MTLSVerificationMiddleware's CN check) rather than via TLS hostname binding — disable
  check_hostname accordingly. Also switch the container healthchecks to hit https with the same
  client cert.

- **chembl**: Degrade gracefully on partial get_chemistry failure
  ([`679ee2b`](https://github.com/athril/agentic-target-evidence/commit/679ee2bcb7987dede46f40755271c3a2104105de))

Gather the mechanism/activity/potency/clinical calls with return_exceptions=True so a timeout or 5xx
  on one (e.g. the slow potency query) only drops that signal instead of discarding the whole
  chemistry bundle. Non-5xx client errors on the primary mechanism/activity calls still raise; the
  bundle is only reported unavailable when every call fails.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **core**: Retry HTTP GET/POST on timeout, not just disconnect
  ([`d4f05db`](https://github.com/athril/agentic-target-evidence/commit/d4f05db29d7ac8cc923ee08b70019a692ff70cd9))

httpx.TimeoutException was previously left unretried, so a single slow upstream response (common for
  the new bulk-data sources) failed the whole call immediately instead of getting the same backoff
  as RemoteProtocolError.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **lenses**: Recover lens verdicts from premature root-object closure
  ([`c9537c3`](https://github.com/athril/agentic-target-evidence/commit/c9537c3ea0ce582c94b9e5985c19e1bd540f22b6))

Local models occasionally close the verdict's root JSON object early and emit the remaining keys
  (e.g. axes) as trailing siblings. Strict json.loads raised "Extra data" on the leftover and
  discarded an otherwise-valid verdict entirely; loads_recovering splices out the stray closing
  brace and retries.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **lenses**: Tolerate raw newlines in LLM verdict JSON
  ([`a4a70a0`](https://github.com/athril/agentic-target-evidence/commit/a4a70a0078050f45c5324b94dbe75e3e82cd7f3c))

Local models emit multi-paragraph prose fields (e.g. the biology lens's mandated 2-4 paragraph
  narrative) with literal newlines between paragraphs rather than \n escapes. Strict json.loads
  rejects raw control characters inside strings, silently discarding an otherwise valid verdict as
  "could not be parsed" — switch to strict=False decoding.

- **mcp**: Add missing FastMCP server entrypoint for ClinGen
  ([`98bbb86`](https://github.com/athril/agentic-target-evidence/commit/98bbb864a7e60b65599a23809c344bdde0782b28))

Every other evidence source ships a server.py alongside tools.py; ClinGen was tools-only since its
  initial commit, leaving it unusable as a standalone MCP server even though the genetics agent
  already consumes get_clingen_validity in-process.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Bound GWAS Catalog SNP concurrency; add PubMed journal/ISSN fields
  ([`8ee6a78`](https://github.com/athril/agentic-target-evidence/commit/8ee6a7822abe33ca376ca5f8dce42140aa8292d3))

GWAS Catalog: unbounded per-SNP association fetches could open hundreds of simultaneous connections
  to the EBI API and trigger read timeouts; bound to 10 concurrent requests via a semaphore.

PubMed: capture full_journal/issn/essn from ESummary so the new source-quality scoring agent can
  match journals against SJR/OpenAlex by ISSN instead of by fuzzy abbreviated-name matching.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **source_quality**: Guard SJR score formatting when matched row has no numeric score
  ([`f8fa9ef`](https://github.com/athril/agentic-target-evidence/commit/f8fa9efafa4b8d5be7efe38f7744b8aa5600c323))

resolve_sjr can return matched=True with sjr=None when a bundled Scimago row has a quartile but a
  blank score, which crashed quality_note formatting with f"{sjr.sjr:.2f}". Also wires
  disease_tissue_expression_note (and the rest of the tissue-context fields) into
  biology_lens/safety_lens contracts so validate_inbound doesn't reject them.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

### Chores

- Fix pre-existing ruff lint errors
  ([`c38a611`](https://github.com/athril/agentic-target-evidence/commit/c38a611b9be799c71eb75b30ea1dcf01304c0b73))

Removes an unused build_trial_facts import in workflow.py, switches pack_batches to PEP 695 generic
  syntax (UP047), fixes an unsorted import block in test_source_quality_wiring.py, and drops unused
  os/tempfile imports in test_scimago.py. All pre-dated the recent feature commits.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Harden gitignore against results data and scratch output
  ([`5128339`](https://github.com/athril/agentic-target-evidence/commit/51283394ca10f342f2006b724865518270a0c7bb))

Broaden results-related ignores to results/, results-*/, and results_*/ so any regenerated or
  timestamped output directory is excluded by default, superseding the narrower per-subdirectory
  globs.

- Project scaffolding and dev tooling
  ([`e45c434`](https://github.com/athril/agentic-target-evidence/commit/e45c43412d6958f417bf90f3276dcb33ef8049ba))

uv-managed pyproject.toml + lockfile, pre-commit hooks (with the check-added-large-files schema/size
  fix), commitlint config, and an .env.example covering every provider/service the stack can route
  to.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Remove dead commented-out code and stale backward-compat comments
  ([`2de5c2a`](https://github.com/athril/agentic-target-evidence/commit/2de5c2ad4168c9cf129f999740046222352b2b96))

langfuse.py: drop a duplicate commented-out copy of the module left over from an earlier edit.
  evidence.py: trim "additive/1.0 rows still validate" comments now that there's no pre-1.0 schema
  in the wild to stay compatible with. No behavior change in either file.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Remove unimplemented results/experiment artifact path
  ([`3b8dbb5`](https://github.com/athril/agentic-target-evidence/commit/3b8dbb5b98a04548561cf8b5029081e1dd8aab01))

ExperimentAgent persists rankings to the Postgres experiments table only and never wrote a file
  artifact, so the mounted/created directory was dead.

- Rename CLI entry points atv-mcp/atv-chat to target-evidence-mcp/target-evidence-chat
  ([`c1370ce`](https://github.com/athril/agentic-target-evidence/commit/c1370ce0f96f813de484928c716b67e230a69612))

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Strip PM/milestone ticket labels from comments and docstrings
  ([`7064881`](https://github.com/athril/agentic-target-evidence/commit/7064881b45d65fa25bf8eb5d339f01a8538b55b5))

Remove leftover scaffolding artifacts (MP-NN task ids, WS# workstream tags, "out of scope for
  current milestone", "bench/eval workstream", "Phase 6 decision") from test docstrings and source
  comments. These are project-management references that don't belong in the codebase; the wording
  is reworded to be self-describing instead. No behavior change.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Update stale "five lenses" references to six
  ([`0c03f00`](https://github.com/athril/agentic-target-evidence/commit/0c03f002b66feecd84e6597cc64a91b22fbac945))

Docstring/comment-only cleanup after the regulatory lens was added — no functional change.

### Code Style

- Apply ruff format to source and tests
  ([`c197483`](https://github.com/athril/agentic-target-evidence/commit/c1974834349557caac69ecb3db6270f15cc24c72))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Continuous Integration

- Remove automatic Claude PR review workflow
  ([`90bcbe8`](https://github.com/athril/agentic-target-evidence/commit/90bcbe87c7821591d2a07287d17e7761211e8d98))

Drop claude-code-review.yml so Claude no longer auto-reviews every incoming pull request. Reviews
  are now opt-in via the @claude mention handler in claude.yml, avoiding unattended token spend.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Documentation

- Add NOTICE.md with license, data-source, and AI-disclaimer notices
  ([`b9d826e`](https://github.com/athril/agentic-target-evidence/commit/b9d826e865441cee184f13b5cb3e4f3f07adfb13))

Apache 2.0 NOTICE covering third-party dependency licenses, public data source terms (including the
  SCImago/OMIM non-commercial carve-outs), and a disclaimer on AI-generated output, data routing for
  SENSITIVE evidence, and cloud LLM provider terms.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Add top-level README
  ([`9b4685d`](https://github.com/athril/agentic-target-evidence/commit/9b4685d136d2ea35c61dd92bff62c84df6dbd463))

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **lenses**: Clarify source-quality scoring semantics for structured evidence
  ([`fda578c`](https://github.com/athril/agentic-target-evidence/commit/fda578c187e48a8abccaac910cd0e9ac61404580))

The score field already maps Q1-Q4/preprint to numeric weights, but lenses were still told to
  down-weight by quartile/preprint flags directly. Spell out the score scale and call out that
  score:1.0 with quartile:null means structured/database evidence (no journal to rank), not an
  unscored source, so lenses stop treating it as lower-confidence by mistake.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **notice**: Clarify lens-file revision behavior and capability assessment
  ([`0d774a0`](https://github.com/athril/agentic-target-evidence/commit/0d774a0be3db0615c2df85a095a3eca7ec9cb81e))

Documents that lens markdown files under lenses/ may be rewritten in place during a run as
  review/critic agents revise the verdict — only the post-completion version is authoritative. Also
  notes that current capability, even with smaller/local models, is approaching a junior
  translational scientist's first pass at this evidence-gathering and triage work, without changing
  the standing expert-review disclaimer.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **notice**: Document DGIdb and TTD data sources
  ([`154a7a7`](https://github.com/athril/agentic-target-evidence/commit/154a7a722dbe4ddf79de183520480359f7d5d729))

Add DGIdb (curated drug-gene interactions, per-claim source licensing) and TTD (target
  development-stage classification) to the data-sources list, and note TTD's unconfirmed
  commercial-use terms in the commercial-use section alongside the other non-commercial-gated
  sources.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Features

- **agents**: Challenge (critic/reviewer) and synthesis (experiment, report)
  ([`0a77857`](https://github.com/athril/agentic-target-evidence/commit/0a77857fbf2c767a8634e9058d98da1c3f747d49))

critic: independent-retrieval fact-check pass over lens verdicts. reviewer:

final consistency/completeness pass. experiment: proposes confirmatory experiments, gated on the
  Mendelian suitability floor. report: renders the dossier; citations.py is the shared formatting
  helpers used by both this and the per-lens report (lens_report.py, landing once interpretation
  lenses do).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **agents**: Retrieval agents and retrieval services
  ([`388b239`](https://github.com/athril/agentic-target-evidence/commit/388b2390b565081882ce69ecf79329a82f76f24e))

Nine retrieval agents (literature, patent, clinical_trial, genetics, omics, functional,
  druggability, opentargets, openfda) plus their backing services/retrieval/* operators, wired to
  the MCP servers committed earlier.

Includes agents/_common.py and services/_common.py (shared provenance/result helpers used by every
  agent and service module from here on) and tests/agents/conftest.py — pulled forward from later in
  the plan since pytest needs both present to even collect this batch's own tests, and neither has
  any forward dependency.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **agents**: Screening, knowledge extraction, and source-quality agents
  ([`91eac74`](https://github.com/athril/agentic-target-evidence/commit/91eac74a0f2ef0632e49655c442df78ae00f70b9))

screening: applies keep/drop/uncertain verdicts to gathered evidence.

knowledge_extraction: full-text upgrade + embeddings for kept evidence.

source_quality: SJR/OpenAlex journal-quality scoring, falling back to the LLM's predatory-journal
  judgment when neither source resolves a journal.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **biology-lens**: Condense uninformative DepMap blocks and add relevance guard
  ([`c9dd678`](https://github.com/athril/agentic-target-evidence/commit/c9dd67803ca9a48b0458f7e4f93d811ad0c81910))

For a non-oncology target with no meaningful cancer-cell-line dependency, the biology lens now
  collapses the per-lineage DepMap table to a one-line caveat instead of a full block the model has
  to talk its way out of, and a new post-LLM guard annotates verdict text that still cites DepMap
  essentiality as mechanism support despite that. Also hardens the constraint guards: the mis_z
  inversion check now catches magnitude-based tolerance claims (not just the "high"/"elevated"
  label), and haploinsufficiency negation handling no longer false-positives on the correct "NOT
  haploinsufficient" band phrasing.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **checkpoint**: Allowlist project pydantic models for msgpack checkpoint serialization
  ([`03d6746`](https://github.com/athril/agentic-target-evidence/commit/03d674629c5997b925c74b65ef2b12a0f6c7c904))

LangGraph's default JsonPlusSerializer allows-with-warning any module/class it doesn't recognize
  when msgpack-serializing checkpoint state, and will start blocking them once
  LANGGRAPH_STRICT_MSGPACK becomes the default. Explicitly allowlist the schemas.* types that flow
  through PipelineState so checkpointing keeps working without relying on that warning.

- **commercial**: Add market-size axis from Orphanet prevalence
  ([`78e8a6b`](https://github.com/athril/agentic-target-evidence/commit/78e8a6b302e3fc580c4bad9a6f071b655e4b70ef))

Give the commercial lens a third axis that sizes the addressable patient population from Orphanet
  disease-prevalence bands. The workflow summarizes the prevalence evidence rows into
  orphanet_prevalence_text fed to the lens, with guidance to read the prevalence band (not the
  disorder name), to treat a missing record as uninformative rather than rare, and to prefer the
  validated/worldwide record when several disagree.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **commercial-lens**: Add competitive-landscape framing and overstatement guard
  ([`6fd7109`](https://github.com/athril/agentic-target-evidence/commit/6fd710971d2a07f45d5b4e91b7b54d0b2e20dc51))

Pre-computes the approved/clinical/preclinical competitive ladder and target-vs-indication
  whitespace framing so the LLM has accurate language to draw from, then adds a post-LLM guard
  (mirroring the constraint/clinical guards) that annotates residual commercial overstatements:
  blanket "no drugs target X" claims, indication-level "underserved" claims drawn from target-level
  evidence, and "market size unknown" asserted from Orphanet silence alone.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **core**: Direction-scoped raw archive layout, sorted/relativized summary.csv
  ([`7e62c90`](https://github.com/athril/agentic-target-evidence/commit/7e62c9000d5f2b9320f1d8ca0a18c2768da3ed21))

archive_raw() now writes under results/data/<gene>/<disease>/<direction>/... instead of
  results/original/<gene>/..., merging the raw-source archive into the per-direction
  derived-artifact tree. export_summary_csv() gains the same direction segment, hoists run-constant
  fields into a leading comment line, sorts rows by (evidence_type, source), and relativizes
  artifact_uri to the CSV's own directory.

Note: architecture.md and docs/{howto_run,setup,adding_evidence_sources}.md still describe the old
  results/original/ split — doc pass is deferred per the current commit-batch plan and needs to
  reconcile this.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **core**: Postgres persistence, baseline migration, checkpointer
  ([`f7d757c`](https://github.com/athril/agentic-target-evidence/commit/f7d757cf72c22fb3e09d132179c0a32dfc24aa3e))

Single-revision Alembic baseline (Postgres is the system of record; results/ artifacts are derived
  and regenerable from it), the LangGraph Postgres checkpointer, and the core utils (exceptions,
  http, json, evidence-text helpers) the rest of the stack depends on.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **core**: Routing, telemetry, and A2A transport
  ([`8fb6b83`](https://github.com/athril/agentic-target-evidence/commit/8fb6b83ab0c2ea5690dda730f190f427aced8912))

Config-driven model routing (hybrid/all_local/custom — agents call the router, never a provider
  directly), Langfuse/OTel telemetry setup, and the mTLS A2A client/server pair. Includes the fix
  for A2AClient's SSL context handling: httpx's verify= takes a stdlib ssl.SSLContext, not an
  httpx-native type, and explicit ssl_context=None must be honored as "no mTLS" rather than falling
  back to env-based cert auto-discovery.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **decision**: Reconciler and Mendelian suitability scoring
  ([`02a2e0f`](https://github.com/athril/agentic-target-evidence/commit/02a2e0fa9915f8a3e4f686ebbadfd9f970def17f))

reconciler.py builds the cross-lens AgreementMap from LensVerdicts. suitability.py applies a
  Mendelian-disease causality score floor, driven by config/scoring.yaml so the thresholds aren't
  hard-coded (rule #4).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **druggability**: Add DGIdb interactions and TTD development stage
  ([`3b1a225`](https://github.com/athril/agentic-target-evidence/commit/3b1a225ed8692f8a6af987ec240fec825ba4d849))

Wire the DGIdb and TTD MCP connectors into the druggability retrieval service alongside the existing
  UniProt + ChEMBL chain. DGIdb adds curated drug-gene interaction claims and druggable-genome
  gene-category annotations (additive enrichment, degrading gracefully on outage); TTD adds target
  development-stage classification, gated behind TTD_ENABLED and skipped entirely when off.

The biology lens gains a developability axis that reads UniProt subcellular localization, ChEMBL
  clinical-candidate progression, and TTD status as a modality-specific, secondary signal, plus
  guidance that knowledge graphs corroborate association not mechanism and that raw variant counts
  are supportive rather than primary evidence.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **evidence**: Add disease-class taxonomy and evidence-hierarchy weighting
  ([`7265512`](https://github.com/athril/agentic-target-evidence/commit/7265512686b6b2a4491ed674165b8acffb40772a))

Replaces the oncology-only binary (_ONCOLOGY_AREA_IDS) with a config-driven disease-class resolver
  (oncology/metabolic/fibrosis/rare_mendelian/...) and a disease-class-conditional evidence-strength
  hierarchy for structured claims, so lenses can reason uniformly across disease classes instead of
  via scattered hardcoded rules.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **evidence**: Claim pipeline + KG builder services
  ([`b74eb0d`](https://github.com/athril/agentic-target-evidence/commit/b74eb0df6402ec2bc90633b5f960a12ef8e6454b))

src/services/evidence/**: claim extraction (full-text -> atomic CoreClaim rows), claim clustering,
  constraint interpretation (gnomAD LoF/missense -> plain-language grade), mouse phenotype rendering
  (IMPC), disease/tissue matching, entity resolution, source quality/sufficiency scoring.

src/services/knowledge_graph/**: offline KG builder/export/ingest/query — this is the
  schemas.knowledge_graph-backed graph service, not the SPOKE MCP server (SPOKE is an external KG
  queried as a retrieval source; this one is built from this run's own evidence).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **gbd**: Add GBD/IHME disease-burden source and auto-keep its evidence in screening
  ([`d32fdbf`](https://github.com/athril/agentic-target-evidence/commit/d32fdbf666f4a4a287076856ec0d7d18c5f5044b))

Adds GBD (Global Burden of Disease) as a disease-keyed, non-commercial-gated epidemiology source
  mirroring the OpenFDA/Orphanet/SCImago patterns: a CSV-based connector under mcp_servers/gbd, a
  fetch_gbd retrieval service, a gbd acquisition node feeding the commercial lens's market-size
  axis, and gateway tool-list gating consistent with omim/scimago/ttd.

Also fixes a screening gap that silently dropped GBD evidence: EvidenceType.EPIDEMIOLOGY was missing
  from screening's _AUTO_KEEP_TYPES, so GBD rows (which carry no gene mention) were going through
  the LLM relevance screener instead of being auto-kept like Orphanet's structurally-identical
  GENETICS-typed prevalence evidence — causing them to be dropped before ever reaching the
  commercial lens.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **genetics**: Surface ancestry-generalizability signals
  ([`e691ffe`](https://github.com/athril/agentic-target-evidence/commit/e691ffec70dd93acbb35fb7d0db8b2e67079b660))

Carry GWAS cohort composition (initial_sample_size) into the genetics lens's source-evidence text
  and flag gnomAD HC pLoF variants whose allele frequency varies >3x across well-sampled populations
  as ancestry-skewed. The genetics lens skill gains an ancestry-generalizability caveat for
  single-ancestry GWAS support and for ancestry-skewed natural-knockout signal — framed as a
  confidence caveat, never a verdict reversal, and never applied to Mendelian/ClinVar causality.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **harness**: Base agent, contracts, loop guard, skills loader
  ([`9dffea4`](https://github.com/athril/agentic-target-evidence/commit/9dffea4870e71c9cb530036c222d7be647271f2d))

BaseAgent enforces each agent's consumes/produces contract (minimal information per agent) and wires
  the loop-guard counters that cap loop-capable edges — agents inherit this rather than
  reimplementing the guarantees themselves.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **infra**: Auto-pull Ollama models on first startup
  ([`57d7d1b`](https://github.com/athril/agentic-target-evidence/commit/57d7d1be07fa1feb8c8ed652badc7996c5f94465))

Add a one-shot `ollama-pull` compose service that pulls the chat/agent model and the embedding model
  into the ollama-models volume once Ollama is healthy, so a fresh clone is self-provisioning (no
  manual `ollama pull`). Mirrors the existing minio-setup one-shot pattern and is a no-op once the
  volume already holds the weights.

- ollama gains a `healthcheck` (ollama list) that ollama-pull gates on. - chat and planner now wait
  on ollama-pull completing, so the model is present before they serve (avoids first-request 404s).
  - Makefile APP_SERVICES includes ollama-pull. - .env.example documents OLLAMA_EMBED_MODEL and the
  auto-pull behavior.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **interpretation**: Genetics/biology/safety/clinical/commercial/regulatory lenses
  ([`5418d74`](https://github.com/athril/agentic-target-evidence/commit/5418d740811d5774f40d251b1345264bd7dd1954))

Six interpretation lenses share _lens_base.py (lens-evidence-type routing, LLM verdict parsing,
  source-quality-aware confidence). genetics_lens consumes SPOKE/MONDO context from the genetics
  agent; biology_lens and commercial_lens consume the constraint/patent-landscape interpreters from
  the evidence services batch.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **interpretation**: Make lens context window and max-claims configurable
  ([`74bc77a`](https://github.com/athril/agentic-target-evidence/commit/74bc77a9f770c208c437a1ad9ae9fadccf03a44d))

The six interpretation lenses can blow past the local model's context window on evidence-heavy
  targets. Two knobs to tune this without code changes:

- LENS_MAX_CLAIMS (env) caps how many claims a single lens call receives; claims are already ranked
  best-first by evidence weight, so the cap drops the weakest. Replaces the hard-coded
  _MAX_CLAIMS=100 with _max_claims(). - LENS_NUM_CTX (env) overrides num_ctx for all six lens tasks
  uniformly, winning over their per-task entries in config/routing.yaml task_num_ctx.

routing.yaml gains explicit per-task num_ctx for each lens (32768) plus the new investigator task
  (16384). Covered by routing-policy and lens-base tests.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **lenses**: Post-llm output guards for hallucinated claims
  ([`9b0bc1e`](https://github.com/athril/agentic-target-evidence/commit/9b0bc1efe1776e78a4362dfe43d8b4fbd0e1baef))

Add a shared post-LLM safety net that annotates (never silently rewrites) lens
  narrative/rationale/axis text contradicting authoritative structured evidence, recording a
  ValidationFlag on every activation:

- Constraint guard (genetics + safety): flags unsupported haploinsufficiency and inverted
  missense-constraint / mis_z-direction claims against the precomputed gnomAD bands. Widens the
  regex coverage (qualifier-first "strong missense constraint", "high mis_z" inversion) with
  negation lookback, and passes the full ConstraintReading to the lens. - Clinical-phase guard
  (clinical): new deterministic trial-fact extraction flags phase-count conflation and per-trial
  phase/recruitment-status mismatches. - Tissue-relevance guard (biology + safety): flags treating a
  high-bulk-TPM, non-disease tissue as disease-relevant.

Safety-lens skill gains rules distinguishing on-target extra-tissue from off-target liabilities,
  naming mouse-KO organ phenotypes as candidate liabilities, and not re-banding constraint or
  reading bulk-TPM rank as relevance.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **lenses**: Resolve and wire disease-class through all 6 lenses
  ([`b5d82ff`](https://github.com/athril/agentic-target-evidence/commit/b5d82ff0e67313766619bfbce8b789c8bb674229))

Replaces the oncology-only binary previously computed ad hoc in the genetics and biology lens nodes
  (_ONCOLOGY_AREA_IDS) with a single _resolve_disease_classes call per lens node, feeding the
  disease_class taxonomy/rules added in the previous commit. Each lens now receives disease_classes
  in its task_spec and injects the matching guidance note via build_disease_class_note. The shared
  claim ranking/truncation in _lens_base.py also switches from a flat non-literature weight to the
  disease-class-conditional evidence_weight hierarchy, and injects an evidence-strength ledger into
  the prompt so the LLM sees which evidence categories are strongest before reasoning over claims.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Add DGIdb MCP server for curated drug-gene interactions
  ([`e3d9b96`](https://github.com/athril/agentic-target-evidence/commit/e3d9b962892bd447d1fde1c96a4c3e07ef50d97b))

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Add MCP gateway and Gradio chat assistant
  ([`6bfd991`](https://github.com/athril/agentic-target-evidence/commit/6bfd9913c386e7c0bfb68e6d008ff2893802402e))

Compose every public mcp_servers/* connector into one FastMCP gateway (atv-mcp) that external MCP
  hosts can call ad hoc, and a Gradio chat assistant (atv-chat) that drives those tools via a local
  Ollama model.

The gateway discovers sources by walking mcp_servers/ and fails closed on any SENSITIVE-classified
  source that grows an importable server.py; the now server-less internal_data source is removed so
  it can never be exposed. Optional bearer auth (MCP_GATEWAY_TOKEN) guards the HTTP transport, and
  the chat assistant persists per-user conversation state via the LangGraph Postgres checkpointer
  with optional CHAT_AUTH login.

Both ship as Docker services (mcp-gateway internal-only, chat on :7860), with Make targets, a
  Windows make.bat wrapper, and a chat dependency group.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **mcp**: Add TTD MCP server for therapeutic target development status
  ([`762b389`](https://github.com/athril/agentic-target-evidence/commit/762b389373e395df08a022b1859d98a4d4b71f69))

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Add UniProt MCP server for protein subcellular localization and class
  ([`841cf28`](https://github.com/athril/agentic-target-evidence/commit/841cf2892aaca471d6b6c7b5b0e35d607e756bc4))

Split out of the former druggability server alongside chembl.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Clingen MCP server
  ([`9d92f5a`](https://github.com/athril/agentic-target-evidence/commit/9d92f5ab6d3ecda7660386b9eda3e801c507e5bf))

Gene-disease validity classification lookups (tools-only — no standalone FastMCP server entrypoint
  for this source yet).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Clinicaltrials.gov MCP server
  ([`1346972`](https://github.com/athril/agentic-target-evidence/commit/134697237f39fa73d2053c39ac9ae15af694c204))

FastMCP tools for searching ClinicalTrials.gov by gene/disease and extracting phase, status, and
  outcome data.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Depmap MCP server
  ([`3b2b8c7`](https://github.com/athril/agentic-target-evidence/commit/3b2b8c7186d5473be68185373af1a244830a742c))

FastMCP tools for CRISPR/RNAi gene-dependency data — bulk-file fetch with lineage breakdown, falling
  back gracefully to summary-only data when the bulk download is unavailable.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Druggability MCP server
  ([`b75f8df`](https://github.com/athril/agentic-target-evidence/commit/b75f8df5c4024ff929a86f08ade4d977021cffc8))

FastMCP tools for protein-class and chemistry data (UniProt/ChEMBL) — the substrate for the
  druggability assessment lens.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Encode MCP server
  ([`2f65b08`](https://github.com/athril/agentic-target-evidence/commit/2f65b0855553a211d35e681cd210bcf44bfb39b4))

Cis-regulatory element assay coverage at a gene's locus, backing the new regulatory_element evidence
  type.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Expression Atlas MCP server
  ([`e3baac0`](https://github.com/athril/agentic-target-evidence/commit/e3baac069101026aff05fb655b7b266aad409529))

Tissue/cell-type expression baseline data (EMBL-EBI), complementing GTEx.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Gencc MCP server
  ([`d7e7c52`](https://github.com/athril/agentic-target-evidence/commit/d7e7c520a940d45a8209df769e9593ef0a09387a))

Gene-disease validity classifications (curated consensus across ClinGen, PanelApp, and other
  submitters).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Gnomad MCP server
  ([`658c92e`](https://github.com/athril/agentic-target-evidence/commit/658c92e51589ae40292a0e165bf3c591cdd5407e))

FastMCP tools for gnomAD LoF/missense constraint and ClinVar variant lookups (accessed via the
  gnomAD API).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Gtex MCP server
  ([`f5c5e85`](https://github.com/athril/agentic-target-evidence/commit/f5c5e85ca450805041374dac452942774e8d8d97))

FastMCP tools for GTEx tissue expression queries.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Gwas Catalog MCP server
  ([`62f998d`](https://github.com/athril/agentic-target-evidence/commit/62f998d16c62f8c40481c21b950d3f3f25031c2b))

FastMCP tools for GWAS Catalog variant/trait association lookups by gene and rsID.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Impc MCP server
  ([`790162b`](https://github.com/athril/agentic-target-evidence/commit/790162b4b934640e17fdfb25f2e0bf1287c10fa3))

Mouse knockout phenotype data from the International Mouse Phenotyping Consortium.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Internal-data MCP server
  ([`c7b9e1d`](https://github.com/athril/agentic-target-evidence/commit/c7b9e1d6282aecdedc6cb732ad7563159f779af4))

Tags every record it emits as SENSITIVE — this is the boundary for proprietary/internal evidence
  sources, distinct from the public-data servers around it.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Omim MCP server
  ([`19f4907`](https://github.com/athril/agentic-target-evidence/commit/19f49072b4a66d25f60b0571a130df37b75a299d))

Gene-disease phenotype/inheritance lookup. Non-commercial-use API key gated behind OMIM_ENABLED
  (default off) per OMIM's terms; the genetics agent skips the source entirely when unset, no error.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Ontology MCP server (HGNC + MONDO)
  ([`dd52abe`](https://github.com/athril/agentic-target-evidence/commit/dd52abefde5750e8e9ddc1e680dc4a6327e3e6f2))

Identifier resolution for gene symbols (HGNC) and disease terms (MONDO) — the canonical
  gene_id/disease_id source the retrieval agents and lenses key off of.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Open Targets MCP server
  ([`add9238`](https://github.com/athril/agentic-target-evidence/commit/add92388ae1b5271c94d6e73dbe3dd0dd8d8a1d8))

FastMCP tools for the Open Targets Platform GraphQL API — target-disease association scores and
  supporting datatype breakdowns.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Openalex MCP server
  ([`d5d5292`](https://github.com/athril/agentic-target-evidence/commit/d5d52929188f711122df54d60bee94c15e93aa01))

CC0 journal-quality signal (citation counts, works metadata) by ISSN — the commercial-safe default
  for the new source-quality scoring agent. Enabled by default; OPENALEX_MAILTO is optional and
  joins the API's polite pool.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Openfda MCP server
  ([`a623dd1`](https://github.com/athril/agentic-target-evidence/commit/a623dd1220085ce8b0d5a034782bf529ab9f0369))

FastMCP tools for FDA drug label and FAERS adverse-event signal lookups — feeds the regulatory lens.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Orphanet MCP server
  ([`a74eb08`](https://github.com/athril/agentic-target-evidence/commit/a74eb08930b5717a603e2255bf456ec73bfde5fd))

Rare-disease gene-disease associations and prevalence data.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Project Score MCP server
  ([`9d333cd`](https://github.com/athril/agentic-target-evidence/commit/9d333cd043f1d7f5abc4b80b1ea7130a36c45568))

Sanger Project Score CRISPR dependency data, complementing DepMap.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Pubmed MCP server
  ([`fc10803`](https://github.com/athril/agentic-target-evidence/commit/fc1080355cedd4871e730400bb1d3fdba66f79ba))

First of the MCP retrieval servers — FastMCP tools for PubMed/MEDLINE search and abstract/full-text
  fetch, using resolved MeSH descriptors rather than untagged free-text terms.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Scimago MCP server
  ([`bbd53db`](https://github.com/athril/agentic-target-evidence/commit/bbd53db45f922846f1363c66641f73226b77a1c1))

SJR journal-rank lookup by ISSN. The bundled SJR dataset is non-commercial-licensed (see NOTICE.md),
  so SCIMAGO_SJR_ENABLED defaults to false and the gzipped index is gitignored — non-commercial
  users regenerate it locally via scripts/build_scimago_index.py. When disabled, source-quality
  scoring falls back to OpenAlex + the LLM's predatory-journal judgment.

Also documents the OMIM and OpenAlex env toggles added alongside this source.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Spoke MCP server
  ([`160ae6b`](https://github.com/athril/agentic-target-evidence/commit/160ae6bcb5c8c43ca9f2cf11850fb242a92bc254))

FastMCP tools for querying the SPOKE biomedical knowledge graph — the last of the 14 MCP retrieval
  servers.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Uspto MCP server
  ([`672e42c`](https://github.com/athril/agentic-target-evidence/commit/672e42c2a73dc3c888cbf5435c289c603684289f))

Patent search against the USPTO Open Data Portal with PDF abstract extraction (+ OCR fallback for
  scanned documents). Search results always carry a plain Google Patents link for manual lookup, but
  this software never fetches from or scrapes Google Patents.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **orchestration**: Target-validation pipeline, planner, runtime config, containers
  ([`d83ea9a`](https://github.com/athril/agentic-target-evidence/commit/d83ea9a718c73136766b48747e5faefa4e83de96))

Wires the full target-validation LangGraph workflow (capabilities/target_validation), the Planner
  agent (user touchpoint), the A2A service runner, and the dev/runtime scaffolding needed to
  actually execute a pipeline run: routing.yaml, otel-collector.yaml, disease_tissue.yaml,
  Docker/Compose, CI, Makefile, run_analysis.py CLI entrypoint, and the smoke/capability test
  suites.

Also includes the placeholder capabilities (indication_expansion, competitor_monitoring,
  target_prioritization) and two standalone dev scripts (scimago index builder, USPTO abstract OCR)
  that have no better home in the batch sequence.

Documentation (AGENTS.md, CLAUDE.md, NOTICE.md, architecture.md, docs/) is intentionally excluded
  from this batch and deferred to a later documentation-reconciliation pass.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **report**: Split dossier into Literature vs Empirical sections, sort literature by
  quality+recency
  ([`fda9931`](https://github.com/athril/agentic-target-evidence/commit/fda99310078f659ad504005a7efd7bcf3d7a9f89))

Both the per-lens report and the full dossier now group evidence into a top-level Literature section
  and a top-level Empirical section (patents, trials, genetics, omics, etc.), instead of one flat
  list of same-level sections. citations.is_literature() is the shared predicate so the two
  renderers never drift. Literature rows sort highest-quality-first then most-recent-first so
  citation numbers and reading order agree across both report types; quality stars now also show on
  patent/trial/generic empirical rows.

- **retrieval**: Concurrent source fetches and Orphanet prevalence
  ([`b0c315c`](https://github.com/athril/agentic-target-evidence/commit/b0c315c87b1014b8839e68aad7b753d1a197c23c))

Restructure the genetics and omics retrieval agents to fan out their independent source calls with
  asyncio.gather in dependency tiers, each call wrapped so one source outage degrades to None/[]
  instead of discarding the evidence already gathered from the others.

Add an Orphanet disease-prevalence source (product 9) — a new get_orphanet_prevalence tool plus its
  MCP server registration — fetched as a dependent tier from the OrphaCodes already resolved for
  gene-disease associations. Prevalence is an addressable-population signal, archived as its own
  evidence row distinct from genetic-validity associations.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **routing**: Token-budget-aware batch packing + per-task Ollama context override
  ([`534cefb`](https://github.com/athril/agentic-target-evidence/commit/534cefb5a1fabb0ac81d33310ab42e9f85718e8b))

Evidence text length varies a lot by source, so fixed item-count batches risk overflowing the
  model's context window. Pack screening and claim_extraction batches by estimated token count
  (core/batching.py) and let Ollama use a smaller num_ctx per task instead of the full 32k window,
  which otherwise forces KV-cache spillover to CPU on 8GB VRAM.

- **schemas**: Canonical Evidence/AgentMessage/state/KG/verdict schemas
  ([`3f2ad9e`](https://github.com/athril/agentic-target-evidence/commit/3f2ad9e06035bfbc3df24498d7377e6d23cbc3e3))

The single source of truth for the wire format (A2A), the Postgres schema, and summary.csv — every
  other layer builds on these Pydantic v2 models rather than defining a parallel shape.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **schemas**: Literature lens-topic routing, regulatory_element type, source-quality fingerprint
  ([`4b33997`](https://github.com/athril/agentic-target-evidence/commit/4b339974763f235adfc0aeaff457d3bf7273ef0b))

Adds EvidenceType.REGULATORY_ELEMENT (ENCODE cis-regulatory assay coverage) and LensTopic
  (genetics/biology/safety/clinical) — CoreClaim.topics tags a free-text literature claim with the
  lenses it's relevant to, so one claim can fan out beyond the biology-lens literature catch-all.
  Additive/optional: existing rows with no topics still validate.

Adds source_quality_fingerprint(), a stable per-run cache key for the new source-quality scoring
  agent, and a source_quality dict field on PipelineState (evidence_id -> SJR/OpenAlex quality
  assessment).

Removes the now-unused core/extension split (ClaimExtension, split_claim, EXTENSION_FOR) — confirmed
  zero remaining callers; topics supersedes it as the mechanism for routing literature claims to
  lenses.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **screening**: Enrich second-pass uncertain items with PMC full text
  ([`151f989`](https://github.com/athril/agentic-target-evidence/commit/151f9890fd2e5b36ce8c3ca25308069a01f52ac8))

Pass 1 leaves an item "uncertain" precisely when the abstract isn't enough to decide. Previously
  pass 2 just re-screened the same abstract. Now, items left uncertain with a PMID get their PMC
  Open Access full text fetched and injected (capped at SCREEN_FULLTEXT_MAX_CHARS, default 8000) so
  pass 2 can re-judge on real content; items with no PMID or no OA text fall back to the abstract as
  before. Renames pubmed fetch_full_text -> fetch_pmc_record (the metadata/link lookup) and
  introduces a separate fetch_full_text(pmc_id) that downloads and parses the JATS body into prose,
  stripping references/figures/tables.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **source-quality**: Top-tier SJR scoring, preprint/non-literature scoring, quality-ranked claim
  truncation
  ([`e687599`](https://github.com/athril/agentic-target-evidence/commit/e687599948258a43130450aae116bf88f1459e8c))

- scimago.tools: journals in the top 3% by raw SJR score 1.0 instead of the flat Q1 score
  (_TOP_TIER_SCORE/_TOP_TIER_PERCENTILE). - source_quality agent: preprints score at the Q4 floor
  regardless of venue; structured/database evidence (no journal to rank) scores at the top-tier
  ceiling instead of going unassessed. - _lens_base: rank claims best-quality-first before the
  _MAX_CLAIMS cap (raised 40->100) so truncation drops the weakest claims rather than whatever
  landed past an arbitrary index; expose the numeric score to lenses alongside
  quartile/predatory/preprint. - planner: re-run SourceQualityAgent on _rerun_acquisition_task,
  since its cache key isn't keyed on the evidence set and would otherwise silently return the
  pre-rerun quality map.

- **synthesis**: Add Investigator agent to close review gaps via MCP tools
  ([`525b672`](https://github.com/athril/agentic-target-evidence/commit/525b6728bea05ee4f485742625940f9b04d7145c))

After gap_detection, the new InvestigatorAgent runs a single bounded ReAct loop over the retrieval
  MCP tools (same gateway the chat assistant uses) to resolve the specific lens gaps/conflicts the
  review surfaced. It is conclusion-enrichment only — its findings flow into a new "Investigation"
  section of the report, never back into screening/lenses — and it never breaks the run on failure.

Wiring: - New node investigator between gap_detection and report; gap_detection's proceed branch now
  routes proceed → investigator → report (replan still loops back to hitl_gate, bounded). -
  PipelineState gains investigation_summary / investigation_tools_used, seeded by the planner and
  run_analysis and cleared on restart-from. - ReportAgent renders the additive Investigation section
  (empty when skipped).

Also reworks the restart router: it is now a passthrough whose conditional edge (_restart_route)
  either fans out to all acquisition nodes (fresh run) or jumps to a single node (restart). A
  Command(goto=...) did not suppress the node's static out-edges in this LangGraph version, so a
  resume re-ran all acquisition; a conditional edge replaces the route instead. _all_raw_evidence
  now dedups by (evidence_type, source) so a re-acquisition can't double-feed claims to the lenses.

langchain-ollama moves from the chat extra to the main dependencies (the investigator's ReAct loop
  needs it in the pipeline image).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **synthesis**: Gap detection agent and per-lens report rendering
  ([`2c53e66`](https://github.com/athril/agentic-target-evidence/commit/2c53e667978e3f7b32b27e5b4c051453c7b27f8a))

gap_detection: identifies evidence gaps and triggers a bounded replan loop.

lens_report.py: renders the per-lens markdown report, sharing citation formatting with the full
  dossier (report/citations.py) and lens-evidence-type routing with the interpretation lenses
  (_lens_base.py) — lands here since it depends on both.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

### Refactoring

- Move run_analysis.py into the src/ package
  ([`53263b6`](https://github.com/athril/agentic-target-evidence/commit/53263b65da16084fb336baeabc921f05594a7d82))

Relocate the CLI entrypoint under src/ so it sits alongside the importable packages and resolves
  imports the same way the rest of the codebase does.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Remove Tier A dead code (gating, scorers, redundant agent wrappers)
  ([`894bcca`](https://github.com/athril/agentic-target-evidence/commit/894bcca98901fb70d6f9e8631ec841c320e83884))

Removes code unreachable from any live entry surface (CLI/planner, MCP gateway, chat):

- capabilities/target_validation/gating.py — validate_transition() was never called; the real HITL
  gate is hitl_gate_node's interrupt(). README corrected. - agents/planner/contract.py — imported by
  nothing. - agents/retrieval/{druggability,openfda,functional}/ — redundant agent wrappers; the
  graph calls services/retrieval/* directly. -
  services/evidence/{sufficiency_scorer,quality_scorer}.py — tested in isolation but routed through
  by no pipeline node. - schemas/evidence.experiment_fingerprint() — defined but never called. -
  agents/planner/agent.py — drop dead create_app/_run_until_interrupt/ _resume_after_hitl
  (superseded by main.py); keep the request models and state helpers main.py imports.

Tests: delete test_gating.py and test_functional_agent.py; trim scorer and experiment_fingerprint
  cases. Retarget the planner API tests at the live agents/planner/main.py app (previously the suite
  only covered the removed create_app and main.py had zero coverage); drop the three create_app-only
  caller-supplied-ID cases that main.py does not implement.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **mcp**: Give every MCP tool an explicit source-prefixed name
  ([`711e4e4`](https://github.com/athril/agentic-target-evidence/commit/711e4e4dcacff70ceb78adb965eb4acab908b2ea))

Each @mcp.tool() across src/mcp_servers/*/server.py now carries an explicit name="<source>_<action>"
  (e.g. chembl_get_chemistry, clingen_get_validity), with redundant repeats of the source name
  stripped from the original function name. ontology/server.py's three tools are prefixed by their
  actual upstream database (hgnc_, mondo_, hpo_) instead of the umbrella folder name, since that
  module wraps three distinct sources.

mcp_gateway/server.py now mounts each sub-server without namespace=, since the explicit per-tool
  name already carries the prefix — this keeps tool names identical whether a client connects to a
  source's standalone server.py or to the composed gateway, and avoids double-prefixing (e.g.
  chembl_chembl_get_chemistry).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Remove druggability MCP server
  ([`ed16e11`](https://github.com/athril/agentic-target-evidence/commit/ed16e11e313dd6be0769a34d077d2d3133b9fd93))

Split into separate chembl and uniprot MCP servers for clearer source attribution.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
