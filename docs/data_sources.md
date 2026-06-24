# Data sources

> Part of the [docs/](README.md) documentation set. For how these are wired into the
> pipeline, see [agents.md](agents.md#retrieval--10-acquisition-nodes-over-30-sources);
> for the in-process access pattern, [components.md](components.md#a-note-on-mcp-servers).

The system draws on **27 source connectors** under
[src/mcp_servers/](src/mcp_servers/), each a folder with a `tools.py` (the real fetch logic,
imported and run **in-process** by the pipeline) and — for every public source — a `server.py`
(a `FastMCP` wrapper). For acquisition the pipeline never speaks MCP; the `server.py` wrappers
are instead the surface the standalone **MCP gateway** exposes
([mcp_gateway.md](mcp_gateway.md)) — which the synthesis-phase `investigator` node also calls
over MCP to close evidence gaps. The
authoritative license list is [NOTICE.md](../NOTICE.md); this page maps each connector to the
pipeline node that consumes it and flags the commercial gates.

> **Connectors vs. named sources.** Folder count, named-source count, and acquisition-node
> count are three different numbers by design — see
> [faq.md](faq.md#whats-the-difference-between-a-connector-folder-and-a-named-source) for why
> (e.g. `ontology` bundles three sources; one acquisition node draws on many connectors).
> `internal_data` is the one connector with **no** `server.py` (it is SENSITIVE and never
> exposed over MCP).

**Named sources, alphabetically (30+ public + your own internal data):** ChEMBL · ClinGen ·
ClinicalTrials.gov · ClinVar · DepMap · DGIdb · ENCODE · Expression Atlas · GBD (IHME) · GenCC ·
gnomAD · Google Patents · GTEx · GWAS Catalog · HGNC · HPA · IMPC · Monarch Initiative · MONDO ·
OMIM · OpenAlex · OpenFDA · OpenTargets · Orphanet · Project Score · PubMed · SCImago (SJR) ·
SPOKE · TTD · UniProt · USPTO · internal data (your org's private tables)

---

## Integrations by role

### Literature
| Server | Source | Feeds | Notes |
|---|---|---|---|
| `pubmed` | PubMed / MEDLINE (NLM) | `literature` agent → `ARTICLE`/`ABSTRACT` | Primary literature retrieval. |

### Genetics (all feed the `genetics` agent → `GENETICS` / `CONSTRAINT`)
| Server | Source | Notes |
|---|---|---|
| `gnomad` | gnomAD + ClinVar (Broad) | LoF/missense constraint; ClinVar P/LP variants (accessed via the gnomAD API). |
| `gwas_catalog` | GWAS Catalog (EMBL-EBI/NHGRI) | Genome-wide association signal. |
| `clingen` | ClinGen (NIH) | Gene–disease validity (bulk JSON-LD). |
| `gencc` | GenCC | Aggregated curated gene–disease validity (bulk CSV). |
| `omim` | OMIM (Johns Hopkins) | Mendelian gene–phenotype. **Gated `OMIM_ENABLED`, non-commercial, off by default**; also needs `OMIM_API_KEY`. |
| `orphanet` | Orphanet / Orphadata (INSERM) | Rare-disease gene–disease associations. |
| `spoke` | SPOKE (UCSF) | Precomputed biomedical knowledge graph; corroborating edges. Also feeds `omics`. |
| `ontology` | HGNC + Mondo + Monarch (EMBL-EBI / Monarch) | Gene/disease ID + HPO/inheritance context. Also used by the planner for ID resolution. |
| `opentargets` | Open Targets (EMBL-EBI / Sanger) | Genetics association context (also its own service + planner resolution). |

### Epidemiology (feeds the `gbd` service → `EPIDEMIOLOGY`)
| Server | Source | Notes |
|---|---|---|
| `gbd` | GBD / IHME (https://ghdx.healthdata.org) | Disease-keyed, whole-population prevalence/incidence — the common-disease counterpart to Orphanet's rare-disease-only prevalence, feeding the commercial lens's market-size axis. No bundled data, no public API; **gated `GBD_ENABLED`, non-commercial, off by default**, also needs `GBD_DATA_PATH` pointing at an operator-downloaded CSV extract. |

### Omics & expression (feed the `omics` agent → `OMICS` / `EXPRESSION` / `REGULATORY_ELEMENT`)
| Server | Source | Notes |
|---|---|---|
| `gtex` | GTEx (NIH Common Fund) + HPA (Science for Life Laboratory) | Tissue expression (bulk TPM) plus HPA tissue specificity and UniProt-resolved subcellular localization. |
| `expression_atlas` | Expression Atlas (EMBL-EBI) | Disease-vs-control differential expression. |
| `encode` | ENCODE | cis-regulatory assay coverage at the locus → `REGULATORY_ELEMENT`. |
| `spoke` | SPOKE (UCSF) | Anatomical/association edges (shared with genetics). |

### Functional genomics (feed the `functional` service → `FUNCTIONAL_GENOMICS`)
| Server | Source | Notes |
|---|---|---|
| `depmap` | DepMap (Broad) | CRISPR/RNAi dependency across cancer lines. |
| `project_score` | Project Score / Cell Model Passports (Sanger) | Sanger CRISPR knockout-fitness; largely non-overlapping panel vs DepMap. CC BY 4.0. |
| `impc` | IMPC (mousephenotype.org, via EBI Solr) | Knockout-mouse phenotype / viability. |

### Clinical, druggability, regulatory, patent
| Server | Source | Feeds | Notes |
|---|---|---|---|
| `clinicaltrials` | ClinicalTrials.gov (NIH) | `clinical_trial` service → `CLINICAL_TRIAL` | |
| `uniprot` | UniProt (EMBL-EBI) | `druggability` service → `DRUGGABILITY` | Protein class, subcellular location, function; carries the ChEMBL target cross-reference consumed by `chembl` below. CC BY 4.0. |
| `chembl` | ChEMBL (EMBL-EBI) | `druggability` service → `DRUGGABILITY` | Drug mechanisms, clinical candidates, potency/bioactivity distribution, keyed off the ChEMBL target id resolved via `uniprot`. CC BY-SA 3.0. |
| `dgidb` | DGIdb (Washington University) | `druggability` service → `DRUGGABILITY` | Curated drug-gene interaction claims + druggable-genome gene-category annotations, aggregated across dozens of source DBs. |
| `ttd` | Therapeutic Target Database (idrblab) | `druggability` service → `DRUGGABILITY` | TTD's own target development-stage classification + mapped drugs. **Gated `TTD_ENABLED`, off by default — commercial-use terms unconfirmed**; bulk-file download URL also unverified, see `mcp_servers/ttd/tools.py`. |
| `openfda` | OpenFDA (FDA) | `openfda` service → `REGULATORY` | Drug labels (SPL) + FAERS adverse-event signal. |
| `uspto` | USPTO / PatentsView | `patent` service → `PATENT` | Needs `USPTO_API_KEY` (free). Google Patents links are for manual lookup only — never scraped. |

### Competition / commercial landscape (feeds the `indication_competition` service → `COMPETITION`)
`openfda` and `clinicaltrials` (above) are *additionally* queried **by indication/condition**,
target-agnostic, to count approved drugs and active trials for the disease regardless of
mechanism — the indication-level counterpart to the gene-keyed `REGULATORY`/`CLINICAL_TRIAL`
rows from the same two sources. Feeds the commercial lens's competitive-landscape axis; no
new source, no new gating.

### Journal quality (feed `source_quality`, not an evidence type)
| Server | Source | Notes |
|---|---|---|
| `openalex` | OpenAlex / OurResearch | **CC0 (public domain)** — the default, commercial-safe journal-quality signal. Gated `OPENALEX_ENABLED`. |
| `scimago` | SCImago Journal & Country Rank (SJR) | **Non-commercial only**; bundled index under `src/mcp_servers/scimago/data/`. Gated `SCIMAGO_SJR_ENABLED`, **off by default**. |

### Internal / proprietary
| Server | Source | Notes |
|---|---|---|
| `internal_data` | Your organization's private data | Feeds `genetics`, `omics`, and `functional`. **Always classified `SENSITIVE`** — see below. |

---

## Sensitivity classification & data safety

Evidence is classified `SENSITIVE` or `NON_SENSITIVE` by `classify()`
([core/routing/classify.py](src/core/routing/classify.py)). The rule is simple and enforced
in code, not just documented:

```python
_SENSITIVE_AGENTS = {"internal_data"}
```

Anything originating from the **`internal_data`** source is `SENSITIVE`; everything else is
`NON_SENSITIVE`. `SENSITIVE` evidence is **always routed to the local Ollama model**,
regardless of routing policy — it never leaves the machine to a cloud provider. Only
`NON_SENSITIVE` evidence may be sent to a configured cloud LLM (and even that can be
commercially sensitive — the gene/disease pair itself — so review your provider's terms
before enabling a cloud policy). This is enforced jointly by `classify.py` and
[core/routing/policy.py](src/core/routing/policy.py).

---

## Licensing & commercial gating

The project is Apache-2.0, but **some data carries narrower terms and is therefore off by
default.** There are exactly five gating flags in code (`OMIM_ENABLED`, `OPENALEX_ENABLED`,
`SCIMAGO_SJR_ENABLED`, `TTD_ENABLED`, `GBD_ENABLED`):

| Source | Flag | Default | Commercial use |
|---|---|---|---|
| OMIM | `OMIM_ENABLED` (+ `OMIM_API_KEY`) | **off** | Non-commercial only — confirm terms with OMIM before enabling. |
| SCImago SJR | `SCIMAGO_SJR_ENABLED` | **off** | Non-commercial only — remove the bundled `scimago/data/` index or get SCImago authorization for commercial use. |
| TTD | `TTD_ENABLED` | **off** | **Unconfirmed** — terms not independently verified for this integration; confirm directly with TTD before enabling. |
| GBD | `GBD_ENABLED` (+ `GBD_DATA_PATH`) | **off** | Non-commercial only (IHME Free-of-Charge Non-commercial User Agreement) — confirm terms with IHME before enabling. |
| OpenAlex | `OPENALEX_ENABLED` | on | **CC0** — commercial-safe; the default journal-quality source and the fallback when SJR is disabled. |

So a clean commercial deployment leaves OMIM, SJR, GBD, and TTD disabled and relies on
OpenAlex for journal quality. Full per-source terms (and the required SCImago citation) are
in [NOTICE.md](../NOTICE.md).

### Journal quality: SJR vs OpenAlex

`source_quality` (read by every lens, see [lenses.md](lenses.md#source_quality--the-field-every-lens-reads-but-no-lens-computes))
prefers **SJR** quartile/score when `SCIMAGO_SJR_ENABLED`, and otherwise falls back to
**OpenAlex** journal metrics. The fallback exists precisely because SJR is non-commercial
while OpenAlex is CC0 — the system stays commercial-safe by default without losing a
journal-quality signal.

---

## API keys

Two integrations need a free key (see [.env.example](.env.example)):

- `USPTO_API_KEY` — required for patent search (without it, `uspto` returns nothing).
- `OMIM_API_KEY` — required *and* `OMIM_ENABLED=true` for OMIM.

`gbd` and `scimago` need no API key, but aren't keyless public endpoints either — both have
**no API at all**. `gbd` requires `GBD_DATA_PATH` pointing at an operator-downloaded GHDx CSV
extract (plus `GBD_ENABLED=true`); `scimago` reads a bundled, gitignored index under
`src/mcp_servers/scimago/data/` (plus `SCIMAGO_SJR_ENABLED=true`). See the licensing table
above for both.

Every other source is a keyless public endpoint (subject to each source's terms of use).

---

## See also

- [components.md](components.md#a-note-on-mcp-servers) — how the pipeline consumes these
  in-process, and how the gateway exposes them over MCP.
- [mcp_gateway.md](mcp_gateway.md) — the gateway that exposes the `server.py` wrappers.
- [NOTICE.md](../NOTICE.md) — authoritative licenses, citations, and the data disclaimer.
