# Gene profile skill and Claude Code plugin

> Part of the [docs/](README.md) documentation set. For the full go/no-go pipeline see
> [tutorial.md](tutorial.md); for ad hoc single-source lookups see
> [mcp_tutorial.md](mcp_tutorial.md).

`gene-profile` is a [Claude Code](https://code.claude.com/docs/en/overview) skill that writes a
citation-checked profile of one human gene for a researcher who wants to understand it: what it
does and how we know, its protein and variants (an allelic series), expression, human genetics,
disease links, a research toolkit (tool compounds, model organisms, cell lines), what is
established, contested and open, and a literature reading path, followed by shorter druggability,
clinical, safety and commercial/regulatory sections. It can be scoped to one disease. The output
is `<GENE>-profile.md` with its figures in `figures/` (SVG), plus `<GENE>-profile.pdf` when you
ask for one.

Up to release 0.1.4 it was called `gene-dossier` and wrote `<GENE>-report.*` into
`results/dossiers/`. It was renamed so that "dossier" refers only to the pipeline's output.

It reuses the same source connectors as the pipeline ([src/mcp_servers/](../src/mcp_servers/)),
but it is a separate, lighter product:

| | Pipeline (`make run`) | `gene-profile` skill |
|---|---|---|
| Question | Is this gene a viable target for this disease, in this direction? | What is known about this gene? |
| Output | Consensus verdict, 0–100 suitability score, six lens narratives | Descriptive profile with numbered references |
| Runs on | LangGraph + Postgres + Langfuse stack (`make up`) | Claude Code + `uv`; no services |
| Who writes the prose | Lens agents, checked by deterministic guards | Claude, checked by `check_profile.py` |

---

## Install and use

**Inside this repository** the skill loads automatically from
[.claude/skills/gene-profile/](../.claude/skills/gene-profile/SKILL.md). Nothing to install.

**In any other project**, install it from this repository's plugin marketplace:

```bash
claude plugin marketplace add athril/agentic-target-evidence
claude plugin install gene-profile@agentic-target-evidence
```

Then ask Claude for a gene profile ("profile TRPC6 in FSGS, with a PDF") or run `/gene-profile`.

Requirements:

- [uv](https://docs.astral.sh/uv/) and network access to the public biomedical APIs.
- Google Chrome or Chromium, only for the optional PDF (the Markdown report is written without it).
- Optional `NCBI_API_KEY`, `USPTO_API_KEY` and `BIOGRID_ORCS_ACCESS_KEY` (free at
  <https://orcsws.thebiogrid.org/>; without it the CRISPR-screens table says "not checked"), read from the environment or from `.env` in the
  working directory.

The first run outside a checkout takes a few extra minutes while uv builds the pinned release of
this package. Later runs reuse uv's cache.

Run directories land in `results/profiles/<GENE>_<date>/` under the working directory.

---

## How it works

The model never types reference metadata, and never types a number that exists in the
retrieved data. Scripts do the fetching, numbering, formatting and checking; Claude reads the
evidence and writes the prose.

| Step | Script | What it does |
|---|---|---|
| Gather | `gather.py <GENE> [--disease …] [--depth quick\|standard\|deep]` | Resolves the symbol (exit 2 = ambiguous, exit 3 = not found), queries ~60 sources in six dependent waves, writes `raw/<key>.json`, `digest.md`, `manifest.json` and a registered `sources.json`. A failing source is retried once, then logged as `error`; it never stops the run. |
| Add sources | `register_source.py <run_dir> abstract\|pmid\|nct\|web …` | Registers an extra PubMed, ClinicalTrials.gov or web source with fetched metadata, so citations are never invented. |
| Write | (Claude) | `draft.md` from [section_template.md](../.claude/skills/gene-profile/references/section_template.md), [citation_rules.md](../.claude/skills/gene-profile/references/citation_rules.md) and [gnomad_bands.md](../.claude/skills/gene-profile/references/gnomad_bands.md). Facts carry `[@key]` citations; numbers are `{{key:path\|fmt}}` anchors into `raw/`. |
| Test claims | `claim_search.py <run_dir>` | For each core claim in `claim_checks.json`, a targeted PubMed search. Gene-wide retrieval rarely reaches the paper that qualifies one claim, and a curated record's sentence can outlive the literature. The writer records whether the claim holds, needs qualifying or conflicts, and a changed claim cites the paper that changed it (`E_CLAIM_CHECK`). |
| Audit | `claim_audit.py <run_dir>` | Lists each block citing a paper for a reader who did not write the draft, who judges it against the cited abstracts alone ([claim_audit.md](../.claude/skills/gene-profile/references/claim_audit.md)). A supported verdict needs a quote the checker finds in those abstracts, and editing a block clears its verdict (`E_CLAIM_AUDIT`). |
| Review | `scientific_review.py <run_dir>` | Fills the report's tables into `review_input.md` for a reader who did not write it, who answers fixed questions on mechanism, genetics, constraint, safety reasoning, currency, agreement between sections and strength of claims ([scientific_review.md](../.claude/skills/gene-profile/references/scientific_review.md)). The answers go in `scientific_review.json`, and an open critical or high finding blocks release. |
| Check | `check_profile.py <run_dir>` | Errors (`E_*`) for unknown or unverified citation keys, broken anchors, uncited factual blocks (open questions included), Tier-3 sources in the Snapshot, missing sections, unknown programmatic blocks, a required block missing from its section (the template-2 research blocks; the allelic series when the run has one; the indication table on disease runs), malformed writer files (`E_HOW_WE_KNOW`, `E_MECHANISM`, `E_OPEN_QUESTION`, `E_CLAIM_CHECK`, `E_CLAIM_AUDIT`), anchors whose text misstates the unit (a fraction followed by `%`, a count called "N-fold") and a disease run described as unscoped; warnings (`W_*`) for hand-typed numbers (per sentence), Tier-3-only support, empty sections, cited papers PubTator3 shows are not about the gene, proper names none of a sentence's cited sources mention, draft-written References/Appendix sections (dropped at render), prose calling a claim established that the How-we-know table counts as one group or cells only (`W_STRENGTH_PROSE`), a source set against a claim from a different kind of evidence (`W_CROSS_KIND_CONTRADICTION`), an approved drug never discussed (`W_DRUG_NO_READOUT`), a conclusion from a screen's hit count (`W_SCREEN_INFERENCE`), an inference added to a paper's finding (`W_INFERENCE_IN_FACT`), a biochemical verb no cited abstract uses (`W_VERB_NOT_IN_SOURCE`) and a mechanism resting on a curated record alone (`W_RECORD_ONLY_MECHANISM`). It also runs the pipeline's `constraint` and `commercial` guards on the text (`E_GUARD_*`), and warns with `W_GUARDS_UNAVAILABLE` if it cannot import them. Writes `check_report.json`. At most two fix rounds; after that, `render.py --allow-issues` prints a *Known issues* section. |
| Render | `render.py <run_dir> [--pdf] [--no-figures]` | Expands the programmatic blocks (tables from `raw/`, figures drawn with matplotlib into `figures/`), substitutes anchors, numbers references and writes `<GENE>-profile.md`. `--pdf` (off by default) also prints `<GENE>-profile.pdf` with headless Chrome, figures embedded; the intermediate HTML goes to a temp dir and is deleted. |

`--depth deep` splits writing across four subagents (biology + research toolkit; human
genetics + disease associations + indication; view of the field + open questions; the
translational context) and merges their fragments.

### Grounded literature

Keyword search finds words, not genes (`"MET"[tiab]` returns 345,759 papers; NCBI links 2,891
to the gene). The skill grounds the literature by identifiers:

- `literature_scope` draws keyword hits across ten publication years and asks PubTator3 how
  many name the gene (its NCBI Gene id, an ortholog's, a variant of it, one of its drugs).
  Below half, the PubMed lists search the gene's curated papers (NCBI gene2pubmed, through the
  E-utilities history server) and the volume reported is the curated count.
- The most-cited list ranks the curated papers together with the keyword pool by NIH iCite, so
  landmarks a relevance pool misses (PCSK9: FOURIER, Cohen 2006) are found.
- `pubtator_map` reads PubTator3's annotations of the curated papers and every listed paper:
  the diseases, chemicals, genes and variants related to the gene inside its own papers (never
  PubTator's PubMed-wide relation search, which maps family names to one member), the organisms
  and cell lines studied, and whether each listed paper names the gene. Papers that do not are
  left out of the digest. Relation types are text-mining tags, not findings.
- `ncbi_gene` adds the RefSeq summary and NLM's GeneRIFs.
- A paper is the gene's literature only when gene2pubmed links it or PubTator tags a gene or
  variant mention with the gene's or an ortholog's NCBI id (`pubtator_map.grounded_pmids`);
  every block built from papers (field map, groups, methods, effect sizes, evidence strength)
  uses that set. A symbol, alias or previous symbol is a search term, never evidence: the
  sense table in `literature_scope` shows what PubTator types each term as in a sample of its
  keyword hits, and a term that is mostly another sense, or that HGNC also gives another gene,
  is not searched. The appendix prints the table.

### Allelic series, spectrum and figures

`allelic_series` joins UniProt natural variants (with UniProt's functional note and its PMIDs),
ClinVar pathogenic / likely pathogenic calls (≥ 1 review star, conflicting left out), gnomAD
pLoF, GWAS leads and the literature's variants, places each in UniProt's features, and types its
effect on the protein only from data: a truncating consequence, Open Targets' curated direction,
the change UniProt's curated note states ("increases calcium ion transport" → gain), or the
variant's own QTL (never for a protein-altering variant); the table ends with a tally of what every
disease variant does. ClinVar comes from its own record (`clinvar`, E-utilities), which keeps
what gnomAD's mirror drops: the condition each call is for, its review status and its number
of submissions. A classification is condition-specific, so the series names the condition
beside it; a run without `clinvar` falls back to the mirror. Rows list their nucleotide
alleles (HGVS c. on the named transcript, GRCh38 positions in raw): alleles sharing an rs id
are separate rows, and a splice-site call without a protein change is still a row. The table keeps four properties in separate columns: molecular effect (typed from records,
and from functional studies the writer records in `variant_function.json`, each checked
against the paper's abstract and labelled `[paper]`), clinical classification with its
condition, population association, and literature. The tally counts record-typed and
paper-typed effects separately. The
spectrum counts P/LP calls by
consequence class and review stars, and missense calls by protein region. `draft.md` places them
with `{{table:allelic_series}}` and `{{table:variant_spectrum}}`, the PDB/AlphaFold list with
`{{table:structures}}`, the gene × disease evidence of a disease run with `{{table:indication}}`,
and the figures with `{{figure:protein_map}}` and `{{figure:expression}}`; the renderer builds
them from `raw/`. The
figures are drawn from the records, never screenshots (site terms vary, HPA is CC BY-SA, and a
screenshot cannot be tied to the cited values); each repeats data a table also carries.

### Accuracy (template 3)

Runs gathered from 2026-11-01 onward use template 3, the same headings as template 2 with more
required evidence. `drug_landscape` merges Open Targets and on-target labels by shared name and
US application, and sets each stage by rule from Drugs@FDA first, since aggregators lag
approvals; FAERS, trial and safety lookups read it. It covers US approvals; other regulators
are not read, so an approval elsewhere shows as `Approved, no US application found`.
`how_we_know.json` gains `not_comparable`, `mechanism.json` gains `route`, and the digest opens
with what the abstracts say by topic. Older runs keep their template.

### Research blocks (template 2)

Template 2 (`manifest.template`) added the research sections
first, *View of the field*, and the translational sections under *Translational context*.
Older runs keep their layout and render as before. Every block's caption ends "From records:
…" or "Chosen by the LLM from: …", a block without data renders its empty state, and the
renderer footnotes cross-field terms (`references/terms.json`) at their first use.

| Block | Built from |
|---|---|
| `how_we_know` | the writer's claims (`how_we_know.json`); cells counted from each cited paper's design (PubMed types and MeSH via `mcp_servers.pubmed.design`), species, last author and stated n |
| `mechanism` | the writer's steps (`mechanism.json`), swimlanes by UniProt compartment, line style from the same strength counts |
| `models`, `partners`, `reagents`, `immune` | NCBI curated mouse ortholog + Ensembl homology (`orthologs`), MGI/IMPC phenotypes by MP class, STRING by channel, HPA antibodies and cell lines, ChEMBL, HPA blood cells / HPO / MP immune triggers |
| `variant_effects`, `allelic_spectrum`, `phenome` | series variants harmonised to the alt allele (GWAS effect allele and CI, gnomAD frequencies by ancestry), GWAS hits by EFO group with Open Targets L2G / colocalisation support |
| `variant_outcomes` | effect sizes parsed verbatim from the grounded papers' abstracts (`stated_effects`), tallied by outcome; forest plot from three papers |
| `burden` | Genebass rare-variant burden in the UK Biobank exomes (pLoF, missense and LC masks) at the paper's thresholds and QC; CC BY 4.0, undocumented browser API, not for non-coding genes |
| `crispr_screens` | BioGRID ORCS screens that scored the gene (about 1,400 for a typical coding gene), by phenotype, hits first, each hit its authors' own call; MIT, needs a free `BIOGRID_ORCS_ACCESS_KEY`. The connector keeps the key out of errors and logs |
| `somatic` | TCGA PanCancer Atlas via cBioPortal (open-access studies only), 20/20 pattern with a binomial recurrence test |
| `field_map`, `groups`, `methods`, `public_data` | indexing records of the grounded papers (MeSH branches, last authors, E05 techniques), GEO series matching the gene and perturbation words, GWAS summary statistics |
| `open_questions` | the writer's questions (`open_questions.json`) with the evidence, public data and active work keys |

The hard rules (cite every fact, anchor every number, absent ≠ not checked, separate
interpretation from fact, scope commercial claims to the target) are listed in
[SKILL.md](../.claude/skills/gene-profile/SKILL.md#hard-rules). Several of them mirror the
pipeline's lens guards ([lenses.md § Output guards](lenses.md#output-guards)).

---

## Layout

```
.claude-plugin/marketplace.json           # marketplace: lists the gene-profile plugin
.claude/skills/gene-profile/
├── .claude-plugin/plugin.json            # plugin manifest (version bumped by release-please)
├── SKILL.md                              # the skill; also the plugin root's SKILL.md
├── scripts/
│   ├── run                               # launcher: every script is called through it
│   ├── gather.py  register_source.py  check_profile.py  render.py
│   ├── profile_schema.py                 # run-directory contracts (manifest, sources, raw records)
│   ├── profile_literature.py             # PubTator3 literature map, keyword precision
│   ├── profile_tables.py                 # allelic series, variant spectrum, their markdown
│   ├── profile_figures.py                # protein map, expression, phenome, spectrum, forest,
│   │                                     #   field map, mechanism figures (matplotlib)
│   ├── profile_biology.py                # orthology reading, immune trigger, models/partners/reagents
│   ├── profile_variants.py               # variant harmonisation, variant effects, phenome points
│   ├── profile_effects.py                # effect sizes stated in abstracts, outcome tally
│   ├── profile_strength.py               # evidence strength per paper (design, species, n, group)
│   ├── profile_somatic.py                # TCGA somatic pattern (20/20 rule)
│   ├── profile_field.py                  # field map, groups, methods, public data
│   ├── profile_writer.py                 # how_we_know / mechanism / open_questions files
│   ├── profile_claims.py                 # claim checks and the sentence audit
│   ├── profile_drugs.py                  # one drug list with a fixed stage rule
│   ├── profile_topics.py                 # what the abstracts say, by topic, for the digest
│   ├── claim_search.py                   # targeted search for each core claim
│   ├── claim_audit.py                    # lists blocks for the audit
│   ├── scientific_review.py              # prepares the report for its reader
│   ├── profile_gates.py                  # five quality gates and the release status
│   ├── profile_retrieval.py              # evidence domains × dimensions searched
│   └── profile_blocks.py                 # {{table:…}} / {{figure:…}} blocks: check and expand
├── references/                           # section template, citation rules, gnomAD bands, terms
└── evals/                                # evals.json, score.py, baseline.json
```

The skill folder is the plugin root, so the same files serve both the in-repo `/gene-profile`
skill and the installed plugin. Everything the skill needs must live inside that folder: an
installed plugin has no access to the rest of the repository (this is why the gnomAD band
wording lives in `references/gnomad_bands.md` rather than pointing at
[skills/genetics_lens.md](../skills/genetics_lens.md)).

**The `run` launcher.** SKILL.md calls every script as
`${CLAUDE_SKILL_DIR}/scripts/run <script> [args]`, so no command depends on the working
directory. The launcher picks one of two environments:

- inside a checkout of this repository: `uv run --project <repo>`, i.e. the repo's own locked
  environment and your local connector code;
- anywhere else: `uv run --no-project --with "agentic-target-evidence @ git+…@vX.Y.Z"`, i.e. the
  release pinned in the script.

`gather.py` and `register_source.py` load `.env` from the working directory, not from the
plugin's install location.

---

## Releases

The plugin version and the pinned connector release move in lockstep with the package.
[release-please-config.json](../release-please-config.json) lists two extra files that each
release bumps:

- `.claude/skills/gene-profile/scripts/run` (the `x-release-please-version` line with the
  `@vX.Y.Z` git ref);
- `.claude/skills/gene-profile/.claude-plugin/plugin.json` (`$.version`).

Merging the release-please PR therefore ships a plugin that runs exactly the connector code of
that tag. Users pick it up with `claude plugin update gene-profile@agentic-target-evidence`.
There is no separate publish step: the marketplace is this GitHub repository. Before changing
either manifest, check it with `claude plugin validate .claude/skills/gene-profile` (and
`claude plugin validate .` for the marketplace).

Connector fixes reach installed plugins only after a release. Inside the repository the
launcher always uses the working tree. A source that needs a module the pinned release lacks
(the PubTator connector and grounding, variant effects and matplotlib arrived after 0.1.4; the cBioPortal, Genebass and BioGRID ORCS connectors, gnomAD variant frequencies, GWAS effect alleles, the PubMed design table and MeSH tree labels after that) is
logged as `error` and its figure or table is left out; the profile still renders.

---

## Evals and tests

- [evals/evals.json](../.claude/skills/gene-profile/evals/evals.json) holds 16 cases, each aimed
  at one failure mode or one gene class (one gene per class; replace one at each release). Score finished runs with
  `scripts/run ../evals/score.py <case_id>=<run_dir> [...]` and compare against
  [evals/baseline.json](../.claude/skills/gene-profile/evals/baseline.json) before changing the
  scripts or SKILL.md. Keep a change only if the score does not drop.
- [tests/gene_profile/](../tests/gene_profile/) covers the checker, anchors, renderer,
  `gather.py`, the literature map, the variant tables, the blocks and the figures offline (HTTP
  mocked with respx, PubTator documents built in the tests); it runs with `make test`.
