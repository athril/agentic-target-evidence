# Langfuse Project Naming

Use this skill when a user asks how runs are organized in Langfuse, how to add a new gene/disease combination as a project, or why a project isn't showing up.

## How it works

Each pipeline run is automatically isolated in its own Langfuse project named:

```
GENE_SYMBOL (ENSEMBL_ID) | disease name (disease_id)
```

Example: `PTPN1 (ENSG00000196396) | pancreatic cancer (EFO_0002618)`

When `run_pipeline()` is called, `ensure_langfuse_project()` in
`packages/core/telemetry/projects.py`:

1. Checks `config/langfuse_projects.json` for a cached entry.
2. If absent, inserts a new row into the Langfuse `projects` table and a
   project-scoped API key pair into `api_keys` (both in the `langfuse`
   Postgres database on port 5433).
3. Calls `Langfuse(public_key=…, secret_key=…)` to redirect the global
   Langfuse client to that project before the `@observe()` trace starts.

The IDs in the title come from `PipelineState.gene_id` and
`PipelineState.disease_id`, which are resolved by the OpenTargets agent.
If the IDs are not yet known when the run starts, the title degrades
gracefully to `GENE_SYMBOL | disease name` (no parentheses).

## Adding a project name manually

You should never need to do this — projects are created automatically on first
run. But if you need to pre-populate or rename one:

```python
import asyncio, os
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5433/gene_target_validation"

from packages.core.telemetry.projects import ensure_langfuse_project

async def main():
    pk, sk = await ensure_langfuse_project("BRCA1 (ENSG00000012048) | breast cancer (EFO_0000305)")
    print(pk, sk)

asyncio.run(main())
```

This writes to the cache and to Langfuse Postgres. The project appears
immediately in the org view at `http://localhost:3000/organization/gtv-org`.

## Cache file

`config/langfuse_projects.json` (gitignored) maps each title to its
`project_id`, `public_key`, `secret_key`, and `base_url`. Delete an entry
from this file to force re-provisioning (a new project and key pair will be
created; the old project is not deleted).

## Title format rules

The title is built by `_trace_title()` in `graph/build_graph.py`:

| Available info | Title format |
|---|---|
| Gene + gene_id + disease + disease_id | `BRCA1 (ENSG00000012048) \| breast cancer (EFO_0000305)` |
| Gene + disease only (IDs not yet resolved) | `BRCA1 \| breast cancer` |

The pipe separator is a literal `|` character. Keep titles short — Langfuse
truncates long project names in the sidebar.

## Troubleshooting

**Project not appearing in UI after a run**
- Check `config/langfuse_projects.json` — if the entry is there but the UI is
  empty, the Langfuse Postgres write succeeded but the UI may need a refresh.
- If the entry is missing, look for `[telemetry] project provisioning failed`
  in the run log. The most common cause is `DATABASE_URL` not set or the
  Langfuse DB being unreachable.

**Traces landing in "Gene Target Validation" instead of the gene project**
- Provisioning failed and the fallback (default project) was used.
- Confirm `LANGFUSE_BASE_URL` in `.env` matches the running Langfuse host.
- The Langfuse DB URL is derived from `DATABASE_URL` by replacing the DB name
  with `langfuse`; override with `LANGFUSE_DATABASE_URL` if the Langfuse DB
  is on a different host or port.

**Parallel runs overwrite each other's Langfuse client**
- `Langfuse()` replaces the global client, so two concurrent `run_pipeline()`
  calls can race. Sequential runs are safe. For parallel pipelines, refactor
  `run_pipeline()` to pass the `Langfuse` instance explicitly instead of
  relying on the global singleton.
