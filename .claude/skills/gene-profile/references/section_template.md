# Profile layout

The reader is a biologist, geneticist or PhD student who wants to **understand the gene**:
what it does and how we know, where it is expressed, what its variants do, what is settled
and what is open, what they can work with, and what to read first. The translational
sections (druggability, clinical, safety, commercial) follow the biology, shorter.

The Snapshot answers "what is this gene, and how solid is that?" on one screen. Each
later section opens with its own bottom line, so a reader can stop at any depth.

`render.py` generates the title, the identity header, the references and the appendix.
**`draft.md` starts at `## Snapshot` and ends with *Evidence gaps & conflicts*** — a
References or Appendix section in the draft is dropped at render. The headings below are required, word for word
(`check_profile.py` matches them). Numbering such as `## 3 Human genetics` is allowed.

## Writing rules for every section

1. **Bottom line** first: 1–2 sentences, cited.
2. **Key data table** next, when the data are tabular. Give it a final `Refs` column and keep it to the top 5–10 rows. Say "top N of M" and anchor M.
3. **Notable findings** as cited bullets, one fact per bullet. Say how each was learned when it matters: in patients, in carriers, in a knockout mouse, in HEK-293 cells, in a crystal structure.
4. **Caveats:** conflicts, keyword-search noise, data limits.
5. **Optional `*Interpretation:*` block.** This is the only place for judgement.

**Programmatic blocks.** A line holding only `{{table:allelic_series}}`,
`{{table:variant_spectrum}}`, `{{table:constraint}}`, `{{table:structures}}`, `{{table:faers}}`, `{{table:indication}}`,
`{{figure:protein_map}}` or `{{figure:expression}}` is replaced by a table or figure the
scripts build from `raw/` (rows and captions carry their own citations). Never retype their
rows; write the prose around them, and let the prose agree with them.

**Direction of effect** runs through the report. *Human genetics* establishes what the
disease alleles do to the protein — gain, loss, both (an allelic series) or unclear — from
the allelic series rows. *Druggability* then names the matching modality. If sections
disagree, record it in *Established, contested & open*.

**Length:**
- Snapshot ≤ 1 screen (about 45 lines).
- Biology, Human genetics: ≤ about 35 lines each plus their tables and figures.
- Other research sections ≤ about 25 lines; translational sections ≤ about 12 lines.
- Long lists are cut to top-N with "… and N more [@key]".

---

## Snapshot

**In brief:** one paragraph, 3–5 sentences, from molecule to disease: what the protein is
and does, in which cells and compartment, what it controls physiologically, and what goes
wrong when it is altered. Every sentence cited.

**How we know** (≤ 5 core claims from the paragraph; a cell is `✓ [@key]`, `✗ [@key]` or `—`):

| Claim | Human genetics | Animal models | Cells & biochemistry | Pharmacology |
|---|---|---|---|---|
| e.g. FSGS variants raise channel activity | ✓ [@allelic_series] | — | ✓ [@pmid:15879175] | — |

A cell is `✓ [@key]` (the source supports the claim), `✗ [@key]` (the source argues
against it: a trial that did not reproduce a genetic association, a model without the
phenotype) or `—` (nothing of that kind). A claim ticked in one column only is phrased as
an association ("associated with … in carriers"), not as an effect. Column by the kind of
evidence the cited source is: *human genetics* = variant, constraint,
curation, GWAS and colocalisation records and studies of carriers; *animal models* =
`ot_mouse`, `impc` and papers whose organisms (shown in the digest) are model organisms;
*cells & biochemistry* = UniProt function and mutagenesis, structures, papers on cell lines
or purified protein; *pharmacology* = drugs, tool compounds and trials. A claim with ticks
in several columns is better established; say so in the prose only through the table.

**Key findings:** 5–7 bullets, each cited: function, the allelic series and direction,
the key model phenotype, expression context, the state of pharmacology, the biggest open
question's evidence.

**At a glance** (fixed rows; Tier 1–2 sources only):

| Domain | Signal | Strength | Refs |
|---|---|---|---|
| Genetics | e.g. Mendelian FSGS (AD, GoF); GenCC Strong | ●●● | [@gencc; @orphanet_associations] |
| Biology | … | ●●○ | … |
| Models | e.g. knockout mice viable; podocyte-specific gain-of-function model | ●○○ | … |
| Druggability | … | ●●○ | … |
| Clinical | … | ●○○ | … |
| Safety | flags, or "no curated flag" with the sources checked | ⚠ / ○ | … |
| Literature | {{pubtator_map:volume.total}} papers; mostly human / mouse | text | [@pubtator_map] |

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
- **Function & pathways:** the RefSeq summary (`ncbi_gene.summary`), the most-cited GeneRIFs (`ncbi_gene.generifs`, NLM-curated one-line findings — cite their `pmid:` after reading the abstract), `uniprot_profile.function`, `uniprot_extra.go`, `reactome`, `string`. State the molecular activity, then the cellular process, then the physiology.
- **Protein & structure:** `{{figure:protein_map}}`, then the features that matter (domains, repeats, transmembrane segments, topology, active sites; `uniprot_extra.features`), key residues from mutagenesis (`uniprot_extra.mutagenesis`), structures (`uniprot_extra.pdb_count`, best resolution) and AlphaFold confidence.
- **Expression & localisation:** `{{figure:expression}}`, then tissue (`gtex`, `hpa`), single-cell type (`hpa` "RNA single cell type specific nCPM"), subcellular location, disease differential expression (`expression_atlas`). Name the cell type that matters for the disease.
- **Essentiality & models:** `depmap`, `project_score`, mouse knockouts (`ot_mouse`, `impc`: viability, the phenotypes that echo human disease, allele types when given).

## Human genetics

- **Constraint:** the line `{{table:constraint}}` (LOEUF, pLI, observed/expected pLoF, mis_z, missense OEUF with their readings, the gnomAD release and the transcript). In prose use the band wording from [gnomad_bands.md](gnomad_bands.md): population depletion, never variant-level benignity or the safety of complete loss.
- **Allelic series:** the line `{{table:allelic_series}}` (required when the run has one), then `{{table:variant_spectrum}}`. The series ends with a tally of what every disease variant does to the target (gain, loss, truncating, mixed, not typed); prose and Snapshot must not contradict it — when gain and truncating/loss rows coexist, say so and weigh them by review stars and functional evidence. Below them, 3–5 cited bullets on what the rows show: which variants were characterised functionally and what they did (UniProt notes, with their PMIDs, and the `[paper]` entries from `variant_function.json`, with the readout, system and condition each was measured in), where disease missense variants cluster, whether truncating variants are reported as pathogenic, for which condition and by how many submitters (the ClinVar entries in the series name them; review stars), carriers and homozygotes of loss-of-function alleles in gnomAD.
- **Mendelian disease:** `gencc`, `clingen`, `orphanet_associations` (its association type says loss or gain of function when Orphanet typed it), `omim`, `uniprot_extra.diseases`, `hpo` (inheritance).
- **Common variation:** `gwas`, `ot_coloc` (H4, tissue, trait), `ot_l2g` (indication runs); the most-studied literature variants (`pubtator_map.variants`).
- **Mechanism / direction of effect:** gain, loss, both, or unclear — stated from the allelic series rows and the spectrum, citing them. Rows that disagree (some variants increase activity, others decrease it) are reported as such, not averaged. When no row says what a variant does, write that the records do not establish the direction.

## Disease associations

Table of the top 10 Open Targets associations (`ot_associations.top`) with score and the
leading datatype. Add the total count. Add corroborating `spoke_diseases` edges and the
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
- **Tool compounds:** activators and inhibitors — the chemicals the literature relates to the gene (`pubtator_map.chemicals`; the relation type is a tag, so read the abstract before calling a compound an agonist or inhibitor), potent ChEMBL chemistry (`chembl`), curated ligands (`dgidb_interactions`).
- **Model organisms:** the organisms the gene's papers study (`pubtator_map.organisms`, `gene_studied`), mouse lines and their phenotypes (`impc`, `ot_mouse`).
- **Cell models:** cell lines used in the gene's papers (`pubtator_map.cell_lines`), dependent lines and lineages (`depmap`).
- **Structures:** `{{table:structures}}` (PDB entries by resolution, the AlphaFold model), then what the structures showed, citing the structure papers.

## Established, contested & open

- **Established** (2–4 bullets): claims supported by at least two independent kinds of evidence (the How-we-know table), each citing both.
- **Contested** (0–4 bullets): claims with evidence on both sides, citing both sides — for example a model phenotype not seen in human carriers, ClinVar classifications at odds with functional studies, conflicting GWAS and QTL directions.
- **Open questions** (3–5 bullets ending in `?`): each cites the record or paper that leaves it open (a gap, a conflict, an uncharacterised domain, variants of uncertain significance, a GWAS signal without colocalisation). Questions need citations.

## Key literature

A reading path, 2–3 papers per step, each with a one-line takeaway after reading its
abstract:
- **Discovery:** the first description of the gene or its disease link (`pubmed_cited`, earliest GeneRIFs).
- **Mechanism:** the papers that established what the protein does.
- **Human genetics:** the variant and association papers.
- **Models:** the key animal or cell-model papers.
- **Reviews:** 2–3 recent reviews (`pubmed_reviews`).
- **Last 24 months:** 3–5 notable papers (`pubmed_recent`).

Volume and trend: `{{pubtator_map:volume.total}}` papers (`pubtator_map.volume.basis` says
whether that is the keyword count or the curated count) and `pubmed_counts.by_year`.
Papers PubTator shows are not about the gene are already left out of the digest lists.

## Druggability & modalities

- Tractability buckets (`ot_tractability`), chemistry depth (`chembl`), curated ligands (`dgidb_interactions`), druggable-genome classes (`dgidb_categories`), structural enablement.
- End by naming the modality that matches the direction of effect.

## Clinical landscape & pipeline

- Table of assets: drug, mechanism, highest phase, status, sponsor, indication, refs. Sources: `ot_known_drugs`, `ct_gene`, `ct_drugs`, plus `nct:` / `pmid:` for results.
- Separate on-target interventional trials from observational or biomarker studies, and drop trials that only mention the gene.
- If `ot_known_drugs` is empty but trials exist, say so explicitly.

## Safety considerations

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
- Keep the kinds apart: a genetic association with lifelong lower activity and the outcome of randomized trials of a drug are different evidence. When both exist and disagree, report both and put the conflict in *Established, contested & open*; a genetic signal is "a genetic safety signal", not an established adverse effect of every modality.
- Expression outside the target tissue and essentiality (`depmap`) raise questions; they are not adverse effects.
- Drugs in `faers.no_faers_reports` are approved somewhere but have no US FAERS reports: say so, don't call them US-approved, and never read zero reports as safety.
- Finish with `*Interpretation:*`: the expected on-target risks of the matching modality, stating which rows they rest on.

## Commercial, IP & regulatory

- **Patents:** `uspto.target_count` patents name the gene in the title; `uspto.indication_only_count` name only the disease (disease runs). Top assignees. If `uspto.capped` is true write "≥".
- **Competitive assets** and **indication crowding** (`openfda_indication`, `ct_condition`, disease runs).
- **Regulatory:** FDA labels that mention the gene or its drugs (`openfda_labels`; discard keyword false-positives), boxed warnings, REMS; orphan or breakthrough status only from a Tier 1–2 source.
- **Market sizing signals:** `orphanet_prevalence`, `gbd`. Say "not sizeable from Orphanet/GBD", never "unknown market".

## Evidence gaps & conflicts

- Every source with status `skipped` or `error`, and why.
- Contradictions between sources, or between sections, not already in *Established, contested & open*.
- Weak-evidence areas a reader might over-read (keyword-search counts, single-submitter ClinVar calls, text-mining relation tags).
- This is the only section exempt from the citation requirement, but cite when you state a fact.
