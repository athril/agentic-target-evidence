# NOTICE

Agentic Target Evidence
Copyright 2026 Patryk Orzechowski (patryk.orzechowski@gmail.com)

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

---

## Disclaimer

This software's primary purpose is to gather evidence about a given gene
target for a given disease — abstracts, patents, clinical trial records, and
other data from many sources — into a single place. On top of that gathered
evidence, it produces several AI-generated artifacts (lens verdicts, summary,
and full report) to help orient that evidence — **these are the output of
AI analysis and must not be trusted blindly.**

It is intended as a **preliminary research tool** to accelerate the early,
labor-intensive evidence-gathering phase of target validation, and is meant
to **complement, not replace, existing tooling and expert review** already in
use within an organization.

- **AI-generated, informational only — not ground truth.** Every lens verdict,
  summary, and report is produced by an LLM reasoning over retrieved evidence.
  It is a synthesis intended to inform and accelerate further investigation,
  not a verified fact or a substitute for one. See `docs/lenses.md` for what
  each of the six lenses evaluates, what a verdict contains, and how
  cross-lens disagreement is surfaced rather than collapsed into a single
  score.
- **Analysis may be oversimplified, and may omit relevant facts or
  contradictions.** The lenses summarize and reconcile evidence to produce a
  readable verdict; in doing so they can compress away nuance, miss a relevant
  study, or fail to surface a contradiction present in the underlying
  evidence. Always check the underlying evidence under `results/data/` and
  the full report before treating a verdict as complete.
- **LLMs can hallucinate, lose context, or draw incorrect conclusions.**
  Apply critical thinking and your own domain judgment to every claim before
  acting on it — especially before any decision with clinical, regulatory, or
  financial consequences.
- **Quality depends on the configured model.** The depth and reliability of
  the reasoning varies with the underlying LLM (see `config/routing.yaml`) —
  results from a small local model and a frontier cloud model are not
  equivalent.
- **This tool empowers experts, it does not replace them.** It is built to
  significantly reduce evidence-gathering time, not to substitute for expert
  review, validation, or sign-off. Current capabilities — even with smaller
  models, including local models routed via the `local` or `hybrid`
  policies — are approaching the level of a first pass by a junior
  translational scientist doing the same evidence-gathering and triage work.
  This is an early-stage capability assessment, not a guarantee, and does not
  change the rest of this disclaimer: outputs still require expert review
  before being acted on.
- **Sensitive data never leaves the local machine.** Evidence classified
  `SENSITIVE` (currently: anything returned by the `internal_data` MCP
  server) is always routed to the local Ollama model, regardless of the
  configured routing policy — this is enforced in code
  (`src/core/routing/policy.py`, `src/core/routing/classify.py`), not just
  documented. Only `NON_SENSITIVE` evidence may be sent to a configured cloud
  provider.
- **Cloud LLM providers are third parties.** Under the `hybrid` or `custom`
  routing policies, non-sensitive evidence (which can still be commercially
  sensitive, e.g. the gene/disease pair under investigation) is sent to
  whichever cloud provider is configured — Anthropic, OpenAI, Google Gemini,
  or AWS Bedrock. That data is subject to the chosen provider's own terms of
  service and data-retention/training-use policies, independent of this
  project's license. Review your provider's terms before enabling a
  cloud-routing policy.
- **Everything is traceable.** Every LLM call, tool call, and A2A message is
  recorded as a Langfuse trace, viewable at `http://localhost:3000` (or your
  configured Langfuse instance) by `trace_id` / `run_id` — use it to audit how
  any conclusion was reached.
- **All output is written under `results/`.** Every run is organized by
  `{gene}/{disease}/{direction}`:
  - `results/data/{gene}/{disease}/{direction}/` — the raw per-source archive
    (abstracts, patents, clinical trial records, genetics/omics data, and other
    retrieved evidence) plus a `summary.csv` of what was gathered.
  - `results/report/{gene}/{disease}/{direction}/` — the generated report
    (`report.md`, `full_report.md`) and the individual lens verdicts under
    `lenses/`.

  Nothing under `results/` is ever deleted or treated as a cache by the
  application — review it directly to see exactly what evidence and reasoning
  produced a given verdict.
- **Lens markdown files are rewritten during a run — only trust the final
  version.** Each lens verdict file under `lenses/` may be written multiple
  times over the course of a single run: an initial draft is produced and then
  revised in place as review/critic agents revise the verdict. The content can
  change between writes. Only the version present after the run has fully
  completed is authoritative — do not read, copy, or act on a lens file while a
  run is still in progress, as intermediate drafts may contain claims that were
  subsequently corrected or removed.
- **Evidence and LLM calls are cached at the database layer.** Re-running the
  same gene/disease/direction reuses previously retrieved evidence and
  previously generated lens verdicts from Postgres (`EvidenceRow` and
  `llm_cache`) instead of re-querying sources or re-prompting the model — this
  is independent of `results/`, which is never consulted for this purpose. Set
  `force_refresh=true` to bypass both caches and force fresh retrieval and
  reasoning.

---

## Third-Party Notices

This product includes software developed by third parties. Their licenses and
notices are listed below.

### Python Dependencies

Dependencies are listed in `pyproject.toml`. Each dependency is governed by its
own license. Please refer to the individual package documentation for details.

Notable dependencies and their licenses:

- **LangGraph / LangChain** — MIT License (https://github.com/langchain-ai/langgraph)
- **Langfuse** — MIT License (https://github.com/langfuse/langfuse)
- **FastMCP** — Apache License 2.0 (https://github.com/PrefectHQ/fastmcp)
- **Pydantic** — MIT License (https://github.com/pydantic/pydantic)
- **PostgreSQL (psycopg)** — LGPL-3.0-only (https://www.psycopg.org)
- **pgvector** — MIT License (https://github.com/pgvector/pgvector)

---

## Data Sources

This software may interact with the following public data sources. Their use is
subject to each source's own terms of service. A license is noted below only
where it has been confirmed against the source's own citation/terms page —
the absence of a license tag is not an indication that the data is
unrestricted; consult the source directly before redistribution or commercial
use:

- **ChEMBL** — EMBL-EBI (https://www.ebi.ac.uk/chembl) — CC BY-SA 3.0
- **ClinGen** — Clinical Genome Resource, NIH-funded (https://clinicalgenome.org). Gene-disease validity data is ingested from the bulk JSON-LD dataset published on the genegraph.clinicalgenome.org "Downloads" page, not by scraping search.clinicalgenome.org — that site's robots.txt disallows crawlers other than a fixed list of search engines.
- **ClinicalTrials.gov** — U.S. National Institutes of Health (https://clinicaltrials.gov)
- **ClinVar** — National Library of Medicine / NCBI (https://www.ncbi.nlm.nih.gov/clinvar) — accessed via the gnomAD API; U.S. government work, public domain
- **DepMap** — Broad Institute (https://depmap.org)
- **DGIdb** — Drug Gene Interaction Database, McDonnell Genome Institute, Washington University (https://dgidb.org) — curated drug-gene interaction claims and druggable-genome gene-category annotations, aggregated from dozens of source databases (DrugBank, ChEMBL, PharmGKB, CIViC, OncoKB, FDA, etc.) via the public GraphQL API. DGIdb's own software/data layer is openly redistributable, but each aggregated interaction inherits the license of its original source database — consult https://dgidb.org/browse/sources before redistributing a specific interaction claim.
- **ENCODE** — ENCODE Project Consortium (https://www.encodeproject.org) — cis-regulatory element (cCRE) annotations, accessed via region-search.
- **Expression Atlas** — EMBL-EBI (https://www.ebi.ac.uk/gxa) — disease-vs-control differential expression.
- **GBD (Global Burden of Disease)** — Institute for Health Metrics and Evaluation, IHME (https://www.healthdata.org / extract source: https://ghdx.healthdata.org) — disease-keyed prevalence/incidence, via an operator-downloaded GBD Results Tool CSV extract (no bundled data, no public API). Distributed under the **IHME Free-of-Charge Non-commercial User Agreement**. Disabled by default and gated behind `GBD_ENABLED` (in addition to `GBD_DATA_PATH` pointing at the operator's own extract) so commercial deployments stay clean — commercial users must confirm terms directly with IHME before enabling this source.
- **GenCC** — Gene Curation Coalition (https://thegencc.org) — aggregated curated gene-disease validity classifications, via the bulk CSV export.
- **gnomAD** — Broad Institute (https://gnomad.broadinstitute.org)
- **Google Patents** — Google LLC (https://patents.google.com) — patent records include a plain link to the patent's Google Patents page for manual lookup only. This software never fetches from or scrapes Google Patents.
- **GTEx** — NIH Common Fund (https://gtexportal.org)
- **GWAS Catalog** — EMBL-EBI / NHGRI-EBI (https://www.ebi.ac.uk/gwas)
- **HGNC** — HUGO Gene Nomenclature Committee, EMBL-EBI (https://www.genenames.org) — gene symbol/alias resolution.
- **Human Protein Atlas (HPA)** — Science for Life Laboratory (https://www.proteinatlas.org) — CC BY-SA 4.0
- **IMPC** — International Mouse Phenotyping Consortium (https://www.mousephenotype.org) — knockout-mouse phenotype data, accessed via the EBI Solr genotype-phenotype API.
- **Monarch Initiative** — (https://monarchinitiative.org) — HPO-derived phenotype annotations for genes.
- **Mondo Disease Ontology** — Monarch Initiative / OBO Foundry, accessed via EBI Ontology Lookup Service (https://www.ebi.ac.uk/ols4) — disease cross-references. CC BY 4.0.
- **OMIM** — Johns Hopkins University / OMIM (https://www.omim.org) — Mendelian gene-phenotype associations, via the bulk `genemap2.txt` download. Requires a free academic/research API key; OMIM restricts use to educational, internal research, and other non-commercial purposes. Disabled by default and gated behind `OMIM_ENABLED` (in addition to `OMIM_API_KEY`) so commercial deployments stay clean — commercial users must confirm terms directly with OMIM before enabling this source.
- **OpenAlex** — OpenAlex / OurResearch (https://openalex.org) — journal-level quality metrics (2-year mean citedness, h-index, DOAJ listing). Released under **CC0 1.0 (public domain)**, so usable commercially. Serves as the default, commercial-safe journal-quality signal and the fallback when the non-commercial SJR data is disabled (see SJR entry below).
- **OpenFDA** — U.S. Food and Drug Administration (https://open.fda.gov) — drug labels (SPL) and adverse event reports (FAERS)
- **Open Targets** — EMBL-EBI / Wellcome Sanger Institute (https://www.opentargets.org)
- **Orphanet / Orphadata** — INSERM (https://www.orphadata.com) — rare-disease gene-disease associations.
- **Project Score / Cell Model Passports** — Wellcome Sanger Institute (https://score.depmap.sanger.ac.uk, API at https://api.cellmodelpassports.sanger.ac.uk) — genome-wide CRISPR-Cas9 knockout-fitness screens across cancer cell lines; the Sanger counterpart to Broad's DepMap, on a largely non-overlapping cell line panel. CC BY 4.0.
- **PubMed / MEDLINE** — National Library of Medicine (https://pubmed.ncbi.nlm.nih.gov)
- **SCImago Journal & Country Rank (SJR)** — SCImago Research Group (https://www.scimagojr.com). Per the SCImago Journal & Country Rank portal terms, all information shown on the portal may be used **for non-commercial purposes only, provided it is cited** (see citation below). The bundled `src/mcp_servers/scimago/data/` index is built (`scripts/build_scimago_index.py`) from a flat-file mirror of this same data published by the `sjrdata` project (https://github.com/ikashnitsky/sjrdata, MIT-licensed packaging) — SCImago's own export endpoint blocks non-browser clients, so this is the publicly redistributed form of the identical dataset. The underlying SJR values are derived from Scopus (Elsevier).

  **Required citation:** SCImago, (n.d.). SJR — SCImago Journal & Country Rank [Portal]. Retrieved June 19, 2026, from https://www.scimagojr.com
  *(update this retrieval date whenever `scripts/build_scimago_index.py` is rerun against a newer SCImago/`sjrdata` export.)*

  **Commercial-use note:** the SJR data's non-commercial restriction is narrower than this project's Apache 2.0 license. Commercial deployments must either remove the bundled SJR index (`src/mcp_servers/scimago/data/`) or obtain separate authorization from SCImago. See the Commercial Inquiries section below.

- **SPOKE** — UCSF (https://spoke.rbvi.ucsf.edu) — precomputed biomedical knowledge graph; corroborating genetics/omics edges.
- **Therapeutic Target Database (TTD)** — idrblab (https://ttd.idrblab.cn) — target development-stage classification (Successful/Clinical Trial/Research Target) + mapped drugs, via a bulk per-target text file. **Commercial-use terms not independently verified for this integration** — TTD's site is a client-rendered SPA that could not be read by automated fetchers when this source was added, so neither the license nor the exact current download URL/field layout was confirmed against the live site. Treated conservatively as non-commercial pending confirmation and disabled by default, gated behind `TTD_ENABLED` — commercial users must confirm current terms directly with TTD before enabling this source, and must also confirm/update the placeholder download URL in `src/mcp_servers/ttd/tools.py`.
- **UniProt** — UniProt Consortium / EMBL-EBI (https://www.uniprot.org) — CC BY 4.0
- **USPTO Patent Data** — United States Patent and Trademark Office (https://developer.uspto.gov). Requires a personal `USPTO_API_KEY` (free, register at https://data.uspto.gov/apis/getting-started) — without it, USPTO patent search will not work.

---

## Contact

For questions about this software, contact:

Patryk Orzechowski
patryk.orzechowski@gmail.com

### Collaboration

We welcome collaboration with academic researchers, bioinformaticians, and
drug-discovery teams. If you are interested in extending the system, contributing
new data-source integrations, or co-authoring research, please reach out at the
address above.

### Commercial Inquiries

Apache 2.0 permits commercial use of this software without restriction. Note,
however, that some third-party data carries narrower terms and is therefore
off by default. In particular the SCImago Journal & Country Rank (SJR) index
(`SCIMAGO_SJR_ENABLED`), OMIM (`OMIM_ENABLED`), and GBD (`GBD_ENABLED`) are
licensed for non-commercial use only (see Data Sources above). Commercial
deployments must leave these disabled, or obtain separate authorization from
SCImago / OMIM / IHME respectively (and remove the bundled SJR index). TTD
(`TTD_ENABLED`) is disabled by default for the same reason, though its terms
are unconfirmed rather than confirmed non-commercial — commercial deployments
must leave it disabled until they've checked TTD's current terms directly.

If your organization is interested in support, custom integrations, or
prioritized feature development, please reach out at the address above to
discuss partnership opportunities.
