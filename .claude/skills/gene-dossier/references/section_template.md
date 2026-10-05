# Dossier layout

The report is ordered **most important first**. The Snapshot answers "what is this gene and
should I care?" on one screen. Each later section opens with its own bottom line, so a reader
can stop at any depth.

`render.py` generates the title, the identity header, the references and the appendix.
**`draft.md` starts at `## Snapshot`.** The headings below are required, word for word
(`check_dossier.py` matches them). Numbering such as `## 3 Human genetics` is allowed.

## Writing rules for every section

1. **Bottom line** first: 1–2 sentences, cited.
2. **Key data table** next, when the data are tabular. Give it a final `Refs` column and keep it to the top 5–10 rows. Say "top N of M" and anchor M.
3. **Notable findings** as cited bullets, one fact per bullet.
4. **Caveats:** conflicts, keyword-search noise, data limits.
5. **Optional `*Interpretation:*` block.** This is the only place for judgement.

**Direction of effect** runs through the whole report. Genetics establishes GoF vs LoF (or
"unclear"). Druggability then names the matching modality: inhibitor/antagonist for GoF,
activator/replacement for LoF. Safety reasons about what *that* modality would do. If the
sections disagree, record it in *Evidence gaps & conflicts*.

**Length:**
- Snapshot ≤ 1 screen (about 35 lines).
- Each section ≤ about 25 lines plus one table.
- Long lists are cut to top-N with "… and N more [@key]".

---

## Snapshot

One-sentence description: what the protein is, where it acts, and its best-known disease
link. Cite it.

**At a glance** (fixed rows; Tier 1–2 sources only):

| Domain | Signal | Strength | Refs |
|---|---|---|---|
| Genetics | e.g. Mendelian FSGS (AD, GoF); GenCC Strong | ●●● | [@gencc; @orphanet_associations] |
| Biology | … | ●●○ | … |
| Druggability | … | ●●○ | … |
| Clinical | … | ●●○ | … |
| Safety | flags or "no curated liabilities" | ⚠ / ✓ | … |
| Commercial | patents · clinical assets · indication crowding | text | … |
| Literature | {{pubmed_counts:total}} papers, {{pubmed_counts:since_YYYY}} since YYYY | text | [@pubmed_counts] |

**Strength scale.** ●●● strong · ●●○ moderate · ●○○ weak · ○○○ searched, nothing found ·
— not checked. Safety uses ⚠ (liability found) or ✓ (no flag found in the sources checked).

| Domain | ●●● | ●●○ | ●○○ |
|---|---|---|---|
| Genetics | ClinGen/GenCC Definitive or Strong; or GWAS + coloc H4 ≥ 0.8 for this gene | Moderate curation; ≥ 3 P/LP ClinVar variants (≥ 1★); L2G ≥ 0.5 | Limited curation, association-only |
| Biology | Reviewed function + experimental GO + pathway + consistent KO phenotype | Function known, partial context | Mostly predicted or uncharacterised |
| Druggability | Approved drug, or clinical-precedence tractability | Potent ChEMBL chemistry (pChEMBL ≥ 7) or structure-enabled pocket | Predicted tractable only |
| Clinical | Approved drug on target | Phase 2–3 asset | Phase 1 / preclinical only |

**Key findings:** 5–7 bullets, each cited, covering genetics, mechanism, modality, clinical
status, the top safety flag, and the competitive picture.

**Open questions:** 3–5 bullets ending in `?`. Questions need no citation.

## Identity & nomenclature

Table with fields: symbol, full name, aliases / previous symbols, locus, locus type, gene
groups, HGNC / Ensembl / Entrez / UniProt / MGI / OMIM IDs, protein length.
Sources: `hgnc`, `uniprot_profile`, `uniprot_extra`.

## Biology

Subsections (`###`):
- **Function & pathways:** `uniprot_profile.function`, `uniprot_extra.go`, `reactome`, `string`.
- **Protein & structure:** families, domains, `uniprot_extra.pdb_count` / best resolution, `alphafold` mean pLDDT.
- **Expression:** tissue (`gtex`, `hpa`), single-cell (`hpa` "RNA single cell type specific nCPM"), localisation, disease differential expression (`expression_atlas`).
- **Essentiality & models:** `depmap`, `project_score`, mouse KO (`ot_mouse`, `impc`).

## Human genetics

- **Constraint table:** LOEUF, pLI, mis_z, missense OEUF, observed/expected pLoF, with gnomAD bands. Use the band wording from `skills/genetics_lens.md`.
- **Mendelian disease:** `gencc`, `clingen`, `orphanet_associations`, `omim`, `uniprot_extra.diseases`, `hpo` (inheritance).
- **Variants:** representative ClinVar P/LP variants with stars and consequence (`gnomad_clinvar`). Note when the "pathogenic" bucket includes *conflicting* classifications. Note the pLoF count and homozygotes (`gnomad_lof`).
- **Common variation:** `gwas`, `ot_coloc` (H4, tissue, trait), `ot_l2g` (indication runs).
- **Mechanism / direction of effect:** GoF, LoF or unclear, with the evidence (Orphanet association type, variant consequence pattern, functional papers).

## Disease associations

Table of the top 10 Open Targets associations (`ot_associations.top`) with score and the
leading datatype. Add the total count. Add corroborating `spoke_diseases` edges and note
where sources agree or disagree. Separate Mendelian, GWAS-only and literature-only links.

## Druggability & modalities

- Tractability buckets (`ot_tractability`).
- Chemistry depth (`chembl`: bioactivities, potent compounds, median pChEMBL, max phase).
- Curated ligands (`dgidb_interactions`, separating inhibitors from activators).
- Druggable-genome classes (`dgidb_categories`).
- Structural enablement (PDB, AlphaFold).
- End by naming the modality that matches the direction of effect.

## Clinical landscape & pipeline

- Table of assets: drug, mechanism, highest phase, status, sponsor, indication, refs. Sources: `ot_known_drugs`, `ct_gene`, plus `nct:` / `pmid:` for results.
- Separate on-target interventional trials from observational or biomarker studies, and drop trials that only mention the gene.
- If `ot_known_drugs` is empty but trials exist, say so explicitly.

## Safety considerations

- Curated liabilities (`ot_safety`).
- KO phenotypes (`ot_mouse`, `impc`), flagging cardiovascular, CNS, reproductive and lethality phenotypes.
- Human LoF tolerance (`gnomad_constraint`, `gnomad_lof` homozygotes) as a proxy for chronic inhibition.
- Expression breadth outside the target tissue (`gtex`, `hpa`).
- Essentiality (`depmap`).
- On-target adverse events of approved drugs (`faers.by_drug`: the up-to-3 approved drugs with the most FAERS reports), and class effects from labels. Drugs in `faers.no_faers_reports` are approved somewhere but have no US FAERS reports — say so, don't call them US-approved.
- Finish with `*Interpretation:*`: the expected on-target risks of the matching modality.

## Commercial & IP

- **Patents:** `uspto.target_count` patents name the gene in the title (target IP); `uspto.indication_only_count` name only the disease (other mechanisms — indication IP, disease runs only). Top assignees from `uspto.patents`. If `uspto.capped` is true the connector stopped at 100 records: write "≥".
- **Competitive assets** on the target (from Clinical).
- **Indication crowding** (`openfda_indication`, `ct_condition`), only in indication runs.
- **Market sizing signals:** `orphanet_prevalence`, `gbd`. Say "not sizeable from Orphanet/GBD", never "unknown market".
- **Optional Tier-3 deal/pipeline facts** (◇).

## Regulatory

- FDA labels that mention the gene or its drugs (`openfda_labels`). Discard keyword false-positives.
- Boxed warnings, REMS.
- Orphan designation or breakthrough status, only if a Tier 1–2 source states it.

## Key literature

- Volume and trend (`pubmed_counts`).
- **Landmark papers** (3–5): the discovery or first-disease papers, chosen from `pubmed_top` / `pubmed_disease` or registered by PMID.
- **Recent reviews** (3–5): from `pubmed_reviews`.
- **Last 24 months** (3–5 notable): from `pubmed_recent`, each with a one-line takeaway.

## Indication fit

*(Required only when `--disease` was given.)*

- Open Targets gene × disease score and datatypes (`ot_association_disease`).
- L2G (`ot_l2g`).
- Disease-specific literature (`pubmed_disease`) and trials (`ct_gene_disease`).
- Disease burden (`gbd`, `orphanet_prevalence`).
- Whether the direction of effect fits the disease mechanism.

## Evidence gaps & conflicts

- Every source with status `skipped` or `error`, and why.
- Contradictions between sources, or between sections.
- Weak-evidence areas a reader might over-read.
- This is the only section exempt from the citation requirement, but cite when you state a fact.
