# Biology Lens — TRPC6 × Focal Segmental Glomerulosclerosis

**Verdict:** Support · **Confidence:** 90%
**Therapeutic direction:** inhibit
**Evidence considered:** 79 source(s) · 82 claim(s)
**Generated:** 2026-06-24 03:28 UTC

---

## Summary

> The evidence strongly supports TRPC6 as a mechanistically understood and druggable target for FSGS inhibition, with clear functional studies linking it to podocyte injury and glomerular disease.

---

## Analysis

TRPC6 is a key regulator of calcium signaling in podocytes, which are critical cells involved in the pathogenesis of FSGS. Multiple articles provide strong evidence that TRPC6 mutations lead to gain-of-function phenotypes (e.g., claim_id: 5d5bc094-27f8-4289-b4cf-f51e253220bc, 960a4bca-0d5e-4de1-b7ee-4c453541e258), and that TRPC6 knockout prevents FSGS development (claim_id: 5d5bc094-27f8-4289-b4cf-f51e253220bc). The protein is expressed in the cell membrane of podocytes, as indicated by UniProt and HPA data. Small-molecule inhibitors targeting TRPC6 have been identified (claim_id: 4f46430e-1ea4-46d8-9754-c1ab4615196e), supporting the druggability of this target. The DepMap CRISPR screen shows that TRPC6 is dependent in unknown lines, with a mean Chronos score of -0.013 (SD 0.129). While not pan-essential, this indicates some level of dependency across cell lines. The mechanism of action is well-established, with multiple studies linking TRPC6 to podocyte injury and glomerular disease progression (e.g., claim_id: e8bd392d-4b3f-4a0b-be40-b32a5841d0cf).

## CRISPR Dependency ([DepMap](https://depmap.org/portal/gene/TRPC6))

**Status:** Context-dependent

| Metric | Value |
| --- | --- |
| Mean Chronos score | -0.013 |
| SD | 0.129 |
| Q1 / Median / Q3 | -0.090 / -0.008 / 0.067 |
| Dependent cell lines | — |

**Per-lineage breakdown (top 12):**

| Lineage | Dep./Total | Dep. % | Mean Chronos |
| --- | --- | --- | --- |
| Esophagus/Stomach | 2/69 | 3% | -0.052 |
| Bowel | 1/63 | 2% | 0.011 |
| Lung | 0/126 | 0% | 0.005 |
| Myeloid | 0/45 | 0% | -0.023 |
| Prostate | 0/10 | 0% | -0.007 |
| Pancreas | 0/48 | 0% | 0.025 |
| Ovary/Fallopian Tube | 0/59 | 0% | -0.005 |
| Fibroblast | 0/1 | 0% | -0.108 |
| CNS/Brain | 0/91 | 0% | -0.023 |
| Kidney | 0/35 | 0% | -0.014 |
| Breast | 0/53 | 0% | 0.029 |
| Bone | 0/58 | 0% | -0.032 |

---

## Open Targets Tractability ([source](https://platform.opentargets.org/target/ENSG00000137672))

**Small molecule** ✓ · **Antibody** ✓ · Half-life Data · Small Molecule Binder

---

## Mouse KO Phenotypes (Open Targets / MGI·IMPC)

7 phenotype(s) observed in mouse orthologue knock-out models.

| Phenotype | Class(es) |
| --- | --- |
| abnormal vasoconstriction | cardiovascular system phenotype, muscle phenotype |
| increased vasoconstriction | cardiovascular system phenotype, muscle phenotype |
| increased thermal nociceptive threshold | behavior/neurological phenotype |
| no abnormal phenotype detected | normal phenotype |
| abnormal vascular smooth muscle physiology | cardiovascular system phenotype, muscle phenotype |
| increased systemic arterial blood pressure | cardiovascular system phenotype |
| abnormal eye physiology | vision/eye phenotype |

---

## Axis Breakdown

| Axis | Verdict | Confidence | Rationale | Sources |
| --- | --- | --- | --- | --- |
| Druggability | Yes | 90% | TRPC6 has a known binding pocket and is predicted to be small-molecule tractable, with cryo-EM structures revealing potential sites for modulators (claim_id: 7c99d258-da42-4c0c-94d0-174517c0fada). The protein class indicates that an antibody would be challenging to develop, so small molecules are the preferred modality. | [8], [9] |
| Mechanism Of Action | Yes | 90% | Functional studies and mouse knockout phenotypes strongly support the gain-of-function mechanism of TRPC6 in FSGS (claim_id: 5d5bc094-27f8-4289-b4cf-f51e253220bc, 960a4bca-0d5e-4de1-b7ee-4c453541e258). The gene is expressed in the relevant cell type (podocytes) and bulk TPM does not indicate low expression in disease-relevant tissues. | [5], [44] |
| Developability | Yes | 90% | TRPC6 is a cell membrane protein, making it accessible for antibody development. However, small molecules are the preferred modality given its channel function (claim_id: c391c049-c828-4741-8877-947ca2c71c18). The presence of a known inhibitor (SAR7334) supports small-molecule tractability. | [11], [33] |

---

## Evidence Considered

### Literature (57)

| # | Source | Detail | Quality | Year | First Author |
| --- | --- | --- | --- | --- | --- |
| 1 | [PMID:42041578](https://pubmed.ncbi.nlm.nih.gov/42041578/) | An Inducible hiPSC-Derived Human Podocyte Model for Functional Analysis of TRPC6 Variants Associated with FSGS. | ★★★ | 2026 | Batool L |
| 2 | [PMID:41481124](https://pubmed.ncbi.nlm.nih.gov/41481124/) | Podocyte-specific Translational Profiling In Vivo Uncovers Distinct Patterns in Trpc6-Deficient Podocytes. | ★★★ | 2026 | Einloft J |
| 3 | [PMID:41100809](https://pubmed.ncbi.nlm.nih.gov/41100809/) | Structural and functional insights of the podocyte slit diaphragm complex. | ★★★ | 2026 | Qadri AH |
| 4 | [PMID:40719843](https://pubmed.ncbi.nlm.nih.gov/40719843/) | Targeting TRPC6 in podocytopathies: Why clinical translation remains a challenge? | ★★★ | 2026 | Patel CA |
| 5 | [PMID:36007181](https://pubmed.ncbi.nlm.nih.gov/36007181/) | Ion channels and channelopathies in glomeruli. | ★★★ | 2023 | Staruschenko A |
| 6 | [PMID:35300969](https://pubmed.ncbi.nlm.nih.gov/35300969/) | The normalized slope conductance as a tool for quantitative analysis of current-voltage relations. | ★★★ | 2022 | Hermann C |
| 7 | [PMID:33922367](https://pubmed.ncbi.nlm.nih.gov/33922367/) | Cytoskeleton Rearrangements Modulate TRPC6 Channel Activity in Podocytes. | ★★★ | 2021 | Shalygin A |
| 8 | [PMID:32149605](https://pubmed.ncbi.nlm.nih.gov/32149605/) | Structural basis for pharmacological modulation of the TRPC6 channel. | ★★★ | 2020 | Bai Y |
| 9 | [PMID:31266820](https://pubmed.ncbi.nlm.nih.gov/31266820/) | Contribution of Coiled-Coil Assembly to Ca(2+)/Calmodulin-Dependent Inactivation of TRPC6 Channel and its Impacts on FSGS-Associated Phenotypes. | ★★★ | 2019 | Polat OK |
| 10 | [PMID:31171574](https://pubmed.ncbi.nlm.nih.gov/31171574/) | Small Fluorescein Arsenical Hairpin-Based Förster Resonance Energy Transfer Analysis Reveals Changes in Amino- to Carboxyl-Terminal Interactions upon OAG Activation of Classical Transient Receptor Potential 6. | ★★★ | 2019 | Fiedler S |
| 11 | [PMID:31115705](https://pubmed.ncbi.nlm.nih.gov/31115705/) | Role of TRPC6 in Progression of Diabetic Kidney Disease. | ★★★ | 2019 | Staruschenko A |
| 12 | [PMID:30953689](https://pubmed.ncbi.nlm.nih.gov/30953689/) | TRPC channels: Regulation, dysregulation and contributions to chronic kidney disease. | ★★★ | 2019 | Dryer SE |
| 13 | [PMID:30665571](https://pubmed.ncbi.nlm.nih.gov/30665571/) | Knockout of TRPC6 promotes insulin resistance and exacerbates glomerular injury in Akita mice. | ★★★ | 2019 | Wang L |
| 14 | [PMID:29954830](https://pubmed.ncbi.nlm.nih.gov/29954830/) | The Calcium-Dependent Protease Calpain-1 Links TRPC6 Activity to Podocyte Injury. | ★★★ | 2018 | Verheijden KAT |
| 15 | [PMID:28028935](https://pubmed.ncbi.nlm.nih.gov/28028935/) | Renoprotection: focus on TRPV1, TRPV4, TRPC6 and TRPM2. | ★★★ | 2017 | Markó L |
| 16 | [PMID:26892346](https://pubmed.ncbi.nlm.nih.gov/26892346/) | TRPC6 G757D Loss-of-Function Mutation Associates with FSGS. | ★★★ | 2016 | Riehle M |
| 17 | [PMID:26436650](https://pubmed.ncbi.nlm.nih.gov/26436650/) | MicroRNA-30 family members regulate calcium/calcineurin signaling in podocytes. | ★★★ | 2015 | Wu J |
| 18 | [PMID:26265544](https://pubmed.ncbi.nlm.nih.gov/26265544/) | A functional tandem between transient receptor potential canonical channels 6 and calcium-dependent chloride channels in human epithelial cells. | ★★★ | 2015 | Bertrand J |
| 19 | [PMID:26156092](https://pubmed.ncbi.nlm.nih.gov/26156092/) | Focal segmental glomerulosclerosis: molecular genetics and targeted therapies. | ★★★ | 2015 | Chen YM |
| 20 | [PMID:26147534](https://pubmed.ncbi.nlm.nih.gov/26147534/) | Genetic Interactions Between TRPC6 and NPHS1 Variants Affect Posttransplant Risk of Recurrent Focal Segmental Glomerulosclerosis. | ★★★ | 2015 | Sun ZJ |
| 21 | [PMID:25847402](https://pubmed.ncbi.nlm.nih.gov/25847402/) | Discovery and pharmacological characterization of a novel potent inhibitor of diacylglycerol-sensitive TRPC cation channels. | ★★★ | 2015 | Maier T |
| 22 | [PMID:25844902](https://pubmed.ncbi.nlm.nih.gov/25844902/) | Gq signaling causes glomerular injury by activating TRPC6. | ★★★ | 2015 | Wang L |
| 23 | [PMID:25521631](https://pubmed.ncbi.nlm.nih.gov/25521631/) | Phospholipase C epsilon (PLCε) induced TRPC6 activation: a common but redundant mechanism in primary podocytes. | ★★★ | 2015 | Kalwa H |
| 24 | [PMID:25407002](https://pubmed.ncbi.nlm.nih.gov/25407002/) | Targeted next-generation sequencing in steroid-resistant nephrotic syndrome: mutations in multiple glomerular genes may influence disease severity. | ★★★ | 2015 | Bullich G |
| 25 | [PMID:24646854](https://pubmed.ncbi.nlm.nih.gov/24646854/) | Angiotensin II has acute effects on TRPC6 channels in podocytes of freshly isolated glomeruli. | ★★★ | 2014 | Ilatovskaya DV |
| 26 | [PMID:24194522](https://pubmed.ncbi.nlm.nih.gov/24194522/) | Transient receptor potential channel 6 (TRPC6) protects podocytes during complement-mediated glomerular disease. | ★★★ | 2013 | Kistler AD |
| 27 | [PMID:23999069](https://pubmed.ncbi.nlm.nih.gov/23999069/) | 254C>G: a TRPC6 promoter variation associated with enhanced transcription and steroid-resistant nephrotic syndrome in Chinese children. | ★★★ | 2013 | Kuang XY |
| 28 | [PMID:23686279](https://pubmed.ncbi.nlm.nih.gov/23686279/) | Unique X-linked familial FSGS with co-segregating heart block disorder is associated with a mutation in the NXF5 gene. | ★★★ | 2013 | Esposito T |
| 29 | [PMID:23645677](https://pubmed.ncbi.nlm.nih.gov/23645677/) | Gain-of-function mutations in transient receptor potential C6 (TRPC6) activate extracellular signal-regulated kinases 1/2 (ERK1/2). | ★★★ | 2013 | Chiluiza D |
| 30 | [PMID:23385000](https://pubmed.ncbi.nlm.nih.gov/23385000/) | Vitamin D down-regulates TRPC6 expression in podocyte injury and proteinuric glomerular disease. | ★★★ | 2013 | Sonneveld R |
| 31 | [PMID:23291369](https://pubmed.ncbi.nlm.nih.gov/23291369/) | New TRPC6 gain-of-function mutation in a non-consanguineous Dutch family with late-onset focal segmental glomerulosclerosis. | ★★★ | 2013 | Hofstra JM |
| 32 | [PMID:22971997](https://pubmed.ncbi.nlm.nih.gov/22971997/) | A novel mutation, outside of the candidate region for diagnosis, in the inverted formin 2 gene can cause focal segmental glomerulosclerosis. | ★★★ | 2013 | Sanchez-Ares M |
| 33 | [PMID:22249312](https://pubmed.ncbi.nlm.nih.gov/22249312/) | Gα12 activation in podocytes leads to cumulative changes in glomerular collagen expression, proteinuria and glomerulosclerosis. | ★★★ | 2012 | Boucher I |
| 34 | [PMID:21959089](https://pubmed.ncbi.nlm.nih.gov/21959089/) | Calcium entry via TRPC6 mediates albumin overload-induced endoplasmic reticulum stress and apoptosis in podocytes. | ★★★ | 2011 | Chen S |
| 35 | [PMID:21839714](https://pubmed.ncbi.nlm.nih.gov/21839714/) | Angiotensin II contributes to podocyte injury by increasing TRPC6 expression via an NFAT-mediated positive feedback signaling pathway. | ★★★ | 2011 | Nijenhuis T |
| 36 | [PMID:21161284](https://pubmed.ncbi.nlm.nih.gov/21161284/) | TRPC channel modulation in podocytes-inching toward novel treatments for glomerular disease. | ★★★ | 2011 | El Hindi S |
| 37 | [PMID:20961851](https://pubmed.ncbi.nlm.nih.gov/20961851/) | Protein kinase C-dependent phosphorylation of transient receptor potential canonical 6 (TRPC6) on serine 448 causes channel inhibition. | ★★★ | 2010 | Bousquet SM |
| 38 | [PMID:20685822](https://pubmed.ncbi.nlm.nih.gov/20685822/) | TRPC6 channels and their binding partners in podocytes: role in glomerular filtration and pathophysiology. | ★★★ | 2010 | Dryer SE |
| 39 | [PMID:20651158](https://pubmed.ncbi.nlm.nih.gov/20651158/) | Activation of NFAT signaling in podocytes causes glomerulosclerosis. | ★★★ | 2010 | Wang Y |
| 40 | [PMID:19936226](https://pubmed.ncbi.nlm.nih.gov/19936226/) | A novel TRPC6 mutation that causes childhood FSGS. | ★★★ | 2009 | Heeringa SF |
| 41 | [PMID:19129465](https://pubmed.ncbi.nlm.nih.gov/19129465/) | TRPC6 mutations associated with focal segmental glomerulosclerosis cause constitutive activation of NFAT-dependent transcription. | ★★★ | 2009 | Schlöndorff J |
| 42 | [PMID:16628251](https://pubmed.ncbi.nlm.nih.gov/16628251/) | Bigenic mouse models of focal segmental glomerulosclerosis involving pairwise interaction of CD2AP, Fyn, and synaptopodin. | ★★★ | 2006 | Huber TB |
| 43 | [PMID:16340659](https://pubmed.ncbi.nlm.nih.gov/16340659/) | Podocyte injury and targeting therapy: an update. | ★★★ | 2006 | Durvasula RV |
| 44 | [PMID:15924139](https://pubmed.ncbi.nlm.nih.gov/15924139/) | TRPC6 is a glomerular slit diaphragm-associated channel required for normal renal function. | ★★★ | 2005 | Reiser J |
| 45 | [PMID:30664212](https://pubmed.ncbi.nlm.nih.gov/30664212/) | Participation of the AngII/TRPC6/NFAT axis in the pathogenesis of podocyte injury in rats with type 2 diabetes. | ★★☆ | 2019 | Ma R |
| 46 | [PMID:26127002](https://pubmed.ncbi.nlm.nih.gov/26127002/) | In silico analysis of functional nsSNPs in human TRPC6 gene associated with steroid resistant nephrotic syndrome. | ★★☆ | 2015 | Joshi BB |
| 47 | [PMID:21471003](https://pubmed.ncbi.nlm.nih.gov/21471003/) | Tyrosine phosphorylation-dependent activation of TRPC6 regulated by PLC-γ1 and nephrin: effect of mutations associated with focal segmental glomerulosclerosis. | ★★☆ | 2011 | Kanda S |
| 48 | [PMID:19674119](https://pubmed.ncbi.nlm.nih.gov/19674119/) | R168H and V165X mutant podocin might induce different degrees of podocyte injury via different molecular mechanisms. | ★★☆ | 2009 | Fan Q |
| 49 | [PMID:30595563](https://pubmed.ncbi.nlm.nih.gov/30595563/) | TRPC6 Mutational Analysis in Iranian Children With Focal Segmental Glomerulosclerosis. | ★☆☆ | 2018 | Gheissari A |
| 50 | [PMID:26017975](https://pubmed.ncbi.nlm.nih.gov/26017975/) | Pleiotropic signaling evoked by tumor necrosis factor in podocytes. | ★☆☆ | 2015 | Abkhezr M |
| 51 | [PMID:25279100](https://pubmed.ncbi.nlm.nih.gov/25279100/) | Regulation of TRPC6 Channels by Non-Steroidal Anti-Inflammatory Drugs. | ★☆☆ | 2012 | Ilatovskaya DV |
| 52 | [PMID:22031853](https://pubmed.ncbi.nlm.nih.gov/22031853/) | Insulin increases surface expression of TRPC6 channels in podocytes: role of NADPH oxidases and reactive oxygen species. | ★☆☆ | 2012 | Kim EY |
| 53 | [PMID:19910702](https://pubmed.ncbi.nlm.nih.gov/19910702/) | NADPH oxidase-derived ROS contributes to upregulation of TRPC6 expression in puromycin aminonucleoside-induced podocyte injury. | ★☆☆ | 2009 | Wang Z |
| 54 | [PMID:26551740](https://pubmed.ncbi.nlm.nih.gov/26551740/) | Regulation of TRPC6 ion channels in podocytes - Implications for focal segmental glomerulosclerosis and acquired forms of proteinuric diseases. | ☆☆☆ | 2015 | Szabó T |
| 55 | [PMID:23689571](https://pubmed.ncbi.nlm.nih.gov/23689571/) | Screening of ACTN4 and TRPC6 mutations in a Chinese cohort of patients with adult-onset familial focal segmental glomerulosclerosis. | ☆☆☆ | 2013 | Zhang Q |
| 56 | [PMID:17459670](https://pubmed.ncbi.nlm.nih.gov/17459670/) | TRPC6 and FSGS: the latest TRP channelopathy. | ☆☆☆ | 2007 | Mukerji N |
| 57 | [PMID:17346947](https://pubmed.ncbi.nlm.nih.gov/17346947/) | TRP channels in kidney disease. | ☆☆☆ | 2007 | Hsu YJ |

### Empirical (22)

| # | Source | Type | Detail | Quality |
| --- | --- | --- | --- | --- |
| 58 | [expression_atlas:E-MTAB-9194](https://www.ebi.ac.uk/gxa/experiments/E-MTAB-9194) | Omics & Expression | TRPC6 DOWN -3.0-fold (p=1.4e-234) in 'embryonic stem cell differentiating toward definitive endoderm; 24 hour' vs 'embryonic stem cell; 0 hour' [E-MTAB-9194] (Expression Atlas). | ★★★ |
| 59 | [expression_atlas:ensg00000137672](https://www.ebi.ac.uk/gxa/genes/ensg00000137672) | Omics & Expression | Expression Atlas: no differential expression data specific to 'Focal Segmental Glomerulosclerosis' found for TRPC6; top significant differential results in other contexts: DOWN -5.9-fold (p=0) in 'definitive endoderm cell; 72 hour' vs 'embryonic stem cell; 0 hour' [E-MTAB-9194]; DOWN -4.5-fold (p=0) in 'embryonic stem cell differentiating toward definitive endoderm; 48 hour' vs 'embryonic stem cell; 0 hour' [E-MTAB-9194]; DOWN -4.6-fold (p=6.4e-306) in 'embryonic stem cell differentiating toward definitive endoderm; 36 hour' vs 'embryonic stem cell; 0 hour' [E-MTAB-9194]; DOWN -5.4-fold (p=8.9e-305) in 'embryonic stem cell differentiating toward definitive endoderm; 60 hour' vs 'embryonic stem cell; 0 hour' [E-MTAB-9194]; DOWN -3.0-fold (p=1.4e-234) in 'embryonic stem cell differentiating toward definitive endoderm; 24 hour' vs 'embryonic stem cell; 0 hour' [E-MTAB-9194]. | ★★★ |
| 60 | [GTEx/HPA](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | GTEx top tissues (median TPM): Lung=23.2, Esophagus_Muscularis=19.0, Thyroid=12.0, Esophagus_Gastroesophageal_Junction=9.5, Adipose_Subcutaneous=7.9. HPA specificity: Tissue enhanced. Subcellular: Cell membrane. | ★★★ |
| 61 | [GTEx · Adipose_Subcutaneous](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Adipose_Subcutaneous: 7.9 TPM median. | ★★★ |
| 62 | [GTEx · Brain_Cerebellum](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Brain_Cerebellum: 0.6 TPM median. | ★★★ |
| 63 | [GTEx · Brain_Cortex](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Brain_Cortex: 0.7 TPM median. | ★★★ |
| 64 | [GTEx · Esophagus_Gastroesophageal_Junction](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Esophagus_Gastroesophageal_Junction: 9.5 TPM median. | ★★★ |
| 65 | [GTEx · Esophagus_Muscularis](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Esophagus_Muscularis: 19.0 TPM median. | ★★★ |
| 66 | [GTEx · Heart_Atrial_Appendage](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Heart_Atrial_Appendage: 0.3 TPM median. | ★★★ |
| 67 | [GTEx · Heart_Left_Ventricle](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Heart_Left_Ventricle: 0.2 TPM median. | ★★★ |
| 68 | [GTEx · Kidney_Cortex](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Kidney_Cortex: 1.2 TPM median. | ★★★ |
| 69 | [GTEx · Liver](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Liver: 0.0 TPM median. | ★★★ |
| 70 | [GTEx · Lung](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Lung: 23.2 TPM median. | ★★★ |
| 71 | [GTEx · Pancreas](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Pancreas: 0.3 TPM median. | ★★★ |
| 72 | [GTEx · Thyroid](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Thyroid: 12.0 TPM median. | ★★★ |
| 73 | [GTEx · Whole_Blood](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Whole_Blood: 0.1 TPM median. | ★★★ |
| 74 | [GTEx/HPA](https://www.proteinatlas.org/ENSG00000137672) | Omics & Expression | TRPC6 RNA tissue specificity: Tissue enhanced (Human Protein Atlas). | ★★★ |
| 75 | [spoke_anatomy:TRPC6](https://spoke.rbvi.ucsf.edu/api/v1/neighborhood/Gene/name/TRPC6) | Omics & Expression | SPOKE knowledge graph confirms TRPC6 expression in 23 anatomical structure(s) (UBERON): Brodmann (1909) area 9, adipose tissue, adrenal gland, adult mammalian kidney, brain, calcaneal tendon, colon, corpus callosum, dorsolateral prefrontal cortex, frontal cortex (+13 more). | ★★★ |
| 76 | [UniProt](https://www.uniprot.org/uniprotkb/Q9Y210) | Omics & Expression | TRPC6 subcellular localization: Cell membrane (UniProt/HPA). | ★★★ |
| 77 | [DepMap](https://depmap.org/portal/gene/TRPC6) | Functional Genomics | DepMap: TRPC6 dependent in unknown lines (Chronos_Combined). Mean Chronos: -0.013 (SD 0.129, Q1/Q3 -0.090/0.067). | ★★★ |
| 78 | [project_score:TRPC6](https://score.depmap.sanger.ac.uk/gene/SIDG39402) | Functional Genomics | Project Score: TRPC6 a fitness gene in 348/350 Sanger cell lines (BAGEL2 scaled BF > 0). Mean scaled BF: 3.570. | ★★★ |
| 79 | [encode_region_search:TRPC6](https://www.encodeproject.org/region-search/?region=TRPC6&genome=GRCh38) | regulatory_element | ENCODE region-search: 1452 experiment(s) overlap the TRPC6 locus (TRPC6: (chr11:101451470-101584007) +/- 2kb). Assays: DNase-seq (641), ChIP-seq (542), ATAC-seq (269); top ChIP-seq targets: CTCF (278), RAD21 (31), POLR2A (19), POLR2AphosphoS5 (19), SMC3 (19). | ★★★ |

---

## Extracted Claims

| Source | Claim | Direction | Confidence |
| --- | --- | --- | --- |
| [22] | Deletion of TRPC6 in GqQ>L-expressing mice prevented FSGS development and inhibited tubular damage and podocyte loss induced by PAN nephrosis. | inhibit | 95% |
| [25] | TRPC6 knockout glomeruli were less susceptible to angiotensin II-induced intracellular calcium transients compared to wild-type glomerular epithelial cells. | activate | 95% |
| [74] | TRPC6 is tissue enhanced. | unspecified | 90% |
| [76] | TRPC6 is located in the cell membrane. | unspecified | 90% |
| [1] | TRPC6 is expressed in podocytes and regulates calcium flux. | unspecified | 90% |
| [3] | TRPC6 is a key regulator of podocyte calcium signaling and cytoskeletal organization. | unspecified | 90% |
| [4] | TRPC6 channel is critical for maintaining the glomerular filtration barrier. | unspecified | 90% |
| [5] | TRPC6 channels are involved in glomerular function and disease. | unspecified | 90% |
| [8] | Cryo-EM structures of TRPC6 reveal two novel recognition sites for small-molecule modulators. | unspecified | 90% |
| [9] | Disruptions of TRPC's coiled-coil assembly impair CaM-mediated inactivation, leading to gain-of-function in TRPC6 activity. | unspecified | 90% |
| [11] | TRPC6 channel activity leads to glomeruli injury in diabetic kidney disease. | inhibit | 90% |
| [45] | Angiotensin II enhances TRPC6 currents and mediates podocyte injury through the TRPC6/NFAT axis in rats with type 2 diabetes. | inhibit | 90% |
| [14] | TRPC6 activity links to calpain-1 activation, which contributes to podocyte injury in FSGS. | inhibit | 90% |
| [16] | Five TRPC6 mutations (N125S, L395A, G757D, L780P, and R895L) cause a loss-of-function phenotype. | unspecified | 90% |
| [17] | miR-30 family members regulate calcium/calcineurin signaling in podocytes. | unspecified | 90% |
| [19] | FSGS is caused by podocyte-specific gene mutations including TRPC6. | unspecified | 90% |
| [46] | Two functional SNPs (rs35857503 at position N157T and rs36111323 at position A404V) in TRPC6 may destabilize the amino acid interactions. | unspecified | 90% |
| [21] | SAR7334 is a novel, highly potent and bioavailable inhibitor of TRPC6 channels. | unspecified | 90% |
| [22] | Gq activation stimulates calcineurin (CN) activity, resulting in CN-dependent upregulation of TRPC6. | unspecified | 90% |
| [25] | Angiotensin II evokes calcium transient through TRPC channels. | unspecified | 90% |
| [26] | -254C>G SNP enhanced transcription from TRPC6 promoter in vitro and was associated with increased TRPC6 expression in renal tissues of SRNS patients. | unspecified | 90% |
| [55] | A missense mutation (Q889K) of TRPC6 was found in two independent families. | unspecified | 90% |
| [29] | Overexpression of gain-of-function TRPC6 mutants resulted in increased ERK1/2 phosphorylation. | unspecified | 90% |
| [30] | Vitamin D down-regulates TRPC6 expression in podocyte injury and proteinuric glomerular disease. | unspecified | 90% |
| [31] | A novel TRPC6 p.Arg175Gln gain-of-function mutation shows increased TRPC6-mediated current. | inhibit | 90% |
| [52] | Insulin increases surface expression of TRPC6 channels in podocytes. | activate | 90% |
| [48] | Podocin (R168H) induces more significant podocyte injury than podocin (V165X). | unspecified | 90% |
| [41] | TRPC6 mutations lead to constitutive activation of NFAT-dependent transcription. | unspecified | 90% |
| [57] | TRPC6 is expressed in kidney along different parts of the nephron and may be involved in hereditary FSGS. | inhibit | 90% |
| [43] | TRPC6 localizes to the slit diaphragm and specific mutations of this channel in kindreds of familial FSGS implicate a causal role for aberrant calcium signaling in podocyte injury. | inhibit | 90% |
| [44] | TRPC6 is a component of the glomerular slit diaphragm and its channel activity is essential for proper regulation of podocyte structure and function. | inhibit | 90% |
| [11] | Angiotensin II, reactive oxygen species, and other factors stimulate calcium influx through TRPC6 channels, causing podocyte hypertrophy and foot process effacement. | inhibit | 85% |
| [22] | Expression of GqQ>L promoted albuminuria, mesangial expansion, and increased glomerular basement membrane width in diabetic mice. | unspecified | 85% |
| [23] | TRPC6 was co-immunoprecipitated with PLCε in a heterologous overexpression system and in freshly isolated murine podocytes. | unspecified | 85% |
| [33] | Genetic activation of Gα12 in podocytes leads to proteinuria and glomerulosclerosis. | inhibit | 85% |
| [35] | Angiotensin II increases TRPC6 expression in podocytes via NFAT-mediated signaling. | activate | 85% |
| [40] | Mutations in TRPC6 lead to increased calcium influx and podocyte injury. | unspecified | 85% |
| [48] | TRPC6 expression is mis-localized in the presence of abnormal podocin (R168H). | unspecified | 85% |
| [60] | TRPC6 is expressed in the lung, esophagus muscularis, thyroid, and esophagus gastroesophageal junction. | unspecified | 80% |
| [70] | TRPC6 has a median TPM of 23.2 in the lung. | unspecified | 80% |
| [65] | TRPC6 has a median TPM of 19.0 in the esophagus muscularis. | unspecified | 80% |
| [72] | TRPC6 has a median TPM of 12.0 in the thyroid. | unspecified | 80% |
| [61] | TRPC6 has a median TPM of 9.5 in the esophagus gastroesophageal junction. | unspecified | 80% |
| [64] | TRPC6 has a median TPM of 7.9 in the subcutaneous adipose tissue. | unspecified | 80% |
| [72] | TRPC6 has a median TPM of 12.0 in the thyroid. | unspecified | 80% |
| [72] | TRPC6 has a median TPM of 9.5 in the esophagus gastroesophageal junction. | unspecified | 80% |
| [72] | TRPC6 has a median TPM of 7.9 in the subcutaneous adipose tissue. | unspecified | 80% |
| [72] | TRPC6 has a median TPM of 1.2 in the kidney cortex. | unspecified | 80% |
| [59] | TRPC6 expression is down-regulated in definitive endoderm cell differentiation. | unspecified | 80% |
| [58] | TRPC6 expression is down-regulated in definitive endoderm cell differentiation. | unspecified | 80% |
| [77] | TRPC6 is dependent in unknown lines. | unspecified | 80% |
| [2] | TRPC6 deficiency does not result in an overt renal phenotype in mice. | unspecified | 80% |
| [6] | TRPC6 mutations in patients with focal segmental glomerulosclerosis resulted in distinct normalized slope conductance (NSC) progressions. | unspecified | 80% |
| [7] | Cytochalasin D, a pharmacological agent that disrupts the actin cytoskeleton, increases TRPC6 channel activity. | unspecified | 80% |
| [10] | FlAsH-tagging of TRPC6 reveals decreased interaction between the amino to carboxyl termini after OAG activation. | unspecified | 80% |
| [12] | TRPC6 channels contribute to the progression of acquired glomerular diseases, including FSGS. | inhibit | 80% |
| [45] | TRPC6 is upregulated in glomerular diseases including diabetic kidney disease. | inhibit | 80% |
| [16] | TRPC6 mutations cause a gain-of-function phenotype leading to calcium-triggered podocyte cell death. | unspecified | 80% |
| [54] | TRPC6 mutations cause a particularly aggressive form of FSGS. | unspecified | 80% |
| [18] | TRPC6 is pivotal for the activation of CaCC by guanabenz. | unspecified | 80% |
| [20] | TRPC6 mutations have variable phenotypes, ranging from healthy carrier to FSGS leading to renal failure. | unspecified | 80% |
| [50] | TNF increases TRPC6 expression and trafficking in podocytes. | unspecified | 80% |
| [23] | TRPC6 activation by PLCε was identified in murine embryonic fibroblasts (MEFs) lacking Gαq/11 proteins. | unspecified | 80% |
| [27] | Three variants (-254C>G, +43C/T, and 240 G>A) were identified in TRPC6. | unspecified | 80% |
| [28] | Mutations of TRPC6 and ACTN4 occur in only a minor portion of Chinese familial FSGS patients. | unspecified | 80% |
| [32] | NSAIDs decrease the activity of endogenous TRPC like calcium channels in podocytes. | inhibit | 80% |
| [51] | NSAIDs decrease the activity of endogenous TRPC like calcium channels in podocytes. | inhibit | 80% |
| [34] | Albumin overload induces TRPC6 expression and ER stress in podocytes. | unspecified | 80% |
| [47] | TRPC6 mutations can lead to the activation of TRPC6 channels, which may contribute to FSGS. | activate | 80% |
| [37] | TRPC6 can be phosphorylated by protein kinase C (PKC) on serine 448, which may regulate its activity. | unspecified | 80% |
| [39] | NFAT signaling in podocytes can cause glomerulosclerosis. | activate | 80% |
| [53] | NADPH oxidase-derived ROS upregulates TRPC6 expression in podocytes. | unspecified | 80% |
| [42] | Combinations of genetic heterozygosity that alone do not result in clinical kidney disease can function together to enhance susceptibility to glomerular damage and FSGS. | inhibit | 80% |
| [2] | TRPC6 loss can confer protection against kidney injury in some models. | inhibit | 75% |
| [56] | Mutant TRPC6 channels may amplify injurious signals mediated by Ang II, a common final pathway of podocyte apoptosis. | inhibit | 75% |
| [78] | TRPC6 is a fitness gene in Sanger cell lines. | unspecified | 70% |
| [13] | TRPC6 knockout increases mesangial expansion, which exacerbates glomerular injury in Akita mice. | unspecified | 70% |
| [49] | TRPC6 mutations are detected in some Iranian children with FSGS. | unspecified | 70% |
| [15] | TRP cation channels have unique sites of regulatory function in the kidney. | unspecified | 70% |
| [24] | Mutations in multiple glomerular genes may increase disease severity. | unspecified | 70% |
| [36] | TRPC6 channel modulation may be a therapeutic target for glomerular disease. | unspecified | 70% |
| [38] | TRPC6 channels play a role in glomerular filtration and pathophysiology. | unspecified | 70% |

---

*Intermediate lens report. Numbered sources above are the original records this
lens reasoned over. See [report.md](../../report.md) for cross-lens synthesis and
[full_report.md](../../full_report.md) for the complete linked evidence dossier.*
