# Profile layout (template 3)

The reader is a PhD student starting a project on the gene, often from an adjacent field,
or a cell or molecular biologist, an immunologist, a human geneticist. They want to
**understand the gene**: what it does and how we know, where it is expressed, what its
variants do, what is settled and what is open, what they can work with, who works on it and
what to read first. The translational sections come last, under one heading, shorter.

The Snapshot answers "what is this gene, and how solid is that?" on one screen. Each
later section opens with its own bottom line, so a reader can stop at any depth.

`render.py` generates the title, the identity header, the legend, the references, the terms
and the appendix. **`draft.md` starts at `## Snapshot` and ends with *Evidence gaps &
conflicts*** — a References or Appendix section in the draft is dropped at render. The `##`
headings below are required, word for word (`check_profile.py` matches them). Numbering
such as `## 3 Human genetics` is allowed. A run keeps the template it was gathered for
(`manifest.template`): template 2 has these headings without the claim checks, the audit or
`drug_landscape`; runs without the field keep the earlier layout.

## Writing rules for every section

1. **Bottom line** first: 1–2 sentences, cited.
2. **Key data table** next, when the data are tabular. Give it a final `Refs` column and keep it to the top 5–10 rows. Say "top N of M" and anchor M.
3. **Notable findings** as cited bullets, one fact per bullet. Say how each was learned when it matters: in patients, in carriers, in a knockout mouse, in HEK-293 cells, in a crystal structure.
4. **Caveats:** conflicts, keyword-search noise, data limits.
5. **Optional `*Interpretation:*` block.** This is the only place for judgement.
6. **Keep the source's verb.** For what a molecule does, use the verb the cited source uses. Never substitute a word with a different biochemical meaning, such as "cut" for "lowered" (`W_VERB_NOT_IN_SOURCE`).
7. **Observation first, inference apart.** A fact bullet says what was seen and in what system. What follows from it goes in `*Interpretation:*`, unless the paper draws that conclusion itself (`W_INFERENCE_IN_FACT`).
8. **Match the claim to the experiment.** Overexpression, added protein or a transgene shows what a molecule can do, not what it normally does or where it comes from.
9. **Records are leads for mechanism.** Use a curated record's sentence on mechanism only with a paper you read (`W_RECORD_ONLY_MECHANISM`). Numbers and identifiers can rest on the record.

**Programmatic blocks.** A line holding only `{{table:…}}` or `{{figure:…}}` is replaced
by a table or figure the scripts build from `raw/` (rows and captions carry their own
citations; each caption ends "From records: …" or, for the writer's files, "Chosen by the
LLM from: …"). A block whose records hold nothing renders its empty state, never nothing.
Never retype their rows; write the prose around them, and let the prose agree with them.
Required blocks (the checker fails without them): `how_we_know` in *Snapshot*;
`models`, `partners`, `immune`, `mechanism`, `crispr_screens` in *Biology*; `allelic_series` (when the run
has one), `variant_effects`, `allelic_spectrum`, `variant_outcomes`, `phenome`, `burden`,
`somatic` in *Human genetics*; `reagents`, `methods`, `public_data` in *Research toolkit*;
`field_map`, `groups` in *View of the field*; `open_questions` in *Established, contested &
open*; `drug_landscape` in *Translational context*; `indication` in *Indication fit* (disease
runs).

**The writer's files** (beside `draft.md`; validated by the checker):

| File | Content |
|---|---|
| `how_we_know.json` | 3–5 core claims: `{"claim": "...", "supports": ["pmid:…", "<record key>"], "contradicts": ["pmid:…"], "not_comparable": [{"key": "pmid:…", "why": "<what differs>"}]}`. `contradicts` is for a source testing the same question; see *Contested*. The renderer counts each cell from the cited papers' records (design, species, groups, n). |
| `mechanism.json` | 4–8 steps: `{"actor", "action", "target", "compartment", "cell_type", "keys": [...]}`; the compartment is a UniProt subcellular location (`raw/uniprot_locations.json`); every step cited. When papers report more than one route, give each its own `"route": "<name>"` (up to 10 steps) and cite each separately; do not merge them into one chain. Steps without a route are shared: routes leave the step before them and rejoin at the next shared step. |
| `claim_checks.json` | One entry per How-we-know claim, and per mechanism or regulation claim that rests on a curated record: `{"claim": "...", "terms": ["<what the claim names>", "<a word for what would change it, or alternatives with OR>"], "outcome": "holds\|qualified\|conflicts\|not_found", "read": ["pmid:…"], "note": "..."}`. See SKILL.md. |
| `claim_audit.json` | One verdict per block citing a paper: see [claim_audit.md](claim_audit.md). |
| `open_questions.json` | `{"question", "evidence": [keys that leave it open], "would_settle": "...", "data": [keys of public data that could test it], "active": [keys of trials or recent papers on it]}`. |
| `variant_function.json` | functional studies of series variants (see SKILL.md). |

The *Snapshot* and *Established, contested & open* prose must not contradict these files.
Do not call a claim established, shown or proven when the How-we-know table marks it
"one group or cells only" (`W_STRENGTH_PROSE`).

**Direction of effect** runs through the report. *Human genetics* establishes what the
disease alleles do to the protein — gain, loss, both (an allelic series) or unclear — from
the allelic series rows and the variant-effects table (directions per allele).
*Translational context* then names the matching modality. If sections disagree, record it
in *Established, contested & open*.

**Literature.** Every statement built from papers uses the gene's grounded papers
(gene2pubmed or a PubTator tag of the gene or an ortholog); keyword counts are volume only
and stay in the appendix. Never write "N papers … X% name the gene" in the prose.

**Length:**
- Snapshot ≤ 1 screen (about 45 lines).
- Biology, Human genetics: ≤ about 40 lines each plus their tables and figures.
- Other research sections ≤ about 25 lines; translational subsections ≤ about 12 lines each.
- Long lists are cut to top-N with "… and N more [@key]".

---

## Snapshot

**In brief:** one paragraph, 3–5 sentences, from molecule to disease: what the protein is
and does, in which cells and compartment, what it controls physiologically, and what goes
wrong when it is altered. Every sentence cited.

**How we know:** the line `{{table:how_we_know}}`, from `how_we_know.json`. A claim with
support in one column only is phrased as an association in the prose.

**What is solid, what is weak, where the open problems are:** three cited bullets, one each.

**At a glance** (fixed rows; Tier 1–2 sources only):

| Domain | Signal | Strength | Refs |
|---|---|---|---|
| Genetics | e.g. Mendelian FSGS (AD, GoF); GenCC Strong | ●●● | [@gencc; @orphanet_associations] |
| Biology | … | ●●○ | … |
| Models | e.g. knockout mice viable; podocyte-specific gain-of-function model | ●○○ | … |
| Druggability | … | ●●○ | … |
| Clinical | … | ●○○ | … |
| Safety | flags, or "no curated flag" with the sources checked | ⚠ / ○ | … |
| Literature | the subfields of the field map, most active first | text | [@field_papers] |

**Strength scale.** ●●● strong · ●●○ moderate · ●○○ weak · ○○○ searched, nothing found ·
— not checked. Safety uses ⚠ (liability found) or ○ (no flag found in the sources checked —
an absence, never a clean bill: name the sources in the Signal cell).

| Domain | ●●● | ●●○ | ●○○ |
|---|---|---|---|
| Genetics | ClinGen/GenCC Definitive or Strong; or GWAS + coloc H4 ≥ 0.8 for this gene | Moderate curation; ≥ 3 P/LP ClinVar variants (≥ 1★); L2G ≥ 0.5 | Limited curation, association-only |
| Biology | Reviewed function + experimental GO + pathway + consistent KO phenotype | Function known, partial context | Mostly predicted or uncharacterised |
| Models | Model phenotype that matches the human disease, in more than one model | A model phenotype, no human counterpart yet | No model, or no phenotype |
| Druggability | Approved drug, or clinical-precedence tractability | Potent ChEMBL chemistry (pChEMBL ≥ 7) or structure-enabled pocket | Predicted tractable only |
| Clinical | Approved drug on target | Phase 2–3 asset | Phase 1 / preclinical only |

## Identity & nomenclature

Table with fields: symbol, full name, aliases / previous symbols, locus, locus type, gene
groups, HGNC / Ensembl / Entrez / UniProt / MGI / OMIM IDs, protein length.
Sources: `hgnc`, `uniprot_profile`, `uniprot_extra`.

## Biology

Subsections (`###`):
- **Function & pathways:** the RefSeq summary (`ncbi_gene.summary`), the most-cited GeneRIFs (`ncbi_gene.generifs` — cite their `pmid:` after reading the abstract), `uniprot_profile.function`, `uniprot_extra.go`, `reactome`. State the molecular activity, then the cellular process, then the physiology. Then `{{table:partners}}` (STRING by channel): write "functionally associated with", and say "binds" only with the paper or structure that shows it (`W_STRING_BINDING`).
- **Regulation:** what induces or represses the gene and what modifies the protein: `uniprot_extra.regulation` (induction, activity regulation, PTM), `uniprot_extra.modified_residues`, feedback, and the abstracts you read; the digest's topic section lists leads. Cover what the sources hold: transcriptional regulators (the site, the system, the effect of disrupting it), processing, and what each modification changes. Rank actors only from a paper that compares them; "X and Y" in a record does not make them equivalent. Report a modified form's activity as measured, and if assays disagree, give both with their systems. If no paper addresses one of these, say so. Then `{{figure:mechanism}}` (from `mechanism.json`).
- **Protein & structure:** `{{figure:protein_map}}` (variants coloured by molecular effect, somatic track when present), then the features that matter (`uniprot_extra.features`), key residues from mutagenesis (`uniprot_extra.mutagenesis`), structures and AlphaFold confidence.
- **Expression & localisation:** `{{figure:expression}}`, `{{table:tissue_evidence}}` and `{{table:immune}}` (shown in full when the data trigger it: HPA immune-cell enrichment, HPO immune phenotypes, mouse immune-system phenotypes; one line otherwise). Below them, cited bullets for the two kinds no record holds: functional experiments placing the gene's action in a tissue and human phenotypes tied to a tissue. Abundance alone never supports a function claim. Keep three questions apart: where the protein is made, where it acts, and which pool acts if there is more than one (local or circulating, intracellular or secreted). Answer each with its own evidence, or write "not established in the sources read".
- **Models & fidelity:** `{{table:models}}` — the mouse ortholog (NCBI curated; Ensembl type and identity), knockout phenotypes by MP class only when a curated ortholog exists, and beside them what human loss of function shows. Say whether the mouse models the human, citing both sides.
- **Essentiality:** `{{table:essentiality}}`. Write "not a broadly essential cancer-cell fitness gene in <screen>", never "not a cancer dependency" (`W_ESSENTIALITY_SCOPE`). Then `{{table:crispr_screens}}` (BioGRID ORCS: published screens beyond fitness, by phenotype; each hit is its own authors' call, so never sum hits into an effect, and read the paper before saying what a hit means. Few hits neither support nor argue against a role, so draw no conclusion from the count (`W_SCREEN_INFERENCE`). Pooled screens read cell-autonomous phenotypes, so a function outside the cell may not appear).

## Human genetics

- **Constraint:** `{{table:constraint}}`. In prose use the band wording from [gnomad_bands.md](gnomad_bands.md).
- **Allelic series:** `{{table:allelic_series}}` then `{{table:variant_spectrum}}`. The series ends with a tally of what every disease variant does to the target; prose and Snapshot must not contradict it. Below them, 3–5 cited bullets: which variants were characterised functionally and what they did (UniProt notes, `[paper]` entries from `variant_function.json`, with readout, system and condition), where disease missense variants cluster, whether truncating variants are reported as pathogenic, for which condition and by how many submitters.
- **Variant effects:** `{{table:variant_effects}}` (alleles, molecular effect, GWAS associations harmonised to the alt allele, gnomAD frequency by ancestry, homozygotes), `{{figure:allelic_spectrum}}` and `{{figure:variant_outcomes}}` (the effect sizes the abstracts state, tallied by outcome). Quote effect sizes from these blocks, never by hand (`W_BARE_NUMBER`).
- **Mendelian disease:** `gencc`, `clingen`, `orphanet_associations`, `omim`, `uniprot_extra.diseases`, `hpo`, `hpo_terms`.
- **Common variation:** `{{figure:phenome}}` (every GWAS association by trait group; hollow points are mapped by position only — say so before attributing them to the gene), `ot_coloc`, `ot_l2g` (indication runs).
- **Human knockouts:** `{{table:burden}}` (Genebass: phenotypes of rare pLoF and missense carriers in the UK Biobank exomes, at the published thresholds), then gnomAD homozygous pLoF carriers (`gnomad_lof`, `variant_frequencies`) and papers on homozygous or compound-heterozygous carriers, read before citing. Burden carriers are almost all heterozygous: it does not answer what complete loss does. No association at a low CAF is a lack of power, never tolerance; absence of homozygotes at this allele count is uninformative.
- **Somatic alterations:** `{{table:somatic}}` (TCGA PanCancer Atlas pattern by the 20/20 rule). A high mutated share alone is not a driver pattern; the label is not a curated driver call.
- **Mechanism / direction of effect:** gain, loss, both, or unclear — stated from the series rows, the variant-effects directions and the spectrum. Rows that disagree are reported, not averaged.

## Disease associations

Table of the top 10 Open Targets associations (`ot_associations.top`) with score and the
leading datatype. Add the total count as "with any evidence (score > 0)" — it includes
literature co-mention and is not a count of causal links (`W_ASSOCIATION_COUNT`). Add corroborating `spoke_diseases` edges and the
diseases the gene's literature relates it to (`pubtator_map.diseases`, with paper counts).
Separate Mendelian, GWAS-only and literature-only links, and note where sources disagree.

## Indication fit

*(Required only when `--disease` was given; a gene-only run leaves it out.)*

- Bottom line, then `{{table:indication}}` (required): Open Targets gene × disease score and datatypes, L2G, approved drugs and trials in the disease, the gene's trials and papers in it, prevalence and burden.
- Bullets on what the table means: is the indication already served (approved drugs, active trials — then it is contested), is the gene's support genetic or literature-only, which of the gene's trials target it.
- Whether the direction of effect fits the disease mechanism, as `*Interpretation:*`.
- Never describe a disease-scoped run as unscoped (`E_SCOPE_CONTRADICTION`).

## Research toolkit

What a researcher can use, each item cited:
- `{{table:reagents}}`: antibodies (HPA, with reliability), cell lines expressing the gene, mouse alleles, tool compounds.
- **Tool compounds:** activators and inhibitors — the chemicals the literature relates to the gene (`pubtator_map.chemicals`; read the abstract before calling a compound an agonist or inhibitor), potent ChEMBL chemistry (`chembl`), curated ligands (`dgidb_interactions`).
- **Structures:** `{{table:structures}}`, then what the structures showed, citing the structure papers.
- **Methods the field uses:** `{{table:methods}}` (MeSH Investigative Techniques of the grounded papers), then the assay behind each key finding of the Snapshot, cited.
- **Public data:** `{{table:public_data}}` (GEO series matching the query — a keyword match, say so; GWAS summary statistics; single-cell data).

## View of the field

- `{{figure:field_map}}`: which subfields are established, rising, quiet — say it from the trend column.
- `{{table:groups}}`: who works on it now (last authors, names as recorded). Cite their papers; never add affiliations the records do not give (`W_UNGROUNDED_NAME`).
- **Reading path:** the key papers in reading order — discovery, mechanism, human genetics, models, 2–3 recent reviews (`pubmed_reviews`), 3–5 notable papers of the last 24 months (`pubmed_recent`) — each with one line on why to read it, after reading its abstract.

## Established, contested & open

- **Established** (2–4 bullets): claims supported by at least two independent kinds of evidence (the How-we-know table), each citing both.
- **Contested** (0–4 bullets): claims with evidence on both sides, citing both sides — a model phenotype not seen in carriers, ClinVar classifications at odds with functional studies, conflicting GWAS and QTL directions. Findings conflict only if they test the same question: comparable exposure, duration, population and outcome. Otherwise write "not directly comparable" and name the difference, such as lifelong genetics vs a short trial, mouse vs human, or cells vs in vivo (`not_comparable` in `how_we_know.json`, `W_CROSS_KIND_CONTRADICTION`). Give the interval for a neutral result; it does not exclude an effect within that interval.
- **Open questions:** `{{table:open_questions}}` (from `open_questions.json`): each question with the evidence that leaves it open, what would settle it, the public data that could test it, and who is working on it. Prose bullets ending in `?` cite their evidence like any claim.

## Translational context

Four `###` subsections, in this order:

### Druggability & modalities

- Tractability buckets (`ot_tractability`), chemistry depth (`chembl`), curated ligands (`dgidb_interactions`), druggable-genome classes (`dgidb_categories`), structural enablement.
- End by naming the modality that matches the direction of effect.

### Clinical landscape & pipeline

- `{{table:drug_landscape}}` lists every drug with its stage, by a fixed rule: a regulator's record outranks an aggregator's (Drugs@FDA, then Open Targets), and a label Open Targets lacks still gets a row. Never type a stage: quote the table, and let the prose agree with it.
- Then a table of what each approved or late-stage drug showed: `| Drug | Indication | Biomarker effect | Outcome trial | Long-term safety | Refs |`. Every approved drug gets a row, or the top rows with "… and N more" (`W_DRUG_NO_READOUT`). Efficacy on a biomarker, outcome evidence and long-term safety are different claims; never one verdict ("proven") for all modalities (`W_PROVEN`). Empty cells say `none found` or `not checked`, never a placeholder. Sources: `ct_gene`, `ct_drugs`, `openfda_labels`, plus `nct:` / `pmid:` for results.
- Separate on-target interventional trials from observational or biomarker studies, and drop trials that only mention the gene.
- If the drug list is empty, say so in one line. If it is empty but trials exist, say that explicitly.

### Safety considerations

Bottom line first, then a table with one row per kind of evidence (keep the rows; a row
with no source says "not checked" or "none found [@key]"):

| Evidence | Signal | What it speaks to | What it does not | Refs |
|---|---|---|---|---|
| Human genetic loss of function | carriers, homozygotes, Mendelian randomisation (`gnomad_lof`, `gnomad_constraint`, papers) | lifelong, usually partial, loss in people | complete loss; a drug's exposure, dose or duration; survivor and ascertainment bias | |
| Animal knockouts | `ot_mouse`, `impc` (flag cardiovascular, CNS, reproductive, lethality) | the gene's roles in a model organism | human consequences: species biology and compensation differ | |
| Pharmacology, per modality | trials and pooled analyses (`pubmed_drug_safety`), label warnings (`openfda_labels`) | what each drug did, at its dose and duration, in the people trialled | other modalities; longer exposure; populations not trialled | |
| Pharmacovigilance | `{{table:faers}}` when present, otherwise FAERS counts with their caveats | signals for follow-up | incidence, causality, or a ranking of drugs | |
| Modality-specific | irreversibility (editing), delivery, tissue reach | the planned modality | other modalities | |

- Curated liabilities (`ot_safety`): `empty` means Open Targets lists none under its curation; write "no curated liability in Open Targets [@ot_safety]", never "no safety liabilities" or "safe".
- Keep the kinds apart: a genetic association with lifelong lower activity and the outcome of randomized trials of a drug are different evidence. When both exist, report both and say in *Established, contested & open* how they relate (see *Contested*); a genetic signal is "a genetic safety signal", not an established adverse effect of every modality.
- Expression outside the target tissue and essentiality (`depmap`) raise questions; they are not adverse effects.
- Drugs in `faers.no_faers_reports` returned no reports under this query. Say only that; a drug can be missing because of how it is named. Never give a reason without a source, and never read zero reports as safety.
- Finish with `*Interpretation:*`: the expected on-target risks of the matching modality, stating which rows they rest on.

### Commercial, IP & regulatory

- **Patents:** `uspto.target_count` patents name the gene in the title; `uspto.indication_only_count` name only the disease (disease runs). Top assignees. If `uspto.capped` is true write "≥".
- **Competitive assets** and **indication crowding** (`openfda_indication`, `ct_condition`, disease runs).
- **Regulatory:** FDA labels that mention the gene or its drugs (`openfda_labels`; discard keyword false-positives), boxed warnings, REMS; orphan or breakthrough status only from a Tier 1–2 source.
- **Market sizing signals:** `orphanet_prevalence`, `gbd`. Say "not sizeable from Orphanet/GBD", never "unknown market".

## Evidence gaps & conflicts

- Every source with status `skipped` or `error`, and why.
- Contradictions between sources, or between sections, not already in *Established, contested & open*.
- Weak-evidence areas a reader might over-read (keyword-search counts, single-submitter ClinVar calls, text-mining relation tags).
- This is the only section exempt from the citation requirement, but cite when you state a fact.
