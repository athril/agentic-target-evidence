# Restricted sources — setup guide

> Part of the [docs/](README.md) documentation set. [data_sources.md](data_sources.md#licensing--commercial-gating)
> has the reference table (flag, default, commercial terms) for every gated source; this page
> is the step-by-step "how do I actually turn this on" companion to that table.

Four source connectors carry license terms narrower than this project's Apache-2.0 and are
therefore **off by default**, each behind its own `*_ENABLED` flag: **OMIM**, **SCImago SJR**,
**GBD (IHME)**, and **TTD**. Flipping a flag to `true` is never enough on its own — each one
also needs either an API key or an operator-supplied data file, obtained outside this repo.
None of them are required for a clean commercial run; a default checkout works fully without
any of the four (OpenAlex covers journal quality, and the other lenses don't depend on the
rest).

**Before enabling any of these for non-commercial/academic use, confirm current terms directly
with the source** — license terms outside this repo's control can change. See
[NOTICE.md](../NOTICE.md) for the full per-source license text this project ships under.

---

## OMIM

Gene-disease "validity" classifications, refreshed from OMIM's own bulk download.

1. Register for a free academic/research API key at https://www.omim.org/api.
2. Set in `.env`:
   ```
   OMIM_API_KEY=<your key>
   OMIM_ENABLED=true
   ```
3. First call to `omim_get_validity` (or the `genetics` acquisition node) downloads
   `genemap2.txt` via `https://data.omim.org/downloads/{api_key}/genemap2.txt`
   ([tools.py](../src/mcp_servers/omim/tools.py)) and caches it locally — no per-call network
   round trip after that.
4. Verify: `uv run pytest tests/mcp_servers/test_omim.py -q`.

Commercial use is **not** covered by the free academic key — confirm a separate license with
OMIM before enabling this in a commercial deployment.

---

## SCImago Journal Rank (SJR)

Journal prestige (SJR score + quartile) for the source-quality signal, used in place of (or
alongside) OpenAlex.

SCImago's own export endpoint sits behind a Cloudflare JS challenge that blocks non-browser
clients, so there is no live API call here at all — instead you build a small **bundled,
offline index** once, from a third-party mirror of SCImago's own "freely available" data:

1. Build the index (gitignored — `src/mcp_servers/scimago/data/*.json.gz` — so you build it
   per checkout, it's never committed):
   ```bash
   uv run --with pyarrow --with httpx scripts/build_scimago_index.py --year 2025
   ```
   This pulls the `sjrdata` mirror (https://github.com/ikashnitsky/sjrdata) and writes
   `src/mcp_servers/scimago/data/scimago_<year>.json.gz`. Re-run yearly to refresh; it never
   runs as part of the application itself. See
   [build_scimago_index.py](../scripts/build_scimago_index.py) for provenance details and the
   `--input` flag if you already have a local parquet file.
2. Set in `.env`:
   ```
   SCIMAGO_SJR_ENABLED=true
   ```
3. Verify: `uv run pytest tests/mcp_servers/test_scimago.py -q`.

Non-commercial/academic use only. For commercial deployments, either leave this off (the
default — OpenAlex's CC0 data covers journal quality instead) or get SCImago's own
authorization for commercial use first.

---

## GBD (Global Burden of Disease, IHME)

Disease-keyed, whole-population prevalence/incidence — the common-disease counterpart to
Orphanet's rare-disease-only prevalence, feeding the commercial lens's market-size axis.

There is no public API and no bundled data — IHME's GBD Results Tool is an interactive query
builder behind a registration wall, not something this project scrapes or redistributes:

1. Go to https://ghdx.healthdata.org, use the **GBD Results Tool**, and export a CSV filtered
   to the cause(s)/disease(s) you need, with `measure ∈ {Prevalence, Incidence}` (filtering
   keeps the extract small instead of pulling the full global dataset).
2. The exported CSV must contain these columns (a standard GBD Results Tool export already
   does): `cause_id, cause_name, measure_name, metric_name, location_name, year, val, upper,
   lower` ([tools.py](../src/mcp_servers/gbd/tools.py)).
3. Set in `.env`:
   ```
   GBD_ENABLED=true
   GBD_DATA_PATH=/path/to/your/gbd_extract.csv
   ```
4. Mapping from a `(gene, disease)` pair's disease string/MONDO id to a GBD `cause_id` is
   normalized-name-first, falling back to an explicit crosswalk at
   [config/gbd_cause_crosswalk.yaml](../config/gbd_cause_crosswalk.yaml) for cases where
   naming doesn't line up cleanly — add an entry there if a disease you care about isn't
   resolving.
5. Verify: `uv run pytest tests/mcp_servers/test_gbd.py tests/capabilities/target_validation/test_gbd_node.py -q`.

Distributed under IHME's **Free-of-Charge Non-commercial User Agreement** — confirm terms with
IHME before enabling this in a commercial deployment.

> `GBD_ENABLED=true` with an empty or unset `GBD_DATA_PATH` is a common half-configured state:
> the node still runs every time, it just returns zero records (`mapping="none"`) — check the
> `[node] gbd: 0 items` log line if GBD evidence isn't showing up in a report.

---

## TTD (Therapeutic Target Database)

Target development-stage classification (e.g. clinical-trial vs. approved-drug target).

**Unverified — confirm before enabling.** Unlike the three sources above, this integration's
download URL and commercial-use terms have not been independently verified against TTD's
current "Data Download" page:

1. Visit TTD's "Data Download" page directly and confirm the current bulk-download link and
   field layout against `_DOWNLOAD_URL` in
   [src/mcp_servers/ttd/tools.py](../src/mcp_servers/ttd/tools.py) — it is currently a
   **placeholder** (`P1-01-TTD_target_download.txt`) that may be stale.
2. Confirm TTD's current terms cover your use case (commercial or non-commercial) directly
   with TTD — this project makes no claim either way.
3. Only then set in `.env`:
   ```
   TTD_ENABLED=true
   ```
4. Verify: `uv run pytest tests/mcp_servers/test_ttd.py -q`.

---

## Checking what's actually mounted

Each flag controls two independent things, both worth checking after setup:

- **The pipeline node** (e.g. `genetics` for OMIM, `gbd` for GBD) — runs every time
  regardless of the flag; the flag only controls whether its *service* returns real data or an
  empty result.
- **The MCP gateway's tool list** — `_OPTIONAL_GATES` in
  [src/mcp_gateway/server.py](../src/mcp_gateway/server.py) excludes a gated source's tool
  entirely from `list_tools()` when its flag is off, so a connected client (chat assistant,
  Claude Desktop) never sees a tool that would just return "disabled" on every call.

To confirm a source is live end-to-end, run a real `make run GENE=... DISEASE=...` and check
the per-node log line (`[node] <name>: N items`) rather than just the flag value.
