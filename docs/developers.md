# Developer guide

> Part of the [docs/](README.md) documentation set. Read [components.md](components.md)
> and [architecture.md](architecture.md) first — this page assumes you know the layout and
> the pipeline.

How to extend the system, the conventions to follow, and — importantly — the code you
should **not** build on.

---

## Project conventions

- **Package layout** is `src/`-based (`pythonpath = ["src"]`); imports are absolute from the
  `src` root (e.g. `from schemas.evidence import Evidence`).
- **Contracts are enforced.** Every agent declares an `AgentContract` whitelisting the
  `task_spec` keys it `consumes` and the payload keys it `produces`; the harness rejects
  anything undeclared at both boundaries. A model-op *service* uses the same contract
  (`ServiceContract = AgentContract`) — being a "service" is not an exemption from routing,
  tracing, or the step budget. See [components.md](components.md#srcharness--the-shared-agent-runtime).
- **Config over constants.** Routing and thresholds are config-driven:
  [config/routing.yaml](config/routing.yaml) (provider/policy per agent) and
  [config/scoring.yaml](config/scoring.yaml) (lens weights, liability points, bands, the Mendelian score floor and the clinical-precedent gates). Don't
  hard-code a model choice or a threshold.
- **Tooling:** `ruff` (line length 100; `E,F,I,UP,B,SIM,TCH`) and `mypy --strict` with the
  Pydantic plugin. Versioning is automated by Release Please on `main`, driven by
  Conventional Commit messages (see [CONTRIBUTING.md](../CONTRIBUTING.md)). The release PR
  also bumps the `gene-profile` plugin version and its pinned connector release (the
  `extra-files` in [release-please-config.json](../release-please-config.json)); see
  [gene_profile.md § Releases](gene_profile.md#releases).

---

## Extension points

### Add a new LLM provider
Implement the `ModelProvider` Protocol (`supports`, `complete`, `embed`) in
[src/core/routing/providers/](src/core/routing/providers/)`<name>.py` (existing adapters:
`ollama`, `bedrock`, `openai`, `azure`, `anthropic`, `google`). Register it in the provider
dict where the `Router` is built (`run_analysis.py` / `planner/main.py`) and reference it in
`config/routing.yaml`. Sensitive routing is non-negotiable: anything classified `SENSITIVE`
must stay on the local provider (the policy engine enforces this).

### Add a new data source
1. Create [src/mcp_servers/](src/mcp_servers/)`<source>/tools.py` with the fetch logic
   returning typed records (use `core.http.get_with_retry`/`post_with_retry`). Add a
   `server.py` `FastMCP` wrapper too if the source should be reachable via the MCP gateway —
   the gateway discovers it automatically, no gateway edit needed
   ([mcp_gateway.md](mcp_gateway.md)). The pipeline imports `tools.py` directly and does not
   need the wrapper.
2. Consume it from a retrieval **service** ([src/services/retrieval/](src/services/retrieval/))
   or an existing acquisition agent, building `Evidence` rows stamped with the right
   **`EvidenceType`** — that type is what routes the evidence to lenses.
3. If it's a *new* acquisition node: add the node + its `restart_router → node →
   screening_first` edges in [workflow.py](src/capabilities/target_validation/workflow.py),
   add a state bucket in [state.py](src/schemas/state.py) (with the `_append` reducer), and
   add it to `_ACQUISITION_NODE_NAMES`.
4. Update [data_sources.md](data_sources.md) and [NOTICE.md](../NOTICE.md) (license!). If the
   license is non-commercial, gate it behind a `<SOURCE>_ENABLED` flag, default off.

See [adding a source in practice] by following any existing pair, e.g.
`services/retrieval/openfda.py` + `mcp_servers/openfda/tools.py`.

### Add a new interpretation lens
1. Create [src/agents/interpretation/](src/agents/interpretation/)`<name>_lens/{agent.py,contract.py}`
   delegating to `run_lens(...)` with your `LENS_NAME`, `EVIDENCE_TYPES`, and `skill_name`.
2. Add the lens's structured `EvidenceType`s to `LENS_EVIDENCE_TYPES` in
   [_lens_base.py](src/agents/interpretation/_lens_base.py) (and, if it should read
   literature, to `LensTopic`).
3. Add the lens name to the `Literal` in `LensVerdict`/`AgreementMap`
   ([verdicts.py](src/schemas/verdicts.py)).
4. Wire the node in `workflow.py`: add it to the `_lenses` tuple so it gets the `hitl_gate →
   lens → experiment` edges. The reconciler picks it up automatically (it iterates whatever
   verdicts arrive).
5. Write the prompt at `skills/<name>_lens.md`.

### Add a new capability (workflow)
[src/capabilities/](src/capabilities/)`<name>/workflow.py` exposing a `build_*_graph()`
factory. The three non-`target_validation` capabilities are currently
`NotImplementedError` stubs — they show the intended pattern. See
[architecture.md §7](architecture.md).

### Add domain knowledge (a skill)
Drop a markdown file in [skills/](skills/) and load it with `ctx.load_skill("<name>")`. Skills
are the prompts/domain rules; they're cached and resolved by name (no import paths).

Don't confuse these pipeline prompts with **Claude Code skills** under
[.claude/skills/](../.claude/skills/) (today only `gene-profile`). Those are loaded by Claude
Code, not by the harness, and a skill shipped as a plugin must be self-contained: it cannot read
`skills/*.md` or anything else outside its own folder once installed. See
[gene_profile.md](gene_profile.md#layout).

### Change a schema that is cached
`Evidence` and `LensVerdict` carry a `schema_version`. When you bump one, old cache entries
are not an error: evidence rows that no longer validate are skipped individually, and a cached
lens verdict that fails validation is treated as a miss and recomputed. The lens cache key
([`lens_fingerprint`](../src/schemas/evidence.py)) includes the `LensVerdict` schema version, so
a bump retires every cached verdict even when the old JSON would still parse. Bump the version
whenever the meaning of a field changes, not only its shape.

The key also carries `lens_input_digest` ([_lens_base.py](../src/agents/interpretation/_lens_base.py)),
so editing a lens skill prompt, a lens's task-spec builder in the workflow, or anything upstream
that changes the claims or context a lens receives retires that lens's cached verdict on its own.
No bump is needed for those. Changes to post-LLM guard code are not covered: use
`--force-refresh` to re-annotate cached verdicts.

### Steer a lens's wording
When a lens keeps writing something the data contradicts, fix it at both ends: compute the
correct framing deterministically and inject it into the prompt (e.g.
`missense_framing_note` in [constraint_interpret.py](../src/services/evidence/constraint_interpret.py)),
and register the recommended wording with the matching guard family in
[guard_rules.py](../src/services/evidence/guard_rules.py) so the invariant suite proves the guard
accepts it. Add the real sentence to [tests/fixtures/guard_corpus.yaml](../tests/fixtures/guard_corpus.yaml).
See [lenses.md § Output guards](lenses.md#output-guards).

---

## Testing

| Command | What it runs |
|---|---|
| `make test` | All tests except `smoke` (`pytest tests/ -m "not smoke"`). |
| `make test-smoke` | End-to-end pipeline smoke test (needs Ollama + internet). |
| `make test-schemas` | Schema/contract tests only. |
| `uv run pytest tests/gene_profile` | The `gene-profile` skill's checker, renderer and gather logic (offline). |

Two pytest markers are defined ([pyproject.toml](pyproject.toml)):

- `integration` — requires a running Postgres (testcontainers); deselect with
  `-m "not integration"`.
- `smoke` — the full end-to-end run; opt in with `-m smoke`.

`asyncio_mode = "auto"`, `pythonpath = ["src"]`, `testpaths = ["tests"]`. Tests mirror the
`src/` tree under `tests/`.

---

## Do **not** build on this (off-path code)

The short "don't extend" list:

- **Planner routes go in [planner/main.py](src/agents/planner/main.py)**, not in
  `agents/planner/agent.py` — that file holds only the request models and state helpers
  `main.py` imports.
- **The A2A-only retrieval *agent* classes** (`PatentAgent`, `ClinicalTrialAgent`,
  `OpenTargetsAgent`) — the live graph uses the `services/retrieval/*.py` functions. Extend
  the service, not the wrapper. (The data and its lens routing are fully live — only the
  wrapper class is off-path.)
- **`services/knowledge_graph/` + `schemas/knowledge_graph.py`** — an unwired island for the
  not-yet-built `target_prioritization` capability. Don't assume it runs.
- **`core/a2a/`** and the `agents-knowledge`/`agents-reasoning`/`report` containers — built
  but off the live path (the pipeline runs agents in-process). Scaffolding for a distributed
  deployment; whether to keep it is a product decision.

> **Not on this list (a common mistake): `mcp_servers/*/server.py`.** These `FastMCP` wrappers
> *are* live — see [faq.md](faq.md#is-mcp_serversserverpy-dead-code).

---

## See also

- [components.md](components.md) — the layers you're extending.
- [agents.md](agents.md) / [lenses.md](lenses.md) — the agent and lens patterns to copy.
