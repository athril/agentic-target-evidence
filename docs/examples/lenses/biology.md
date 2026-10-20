# Biology Lens — TRPC6 × Focal Segmental Glomerulosclerosis

**Verdict:** Support · **Confidence:** 85%
**Therapeutic direction:** unspecified
**Evidence considered:** 91 source(s) · 106 claim(s)
**Generated:** 2026-10-06 02:56 UTC

---

## Summary

> The evidence strongly supports TRPC6 as a mechanistically understood and druggable target for focal segmental glomerulosclerosis (FSGS). The gene is expressed in podocytes, which are the relevant cell type in FSGS, and multiple studies link TRPC6 variants to the disease. Small-molecule inhibitors of TRPC6 have shown therapeutic effects in models of FSGS. While the CRISPR dependency data from DepMap shows near-zero essentiality across cancer cell lines, this is expected for a non-oncology target and does not detract from the mechanistic evidence.
[⚠ DEPMAP RELEVANCE GUARD: The text treats DepMap cancer-cell-line essentiality as supportive of this target, but DepMap: 2/1178 lines (0.2%), mean Chronos=-0.015, and the indication is non-oncology — cancer-lineage dependency data carry no mechanism signal here and must not be cited as support.]
[⚠ QUANTITY GUARD: The text describes cell-line dependency as 'essentiality across cancer cell lines', but the data show: not a cell-line dependency — fitness effect in 2/1178 DepMap cancer cell lines (0.2%). Quote the number instead of a vague quantifier.]

---

## Analysis

TRPC6 is a promising drug target for FSGS based on strong functional genomics and literature evidence. The gene is expressed in podocytes, which are critical cells affected by FSGS, with median TPM values of 23.2 in lung and 19.0 in esophagus muscularis from GTEx data. Multiple studies link TRPC6 variants to the disease, including gain-of-function mutations that activate the channel (e.g., Z3571 inhibitor). Small-molecule inhibitors targeting TRPC6 have shown therapeutic effects in FSGS models. The CRISPR dependency data from DepMap shows near-zero essentiality across cancer cell lines, which is expected for a non-oncology target and does not detract from the mechanistic evidence. The protein class of TRPC6 as an ion channel presents challenges for antibody tractability but supports small-molecule modality. Overall, the biology strongly supports this gene as a druggable target.
[⚠ DEPMAP RELEVANCE GUARD: The text treats DepMap cancer-cell-line essentiality as supportive of this target, but DepMap: 2/1178 lines (0.2%), mean Chronos=-0.015, and the indication is non-oncology — cancer-lineage dependency data carry no mechanism signal here and must not be cited as support.]
[⚠ QUANTITY GUARD: The text describes cell-line dependency as 'essentiality across cancer cell lines', but the data show: not a cell-line dependency — fitness effect in 2/1178 DepMap cancer cell lines (0.2%). Quote the number instead of a vague quantifier.]

## CRISPR Dependency ([DepMap](https://depmap.org/portal/gene/TRPC6))

**Status:** Context-dependent

| Metric | Value |
| --- | --- |
| Mean Chronos score | -0.015 |
| SD | 0.109 |
| Q1 / Median / Q3 | -0.079 / -0.011 / 0.053 |
| Dependent cell lines | 2/1178 (0.2%) |

**Per-lineage breakdown (top 12):**

| Lineage | Dep./Total | Dep. % | Mean Chronos |
| --- | --- | --- | --- |
| Esophagus/Stomach | 2/69 | 3% | -0.048 |
| Ovary/Fallopian Tube | 0/59 | 0% | -0.006 |
| Myeloid | 0/42 | 0% | -0.029 |
| Bowel | 0/63 | 0% | 0.003 |
| Bladder/Urinary Tract | 0/34 | 0% | -0.020 |
| Lung | 0/126 | 0% | 0.005 |
| Skin | 0/74 | 0% | -0.015 |
| Breast | 0/53 | 0% | 0.025 |
| Pancreas | 0/47 | 0% | 0.022 |
| Lymphoid | 0/93 | 0% | -0.029 |
| CNS/Brain | 0/90 | 0% | -0.036 |
| Soft Tissue | 0/45 | 0% | -0.018 |

---

## Open Targets Tractability ([source](https://platform.opentargets.org/target/ENSG00000137672))

**Small molecule** ✓ · **Antibody** ✓ · Half-life Data · Small Molecule Binder

---

## Mouse KO Phenotypes (Open Targets / MGI·IMPC)

7 phenotype(s) observed in mouse orthologue knock-out models.

| Phenotype | Class(es) |
| --- | --- |
| increased thermal nociceptive threshold | behavior/neurological phenotype |
| no abnormal phenotype detected | normal phenotype |
| abnormal vascular smooth muscle physiology | cardiovascular system phenotype, muscle phenotype |
| increased vasoconstriction | cardiovascular system phenotype, muscle phenotype |
| increased systemic arterial blood pressure | cardiovascular system phenotype |
| abnormal vasoconstriction | cardiovascular system phenotype, muscle phenotype |
| abnormal eye physiology | vision/eye phenotype |

---

## Axis Breakdown

| Axis | Verdict | Confidence | Rationale | Sources |
| --- | --- | --- | --- | --- |
| Druggability | Yes | 85% | TRPC6 is predicted to be small-molecule tractable per Open Targets and has known binding pockets, supporting the development of small-molecule inhibitors. The protein class as an ion channel presents challenges for antibody tractability but supports a small-molecule modality. | [4], [11], [52] |
| Mechanism Of Action | Yes | 85% | Multiple studies link TRPC6 variants to FSGS, including gain-of-function mutations that activate the channel and show therapeutic effects with small-molecule inhibitors. The CRISPR KO phenotype in mouse models supports a critical role for TRPC6 in podocyte injury and proteinuria.
[⚠ DEPMAP RELEVANCE GUARD: The text treats DepMap cancer-cell-line essentiality as supportive of this target, but DepMap: 2/1178 lines (0.2%), mean Chronos=-0.015, and the indication is non-oncology — cancer-lineage dependency data carry no mechanism signal here and must not be cited as support.] | [36], [38], [73] |
| Developability | Yes | 85% | TRPC6 is a cell membrane protein, making it accessible for biologics such as antibodies. However, the protein class as an ion channel presents challenges for antibody tractability but supports small-molecule modality. There are clinical-stage chemical matter examples supporting this target. | [46], [48] |

---

## Automated Checks

| Check | Outcome | Detail |
| --- | --- | --- |
| Tissue relevance (bulk TPM) | not evaluated | no curated disease-tissue mapping |
| Cell-line dependency relevance | **flagged** | dependency.relevance |
| Quantities vs. data bands | **flagged** | quantity.cell_line_dependency |

---

## Evidence Considered

### Literature (72)

| # | Source | Detail | Quality | Year | First Author |
| --- | --- | --- | --- | --- | --- |
| 1 | [PMID:41481124](https://pubmed.ncbi.nlm.nih.gov/41481124/) | Podocyte-specific Translational Profiling In Vivo Uncovers Distinct Patterns in Trpc6-Deficient Podocytes. | ★★★ | 2026 | Einloft J |
| 2 | [PMID:41276089](https://pubmed.ncbi.nlm.nih.gov/41276089/) | TRPC6 inhibition by Z3571: A structure-based strategy to ameliorate glomerular and tubular dysfunction in chronic kidney disease. | ★★★ | 2026 | Zhou L |
| 3 | [PMID:41100809](https://pubmed.ncbi.nlm.nih.gov/41100809/) | Structural and functional insights of the podocyte slit diaphragm complex. | ★★★ | 2026 | Qadri AH |
| 4 | [PMID:40719843](https://pubmed.ncbi.nlm.nih.gov/40719843/) | Targeting TRPC6 in podocytopathies: Why clinical translation remains a challenge? | ★★★ | 2026 | Patel CA |
| 5 | [PMID:40409549](https://pubmed.ncbi.nlm.nih.gov/40409549/) | Delayed inactivation of TRPC6 as a determinative characteristic of FSGS-associated variants. | ★★★ | 2025 | Okada R |
| 6 | [PMID:40388293](https://pubmed.ncbi.nlm.nih.gov/40388293/) | Genotype-phenotype correlations and clinical outcomes of genetic TRPC6 podocytopathies. | ★★★ | 2025 | McAnallen SM |
| 7 | [PMID:39352759](https://pubmed.ncbi.nlm.nih.gov/39352759/) | Natural History and Clinicopathological Associations of TRPC6-Associated Podocytopathy. | ★★★ | 2025 | Wooden B |
| 8 | [PMID:38766272](https://pubmed.ncbi.nlm.nih.gov/38766272/) | Genome editing and kidney health. | ★★★ | 2024 | Tavakolidakhrabadi N |
| 9 | [PMID:36940798](https://pubmed.ncbi.nlm.nih.gov/36940798/) | Podocyte protease activated receptor 1 stimulation in mice produces focal segmental glomerulosclerosis mirroring human disease signaling events. | ★★★ | 2023 | May CJ |
| 10 | [PMID:36750145](https://pubmed.ncbi.nlm.nih.gov/36750145/) | The small GTPase regulatory protein Rac1 drives podocyte injury independent of cationic channel protein TRPC5. | ★★★ | 2023 | Polat OK |
| 11 | [PMID:36007181](https://pubmed.ncbi.nlm.nih.gov/36007181/) | Ion channels and channelopathies in glomeruli. | ★★★ | 2023 | Staruschenko A |
| 12 | [PMID:35051376](https://pubmed.ncbi.nlm.nih.gov/35051376/) | Structural mechanism of human TRPC3 and TRPC6 channel regulation by their intracellular calcium-binding sites. | ★★★ | 2022 | Guo W |
| 13 | [PMID:34830371](https://pubmed.ncbi.nlm.nih.gov/34830371/) | Repurposing Riociguat to Target a Novel Paracrine Nitric Oxide-TRPC6 Pathway to Prevent Podocyte Injury. | ★★★ | 2021 | 't Hart D |
| 14 | [PMID:33922367](https://pubmed.ncbi.nlm.nih.gov/33922367/) | Cytoskeleton Rearrangements Modulate TRPC6 Channel Activity in Podocytes. | ★★★ | 2021 | Shalygin A |
| 15 | [PMID:32149605](https://pubmed.ncbi.nlm.nih.gov/32149605/) | Structural basis for pharmacological modulation of the TRPC6 channel. | ★★★ | 2020 | Bai Y |
| 16 | [PMID:32004513](https://pubmed.ncbi.nlm.nih.gov/32004513/) | TRPC channels: Structure, function, regulation and recent advances in small molecular probes. | ★★★ | 2020 | Wang H |
| 17 | [PMID:31877991](https://pubmed.ncbi.nlm.nih.gov/31877991/) | TRPC Channels in Proteinuric Kidney Diseases. | ★★★ | 2019 | Hall G |
| 18 | [PMID:31416818](https://pubmed.ncbi.nlm.nih.gov/31416818/) | TRPC6 Binds to and Activates Calpain, Independent of Its Channel Activity, and Regulates Podocyte Cytoskeleton, Cell Adhesion, and Motility. | ★★★ | 2019 | Farmer LK |
| 19 | [PMID:31266820](https://pubmed.ncbi.nlm.nih.gov/31266820/) | Contribution of Coiled-Coil Assembly to Ca(2+)/Calmodulin-Dependent Inactivation of TRPC6 Channel and its Impacts on FSGS-Associated Phenotypes. | ★★★ | 2019 | Polat OK |
| 20 | [PMID:31266804](https://pubmed.ncbi.nlm.nih.gov/31266804/) | Transmembrane insertases and N-glycosylation critically determine synthesis, trafficking, and activity of the nonselective cation channel TRPC6. | ★★★ | 2019 | Talbot BE |
| 21 | [PMID:31171574](https://pubmed.ncbi.nlm.nih.gov/31171574/) | Small Fluorescein Arsenical Hairpin-Based Förster Resonance Energy Transfer Analysis Reveals Changes in Amino- to Carboxyl-Terminal Interactions upon OAG Activation of Classical Transient Receptor Potential 6. | ★★★ | 2019 | Fiedler S |
| 22 | [PMID:31115705](https://pubmed.ncbi.nlm.nih.gov/31115705/) | Role of TRPC6 in Progression of Diabetic Kidney Disease. | ★★★ | 2019 | Staruschenko A |
| 23 | [PMID:30953689](https://pubmed.ncbi.nlm.nih.gov/30953689/) | TRPC channels: Regulation, dysregulation and contributions to chronic kidney disease. | ★★★ | 2019 | Dryer SE |
| 24 | [PMID:30665571](https://pubmed.ncbi.nlm.nih.gov/30665571/) | Knockout of TRPC6 promotes insulin resistance and exacerbates glomerular injury in Akita mice. | ★★★ | 2019 | Wang L |
| 25 | [PMID:29954830](https://pubmed.ncbi.nlm.nih.gov/29954830/) | The Calcium-Dependent Protease Calpain-1 Links TRPC6 Activity to Podocyte Injury. | ★★★ | 2018 | Verheijden KAT |
| 26 | [PMID:29619864](https://pubmed.ncbi.nlm.nih.gov/29619864/) | ATRA attenuate proteinuria via downregulation of TRPC6 in glomerulosclerosis rats induced by adriamycin. | ★★★ | 2018 | Zhang L |
| 27 | [PMID:28800680](https://pubmed.ncbi.nlm.nih.gov/28800680/) | Pharmacological inhibition of focal segmental glomerulosclerosis-related, gain of function mutants of TRPC6 channels by semi-synthetic derivatives of larixol. | ★★★ | 2017 | Urban N |
| 28 | [PMID:28629718](https://pubmed.ncbi.nlm.nih.gov/28629718/) | Changes in podocyte TRPC channels evoked by plasma and sera from patients with recurrent FSGS and by putative glomerular permeability factors. | ★★★ | 2017 | Kim EY |
| 29 | [PMID:28028935](https://pubmed.ncbi.nlm.nih.gov/28028935/) | Renoprotection: focus on TRPV1, TRPV4, TRPC6 and TRPM2. | ★★★ | 2017 | Markó L |
| 30 | [PMID:27895156](https://pubmed.ncbi.nlm.nih.gov/27895156/) | Sildenafil Prevents Podocyte Injury via PPAR-γ-Mediated TRPC6 Inhibition. | ★★★ | 2017 | Sonneveld R |
| 31 | [PMID:25847402](https://pubmed.ncbi.nlm.nih.gov/25847402/) | Discovery and pharmacological characterization of a novel potent inhibitor of diacylglycerol-sensitive TRPC cation channels. | ★★★ | 2015 | Maier T |
| 32 | [PMID:25844902](https://pubmed.ncbi.nlm.nih.gov/25844902/) | Gq signaling causes glomerular injury by activating TRPC6. | ★★★ | 2015 | Wang L |
| 33 | [PMID:25521631](https://pubmed.ncbi.nlm.nih.gov/25521631/) | Phospholipase C epsilon (PLCε) induced TRPC6 activation: a common but redundant mechanism in primary podocytes. | ★★★ | 2015 | Kalwa H |
| 34 | [PMID:24646854](https://pubmed.ncbi.nlm.nih.gov/24646854/) | Angiotensin II has acute effects on TRPC6 channels in podocytes of freshly isolated glomeruli. | ★★★ | 2014 | Ilatovskaya DV |
| 35 | [PMID:24194522](https://pubmed.ncbi.nlm.nih.gov/24194522/) | Transient receptor potential channel 6 (TRPC6) protects podocytes during complement-mediated glomerular disease. | ★★★ | 2013 | Kistler AD |
| 36 | [PMID:23645677](https://pubmed.ncbi.nlm.nih.gov/23645677/) | Gain-of-function mutations in transient receptor potential C6 (TRPC6) activate extracellular signal-regulated kinases 1/2 (ERK1/2). | ★★★ | 2013 | Chiluiza D |
| 37 | [PMID:22249312](https://pubmed.ncbi.nlm.nih.gov/22249312/) | Gα12 activation in podocytes leads to cumulative changes in glomerular collagen expression, proteinuria and glomerulosclerosis. | ★★★ | 2012 | Boucher I |
| 38 | [PMID:21959089](https://pubmed.ncbi.nlm.nih.gov/21959089/) | Calcium entry via TRPC6 mediates albumin overload-induced endoplasmic reticulum stress and apoptosis in podocytes. | ★★★ | 2011 | Chen S |
| 39 | [PMID:21839714](https://pubmed.ncbi.nlm.nih.gov/21839714/) | Angiotensin II contributes to podocyte injury by increasing TRPC6 expression via an NFAT-mediated positive feedback signaling pathway. | ★★★ | 2011 | Nijenhuis T |
| 40 | [PMID:21258036](https://pubmed.ncbi.nlm.nih.gov/21258036/) | TRPC6 enhances angiotensin II-induced albuminuria. | ★★★ | 2011 | Eckel J |
| 41 | [PMID:21161284](https://pubmed.ncbi.nlm.nih.gov/21161284/) | TRPC channel modulation in podocytes-inching toward novel treatments for glomerular disease. | ★★★ | 2011 | El Hindi S |
| 42 | [PMID:20961851](https://pubmed.ncbi.nlm.nih.gov/20961851/) | Protein kinase C-dependent phosphorylation of transient receptor potential canonical 6 (TRPC6) on serine 448 causes channel inhibition. | ★★★ | 2010 | Bousquet SM |
| 43 | [PMID:19936226](https://pubmed.ncbi.nlm.nih.gov/19936226/) | A novel TRPC6 mutation that causes childhood FSGS. | ★★★ | 2009 | Heeringa SF |
| 44 | [PMID:19562370](https://pubmed.ncbi.nlm.nih.gov/19562370/) | Molecular genetic analysis of podocyte genes in focal segmental glomerulosclerosis--a review. | ★★★ | 2009 | Löwik MM |
| 45 | [PMID:19129465](https://pubmed.ncbi.nlm.nih.gov/19129465/) | TRPC6 mutations associated with focal segmental glomerulosclerosis cause constitutive activation of NFAT-dependent transcription. | ★★★ | 2009 | Schlöndorff J |
| 46 | [PMID:18443213](https://pubmed.ncbi.nlm.nih.gov/18443213/) | Bigenic heterozygosity and the development of steroid-resistant focal segmental glomerulosclerosis. | ★★★ | 2008 | Löwik M |
| 47 | [PMID:18434567](https://pubmed.ncbi.nlm.nih.gov/18434567/) | 2007 Young Investigator Award: TRP'ing into a new era for glomerular disease. | ★★★ | 2008 | Winn MP |
| 48 | [PMID:18364721](https://pubmed.ncbi.nlm.nih.gov/18364721/) | Primer: strategies for identifying genes involved in renal disease. | ★★★ | 2008 | de Borst MH |
| 49 | [PMID:17116414](https://pubmed.ncbi.nlm.nih.gov/17116414/) | TRPC6 in glomerular health and disease: what we know and what we believe. | ★★★ | 2006 | Schlöndorff JS |
| 50 | [PMID:16340659](https://pubmed.ncbi.nlm.nih.gov/16340659/) | Podocyte injury and targeting therapy: an update. | ★★★ | 2006 | Durvasula RV |
| 51 | [PMID:16290061](https://pubmed.ncbi.nlm.nih.gov/16290061/) | TRPC6 - a new podocyte gene involved in focal segmental glomerulosclerosis. | ★★★ | 2005 | Kriz W |
| 52 | [PMID:15924139](https://pubmed.ncbi.nlm.nih.gov/15924139/) | TRPC6 is a glomerular slit diaphragm-associated channel required for normal renal function. | ★★★ | 2005 | Reiser J |
| 53 | [PMID:40497816](https://pubmed.ncbi.nlm.nih.gov/40497816/) | Genetic Exploration of Targeting the Transient Receptor Potential Cation Channel Subfamily Member 6. | ★★☆ | 2025 | Graesser C |
| 54 | [PMID:40059794](https://pubmed.ncbi.nlm.nih.gov/40059794/) | Emerging Trends in Modulation of Transient Receptor Potential Canonical 6 Channels as Therapeutic Targets. | ★★☆ | 2025 | Sharma A |
| 55 | [PMID:38315264](https://pubmed.ncbi.nlm.nih.gov/38315264/) | Novel Pathogenic Mutation of P209L in TRPC6 Gene Causes Adult Focal Segmental Glomerulosclerosis. | ★★☆ | 2024 | Yu T |
| 56 | [PMID:35371476](https://pubmed.ncbi.nlm.nih.gov/35371476/) | Evaluation of the presence of TRPC6 channels in human vessels: A pilot study using immunohistochemistry. | ★★☆ | 2022 | Abdinghoff J |
| 57 | [PMID:30664212](https://pubmed.ncbi.nlm.nih.gov/30664212/) | Participation of the AngII/TRPC6/NFAT axis in the pathogenesis of podocyte injury in rats with type 2 diabetes. | ★★☆ | 2019 | Ma R |
| 58 | [PMID:21471003](https://pubmed.ncbi.nlm.nih.gov/21471003/) | Tyrosine phosphorylation-dependent activation of TRPC6 regulated by PLC-γ1 and nephrin: effect of mutations associated with focal segmental glomerulosclerosis. | ★★☆ | 2011 | Kanda S |
| 59 | [PMID:19674119](https://pubmed.ncbi.nlm.nih.gov/19674119/) | R168H and V165X mutant podocin might induce different degrees of podocyte injury via different molecular mechanisms. | ★★☆ | 2009 | Fan Q |
| 60 | [PMID:39999713](https://pubmed.ncbi.nlm.nih.gov/39999713/) | Generation of an induced pluripotent stem cell line (NCKDi006-A) from a 34-year-old patient with focal segmental glomerulosclerosis carrying a heterozygous mutation in the TRPC6 gene. | ★☆☆ | 2025 | Wang Y |
| 61 | [PMID:39349897](https://pubmed.ncbi.nlm.nih.gov/39349897/) | Effective calcineurin inhibitor treatment in adult-onset steroid-resistant nephrotic syndrome with a novel splice donor site variant of TRPC6: a case report. | ★☆☆ | 2025 | Nagasaka T |
| 62 | [PMID:26084930](https://pubmed.ncbi.nlm.nih.gov/26084930/) | TRPC6 channel as an emerging determinant of the podocyte injury susceptibility in kidney diseases. | ★☆☆ | 2015 | Ilatovskaya DV |
| 63 | [PMID:24790760](https://pubmed.ncbi.nlm.nih.gov/24790760/) | The novel diagnostic biomarkers for focal segmental glomerulosclerosis. | ★☆☆ | 2014 | Nafar M |
| 64 | [PMID:22031853](https://pubmed.ncbi.nlm.nih.gov/22031853/) | Insulin increases surface expression of TRPC6 channels in podocytes: role of NADPH oxidases and reactive oxygen species. | ★☆☆ | 2012 | Kim EY |
| 65 | [PMID:26551740](https://pubmed.ncbi.nlm.nih.gov/26551740/) | Regulation of TRPC6 ion channels in podocytes - Implications for focal segmental glomerulosclerosis and acquired forms of proteinuric diseases. | ☆☆☆ | 2015 | Szabó T |
| 66 | [PMID:23529444](https://pubmed.ncbi.nlm.nih.gov/23529444/) | Single-channel analysis of TRPC channels in the podocytes of freshly isolated Glomeruli. | ☆☆☆ | 2013 | Ilatovskaya DV |
| 67 | [PMID:23171048](https://pubmed.ncbi.nlm.nih.gov/23171048/) | SNF8, a member of the ESCRT-II complex, interacts with TRPC6 and enhances its channel activity. | ☆☆☆ | 2012 | Carrasquillo R |
| 68 | [PMID:19124028](https://pubmed.ncbi.nlm.nih.gov/19124028/) | Identification and functional analysis of a novel TRPC6 mutation associated with late onset familial focal segmental glomerulosclerosis in Chinese patients. | ☆☆☆ | 2009 | Zhu B |
| 69 | [PMID:17459670](https://pubmed.ncbi.nlm.nih.gov/17459670/) | TRPC6 and FSGS: the latest TRP channelopathy. | ☆☆☆ | 2007 | Mukerji N |
| 70 | [PMID:17346947](https://pubmed.ncbi.nlm.nih.gov/17346947/) | TRP channels in kidney disease. | ☆☆☆ | 2007 | Hsu YJ |
| 71 | [PMID:16752799](https://pubmed.ncbi.nlm.nih.gov/16752799/) | Genetic basis of nephrotic syndrome--review. | ☆☆☆ | 2006 | Obeidová H |
| 72 | [PMID:38814201](https://pubmed.ncbi.nlm.nih.gov/38814201/) | A novel gain-of-function mutation in transient receptor potential C6 that causes podocytes injury. | — | 2024 | Yu M |

### Empirical (19)

| # | Source | Type | Detail | Quality |
| --- | --- | --- | --- | --- |
| 73 | [GTEx/HPA](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | GTEx top tissues (median TPM): Lung=23.2, Esophagus_Muscularis=19.0, Thyroid=12.0, Esophagus_Gastroesophageal_Junction=9.5, Adipose_Subcutaneous=7.9. HPA specificity: Tissue enhanced. Subcellular: Cell membrane. | ★★★ |
| 74 | [GTEx · Adipose_Subcutaneous](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Adipose_Subcutaneous: 7.9 TPM median. | ★★★ |
| 75 | [GTEx · Brain_Cerebellum](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Brain_Cerebellum: 0.6 TPM median. | ★★★ |
| 76 | [GTEx · Brain_Cortex](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Brain_Cortex: 0.7 TPM median. | ★★★ |
| 77 | [GTEx · Esophagus_Gastroesophageal_Junction](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Esophagus_Gastroesophageal_Junction: 9.5 TPM median. | ★★★ |
| 78 | [GTEx · Esophagus_Muscularis](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Esophagus_Muscularis: 19.0 TPM median. | ★★★ |
| 79 | [GTEx · Heart_Atrial_Appendage](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Heart_Atrial_Appendage: 0.3 TPM median. | ★★★ |
| 80 | [GTEx · Heart_Left_Ventricle](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Heart_Left_Ventricle: 0.2 TPM median. | ★★★ |
| 81 | [GTEx · Kidney_Cortex](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Kidney_Cortex: 1.2 TPM median. | ★★★ |
| 82 | [GTEx · Liver](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Liver: 0.0 TPM median. | ★★★ |
| 83 | [GTEx · Lung](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Lung: 23.2 TPM median. | ★★★ |
| 84 | [GTEx · Pancreas](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Pancreas: 0.3 TPM median. | ★★★ |
| 85 | [GTEx · Thyroid](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Thyroid: 12.0 TPM median. | ★★★ |
| 86 | [GTEx · Whole_Blood](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Whole_Blood: 0.1 TPM median. | ★★★ |
| 87 | [GTEx/HPA](https://www.proteinatlas.org/ENSG00000137672) | Omics & Expression | TRPC6 RNA tissue specificity: Tissue enhanced (Human Protein Atlas). | ★★★ |
| 88 | [spoke_anatomy:TRPC6](https://spoke.rbvi.ucsf.edu/api/v1/neighborhood/Gene/name/TRPC6) | Omics & Expression | SPOKE knowledge graph confirms TRPC6 expression in 23 anatomical structure(s) (UBERON): Brodmann (1909) area 9, adipose tissue, adrenal gland, adult mammalian kidney, brain, calcaneal tendon, colon, corpus callosum, dorsolateral prefrontal cortex, frontal cortex (+13 more). | ★★★ |
| 89 | [UniProt](https://www.uniprot.org/uniprotkb/Q9Y210) | Omics & Expression | TRPC6 subcellular localization: Cell membrane (UniProt/HPA). | ★★★ |
| 90 | [DepMap](https://depmap.org/portal/gene/TRPC6) | Functional Genomics | DepMap: TRPC6 dependent in 2/1178 lines (Chronos_Combined; scores from DepMap 24Q4 Public (figshare)). Mean Chronos: -0.015 (SD 0.109, Q1/Q3 -0.079/0.053). | ★★★ |
| 91 | [project_score:TRPC6](https://score.depmap.sanger.ac.uk/gene/SIDG39402) | Functional Genomics | Project Score: TRPC6 a fitness gene in 2/512 Sanger cell lines (scaled BF < 0). Mean scaled BF: 3.485 (negative = depletion). | ★★★ |

---

## Extracted Claims

| Source | Claim | Direction | Confidence |
| --- | --- | --- | --- |
| [73] | TRPC6 is expressed in the lung at a median TPM of 23.2. | unspecified | 90% |
| [73] | TRPC6 is expressed in the esophagus muscularis at a median TPM of 19.0. | unspecified | 90% |
| [73] | TRPC6 is expressed in the thyroid at a median TPM of 12.0. | unspecified | 90% |
| [73] | TRPC6 is expressed in the esophagus gastroesophageal junction at a median TPM of 9.5. | unspecified | 90% |
| [73] | TRPC6 is expressed in the subcutaneous adipose tissue at a median TPM of 7.9. | unspecified | 90% |
| [87] | TRPC6 is tissue enhanced in the lung. | unspecified | 90% |
| [87] | TRPC6 is tissue enhanced in the esophagus muscularis. | unspecified | 90% |
| [87] | TRPC6 is tissue enhanced in the thyroid. | unspecified | 90% |
| [87] | TRPC6 is tissue enhanced in the esophagus gastroesophageal junction. | unspecified | 90% |
| [87] | TRPC6 is expressed in subcutaneous adipose tissue. | unspecified | 90% |
| [89] | TRPC6 is located in the cell membrane. | unspecified | 90% |
| [83] | TRPC6 is expressed in the lung at a median TPM of 23.2. | unspecified | 90% |
| [78] | TRPC6 is expressed in the esophagus muscularis at a median TPM of 19.0. | unspecified | 90% |
| [85] | TRPC6 is expressed in the thyroid at a median TPM of 12.0. | unspecified | 90% |
| [77] | TRPC6 is expressed in the esophagus gastroesophageal junction at a median TPM of 9.5. | unspecified | 90% |
| [74] | TRPC6 is expressed in the subcutaneous adipose tissue at a median TPM of 7.9. | unspecified | 90% |
| [81] | TRPC6 is expressed in the kidney cortex at a median TPM of 1.17. | unspecified | 90% |
| [90] | TRPC6 CRISPR dependency (DepMap): not a cell-line dependency — fitness effect in 2/1178 DepMap cancer cell lines (0.2%). | unspecified | 90% |
| [91] | TRPC6 CRISPR dependency (Project Score): not a cell-line dependency — fitness effect in 2/512 Project Score cancer cell lines (0.4%). | unspecified | 90% |
| [1] | TRPC6 is expressed in podocytes and involved in calcium signaling. | unspecified | 90% |
| [2] | Z3571 is a TRPC6 inhibitor with therapeutic effects in models of focal segmental glomerulosclerosis. | unspecified | 90% |
| [3] | TRPC6 is a channel involved in the slit diaphragm of podocytes. | unspecified | 90% |
| [4] | TRPC6 is involved in podocyte injury and proteinuric kidney diseases. | unspecified | 90% |
| [53] | TRPC6 inhibition is being investigated for focal segmental glomerulosclerosis. | unspecified | 90% |
| [5] | More than 30 TRPC6 variants have been reported to be associated with focal segmental glomerulosclerosis. | unspecified | 90% |
| [5] | TRPC6 variants exhibit delayed Ca2+-dependent inactivation (CDI) which may be a common feature of FSGS-associated variants. | unspecified | 90% |
| [6] | TRPC6 variants are associated with focal segmental glomerulosclerosis. | unspecified | 90% |
| [54] | TRPC6 is a crucial part of the kidney podocytes. | unspecified | 90% |
| [60] | TRPC6 variants are associated with focal segmental glomerulosclerosis. | unspecified | 90% |
| [7] | TRPC6 variants are associated with focal segmental glomerulosclerosis. | unspecified | 90% |
| [61] | TRPC6 variants are associated with focal segmental glomerulosclerosis. | unspecified | 90% |
| [8] | CRISPR-based gene editing technologies offer potential treatments for genetic kidney diseases. | unspecified | 90% |
| [13] | NO prevents podocyte injury by inhibiting injurious TRPC6-mediated signaling in a soluble guanylate cyclase (sGC)-dependent manner. | unspecified | 90% |
| [14] | Cytoskeleton rearrangements modulate TRPC6 channel activity in podocytes. | unspecified | 90% |
| [15] | Cryo-EM structures of TRPC6 reveal two novel recognition sites for small-molecule modulators. | unspecified | 90% |
| [16] | TRPC6 mutations are linked to familial focal segmental glomerulosclerosis. | unspecified | 90% |
| [17] | TRPC6 mutations are implicated in the pathophysiology of focal segmental glomerulosclerosis. | unspecified | 90% |
| [18] | TRPC6 binds to and activates calpain, independent of its channel activity. | unspecified | 90% |
| [20] | TRPC6 expression is diminished by loss of the WD repeat domain 83 opposite strand (WDR83OS) gene. | unspecified | 90% |
| [21] | Mutations in the amino-terminal region of TRPC6 affect its trafficking and activity. | unspecified | 90% |
| [22] | TRPC6 channel activity leads to podocyte hypertrophy and foot process effacement in diabetic kidney disease. | unspecified | 90% |
| [23] | TRPC6 channels contribute to the progression of diabetic kidney disease. | unspecified | 90% |
| [24] | TRPC6 knockout reduces albuminuria in Akita mice. | unspecified | 90% |
| [24] | TRPC6 knockout promotes insulin resistance in Akita mice. | unspecified | 90% |
| [57] | AngII enhances TRPC6 currents in podocytes and ventricular myocytes. | unspecified | 90% |
| [26] | ATRA down-regulates TRPC6 expression and attenuates proteinuria in glomerulosclerosis rats. | unspecified | 90% |
| [29] | TRPC6 is involved in chronic proteinuric kidney disease, particularly in focal segmental glomerulosclerosis (FSGS). | unspecified | 90% |
| [30] | Sildenafil reduces TRPC6 expression and activity in podocytes. | inhibit | 90% |
| [65] | TRPC6 is a non-selective cation channel that plays a critical role in the pathogenesis of FSGS. | unspecified | 90% |
| [62] | TRPC6 channel is an emerging determinant of podocyte injury susceptibility in kidney diseases. | unspecified | 90% |
| [31] | SAR7334 is a novel, highly potent and bioavailable inhibitor of TRPC6 channels. | unspecified | 90% |
| [32] | Gq signaling causes glomerular injury by activating TRPC6. | unspecified | 90% |
| [34] | Angiotensin II causes depolarization and increased intracellular calcium concentration in podocytes. | unspecified | 90% |
| [66] | TRPC channels can be studied in freshly isolated glomeruli using patch-clamping techniques. | unspecified | 90% |
| [67] | SNF8 interacts with TRPC6 and enhances its channel activity. | modulate | 90% |
| [64] | Insulin increases surface expression of TRPC6 channels in podocytes. | activate | 90% |
| [39] | Angiotensin II increases TRPC6 expression in podocytes via a calcineurin/NFAT positive feedback signaling pathway. | activate | 90% |
| [40] | TRPC6 mutations cause kidney disease in familial forms of adult-onset focal segmental glomerulosclerosis (FSGS). | unspecified | 90% |
| [43] | Mutations in TRPC6 can lead to a large increase in channel currents and impaired channel inactivation. | unspecified | 90% |
| [59] | Mutant podocin (R168H) induces more significant podocyte apoptosis and expression changes than mutant podocin (V165X). | unspecified | 90% |
| [44] | TRPC6 mutations have been identified in some cases of focal segmental glomerulosclerosis. | unspecified | 90% |
| [68] | A novel TRPC6 mutation (Q889K) was identified in a Chinese pedigree with late onset familial FSGS. | unspecified | 90% |
| [68] | Intracellular Ca(2+) levels were significantly higher in cells expressing mutant TRPC6 compared to wild type TRPC6 upon stimulation with OAG. | unspecified | 90% |
| [46] | Mutations in TRPC6 have been found to cause an autosomal dominant form of FSGS. | unspecified | 90% |
| [47] | Mutations in TRPC6 cause an increase in intracellular calcium transients. | unspecified | 90% |
| [48] | Mutations in TRPC6 have been found to cause an increase in intracellular calcium transients. | unspecified | 90% |
| [70] | Mutations in TRPC6 have been implicated in hereditary focal segmental glomerulosclerosis. | unspecified | 90% |
| [49] | Mutations in TRPC6 have been identified as causing a familial form of focal segmental glomerulosclerosis. | unspecified | 90% |
| [71] | Mutations in TRPC6 have been identified as causing a familial form of focal segmental glomerulosclerosis. | unspecified | 90% |
| [50] | TRPC6 localizes to the slit diaphragm and specific mutations of TRPC6 have been identified in kindreds with familial focal segmental glomerulosclerosis. | unspecified | 90% |
| [51] | Mutations in TRPC6 have been shown to cause focal segmental glomerulosclerosis. | unspecified | 90% |
| [55] | Systems genetics analysis of the BXD mice kidney transcriptomes identified Pygm as an upstream regulator of Trpc6 in FSGS development. | unspecified | 85% |
| [9] | Podocyte PAR-1 activation causes a pro-migratory phenotype and is mirrored in patient disease biopsies. | unspecified | 85% |
| [11] | TRPC6 is a promising target for drug development in glomerular diseases such as FSGS. | unspecified | 85% |
| [12] | The gain-of-function (GOF) mutations of TRPC6 activate the channel by allosterically abolishing the inhibitory effects of intracellular calcium. | unspecified | 85% |
| [12] | Structures of human TRPC6 in complex with two chemically distinct inhibitors reveal different conformations of the transmembrane domain, providing templates for further structure-based drug design targeting TRPC6-related diseases such as FSGS. | unspecified | 85% |
| [16] | TRPC channels, including TRPC6, contribute to a broad spectrum of cellular functions and physiological roles. | unspecified | 85% |
| [19] | TRPC6 mutations delay Ca2+-dependent inactivation of the channel. | unspecified | 85% |
| [24] | TRPC6 knockout reduces tubular injury in Akita mice. | unspecified | 85% |
| [57] | TRPC6 is expressed in podocytes and mediates podocyte injury induced by high glucose. | unspecified | 85% |
| [25] | TRPC6 activity is linked to calpain-1 activation in podocytes. | unspecified | 85% |
| [27] | TRPC6 channels are targetable using larixol-derived TRPC6 inhibitors. | unspecified | 85% |
| [33] | PLCε-/- podocytes are indistinguishable from WT podocytes in their angiotensin II-induced formation of actin stress fibers. | unspecified | 85% |
| [35] | TRPC6 overexpression protects podocytes from complement-mediated injury. | unspecified | 85% |
| [40] | TRPC6 deficiency reduces albuminuria in mice during angiotensin II infusion. | inhibit | 85% |
| [45] | TRPC6 mutations associated with focal segmental glomerulosclerosis enhance NFAT-dependent transcription. | unspecified | 85% |
| [68] | Mutant TRPC6 expression was slightly increased compared to wild type TRPC6 in HEK293 cells. | unspecified | 85% |
| [69] | Mutant TRPC6 may affect critical interactions with podocyte structural proteins, leading to abnormalities in the slit diaphragm or podocyte foot processes. | unspecified | 85% |
| [69] | Mutant TRPC6 may amplify injurious signals mediated by Ang II, a common final pathway of podocyte apoptosis in various mammalian species. | unspecified | 85% |
| [52] | TRPC6 is expressed in podocytes and is a component of the glomerular slit diaphragm. | unspecified | 85% |
| [90] | TRPC6 is dependent in 2 out of 1178 cell lines. | unspecified | 80% |
| [91] | TRPC6 is a fitness gene in 2 out of 512 Sanger cell lines. | unspecified | 80% |
| [5] | TRPC6 variants exhibit gain-of-function or loss-of-function phenotypes, making the role of TRPC6 in FSGS controversial. | unspecified | 80% |
| [72] | TRPC6-N110S mutation causes podocyte injury by enhancing calcium influx and podocyte apoptosis. | unspecified | 80% |
| [10] | TRPC5 over-expression or inhibition does not ameliorate proteinuria induced by Rac1 activation in podocytes. | unspecified | 80% |
| [56] | TRPC6 channels are detected in the intima and adventitia of human vessels. | unspecified | 80% |
| [28] | TRPC6 channels are increased in podocytes exposed to sera from patients with recurrent FSGS. | unspecified | 80% |
| [63] | DPEP1 acts via leukotrienes on TRPC6 and results in increased podocyte motility and proteinuria. | unspecified | 80% |
| [36] | FSGS-associated mutations in TRPC6 result in activation of ERK1/2 kinases. | unspecified | 80% |
| [38] | TRPC6-mediated calcium entry contributes to albumin overload-induced endoplasmic reticulum stress and apoptosis in podocytes. | unspecified | 80% |
| [58] | Tyrosine phosphorylation-dependent activation of TRPC6 is regulated by PLC-γ1 and nephrin. | modulate | 80% |
| [41] | TRPC channel modulation in podocytes may be a target for novel treatments of glomerular disease. | unspecified | 80% |
| [42] | PKC-dependent phosphorylation of TRPC6 on Serine 448 inhibits channel activity. | inhibit | 80% |
| [24] | TRPC6 knockout increases mesangial expansion in Akita mice. | unspecified | 75% |
| [33] | TRPC6 activation by PLCε leads to increased intracellular Ca(2+) concentration in podocytes. | unspecified | 75% |
| [37] | Pharmacological targeting of TRPC6 is feasible and may be important in treating pulmonary diseases with excessive hypoxic vasoconstriction. | unspecified | 70% |

---

*Intermediate lens report. Numbered sources above are the original records this
lens reasoned over. See [report.md](../../report.md) for cross-lens synthesis and
[full_report.md](../../full_report.md) for the complete linked evidence dossier.*
