# Safety Lens — TRPC6 × Focal Segmental Glomerulosclerosis

**Verdict:** Support · **Confidence:** 85%
**Therapeutic direction:** unspecified
**Evidence considered:** 44 source(s) · 54 claim(s)
**Generated:** 2026-10-06 03:10 UTC

---

## Summary

> The available evidence supports a safe on-target profile for TRPC6 inhibition in FSGS, with no significant toxicity concerns. The target is LoF-tolerant and has a broad expression pattern that includes the disease-relevant tissue (kidney). Mouse KO phenotypes suggest potential cardiovascular effects but do not indicate severe or embryonic lethality. FAERS data are empty, which does not rule out risk.

---

## Analysis

The safety lens assessment for TRPC6 in FSGS indicates a favorable on-target profile and appropriate tissue specificity. The target is LoF-tolerant (LOEUF=0.759), with no evidence of haploinsufficiency, supporting the tolerability of pharmacological inhibition. Mouse KO phenotypes include increased vasoconstriction and systemic arterial blood pressure, which are candidate on-target liabilities but do not indicate severe or embryonic lethality. The target is expressed in the kidney at a median TPM of 1.2, consistent with its role in FSGS. Bulk expression data from GTEx show low levels in other tissues, reducing the risk of off-target effects. FAERS data are empty, which does not rule out potential risks but supports the overall safety profile.

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
| Toxicity | Yes | 85% | The target is LoF-tolerant and has no significant toxicity signals from mouse KO phenotypes or FAERS data, supporting the safety of chronic inhibition. | [15], [40], [42] |
| Tissue Specificity | Yes | 85% | TRPC6 is expressed in the kidney at a median TPM of 1.2, consistent with its role in FSGS. Bulk expression data from GTEx show low levels in other tissues, reducing the risk of off-target effects. | [26], [32] |

---

## Automated Checks

| Check | Outcome | Detail |
| --- | --- | --- |
| gnomAD constraint wording | passed | — |
| Tissue relevance (bulk TPM) | not evaluated | no curated disease-tissue mapping |
| Quantities vs. data bands | passed | — |

---

## Evidence Considered

### Literature (14)

| # | Source | Detail | Quality | Year | First Author |
| --- | --- | --- | --- | --- | --- |
| 1 | [PMID:40455255](https://pubmed.ncbi.nlm.nih.gov/40455255/) | Phase 1 trials of BI 764198, a transient receptor potential channel 6 inhibitor, in healthy volunteers and participants with kidney impairment. | ★★★ | 2025 | Schultz A |
| 2 | [PMID:40388293](https://pubmed.ncbi.nlm.nih.gov/40388293/) | Genotype-phenotype correlations and clinical outcomes of genetic TRPC6 podocytopathies. | ★★★ | 2025 | McAnallen SM |
| 3 | [PMID:36940798](https://pubmed.ncbi.nlm.nih.gov/36940798/) | Podocyte protease activated receptor 1 stimulation in mice produces focal segmental glomerulosclerosis mirroring human disease signaling events. | ★★★ | 2023 | May CJ |
| 4 | [PMID:34830371](https://pubmed.ncbi.nlm.nih.gov/34830371/) | Repurposing Riociguat to Target a Novel Paracrine Nitric Oxide-TRPC6 Pathway to Prevent Podocyte Injury. | ★★★ | 2021 | 't Hart D |
| 5 | [PMID:33918778](https://pubmed.ncbi.nlm.nih.gov/33918778/) | Effects of TRPC6 Inactivation on Glomerulosclerosis and Renal Fibrosis in Aging Rats. | ★★★ | 2021 | Kim EY |
| 6 | [PMID:25847402](https://pubmed.ncbi.nlm.nih.gov/25847402/) | Discovery and pharmacological characterization of a novel potent inhibitor of diacylglycerol-sensitive TRPC cation channels. | ★★★ | 2015 | Maier T |
| 7 | [PMID:24194522](https://pubmed.ncbi.nlm.nih.gov/24194522/) | Transient receptor potential channel 6 (TRPC6) protects podocytes during complement-mediated glomerular disease. | ★★★ | 2013 | Kistler AD |
| 8 | [PMID:23385000](https://pubmed.ncbi.nlm.nih.gov/23385000/) | Vitamin D down-regulates TRPC6 expression in podocyte injury and proteinuric glomerular disease. | ★★★ | 2013 | Sonneveld R |
| 9 | [PMID:22280812](https://pubmed.ncbi.nlm.nih.gov/22280812/) | Novel pharmacological TRPC inhibitors block hypoxia-induced vasoconstriction. | ★★★ | 2012 | Urban N |
| 10 | [PMID:18434567](https://pubmed.ncbi.nlm.nih.gov/18434567/) | 2007 Young Investigator Award: TRP'ing into a new era for glomerular disease. | ★★★ | 2008 | Winn MP |
| 11 | [PMID:40497816](https://pubmed.ncbi.nlm.nih.gov/40497816/) | Genetic Exploration of Targeting the Transient Receptor Potential Cation Channel Subfamily Member 6. | ★★☆ | 2025 | Graesser C |
| 12 | [PMID:32865014](https://pubmed.ncbi.nlm.nih.gov/32865014/) | (Pro)renin receptor decoy peptide PRO20 protects against adriamycin-induced nephropathy by targeting the intrarenal renin-angiotensin system. | ★☆☆ | 2020 | Luo R |
| 13 | [PMID:24790760](https://pubmed.ncbi.nlm.nih.gov/24790760/) | The novel diagnostic biomarkers for focal segmental glomerulosclerosis. | ★☆☆ | 2014 | Nafar M |
| 14 | [PMID:25279100](https://pubmed.ncbi.nlm.nih.gov/25279100/) | Regulation of TRPC6 Channels by Non-Steroidal Anti-Inflammatory Drugs. | ★☆☆ | 2012 | Ilatovskaya DV |

### Empirical (30)

| # | Source | Type | Detail | Quality |
| --- | --- | --- | --- | --- |
| 15 | [gnomAD](https://gnomad.broadinstitute.org/gene/ENSG00000137672) | Constraint | Constraint interpretation for TRPC6: LOEUF=0.759 (relatively LoF-tolerant (NOT haploinsufficient)); mis_z=1.70 (no meaningful missense constraint; note: higher mis_z = more missense-constrained); pLI=0.0000 (pLI-tolerant (consistent with LoF tolerance)); missense OEUF=0.928 (missense-tolerant (gene accepts amino-acid change globally)). syn_z=-0.000 (≈0 expected; \|syn_z\|>2 flags data quality). | ★★★ |
| 16 | [gnomAD](https://gnomad.broadinstitute.org/gene/ENSG00000137672) | Constraint | ClinVar variants in TRPC6 (via gnomAD): 40 Pathogenic (30 missense_variant, 5 synonymous_variant, 2 frameshift_variant, 2 stop_gained, 1 splice_acceptor_variant), 29 Likely Pathogenic (14 frameshift_variant, 9 missense_variant, 5 stop_gained, 1 splice_acceptor_variant), 124 Benign/Likely Benign out of 501 total. | ★★★ |
| 17 | [gnomAD](https://gnomad.broadinstitute.org/gene/ENSG00000137672) | Constraint | gnomAD v4: 194 HC pLoF variants observed in TRPC6. Most common AF=2.63e-05. No homozygous carriers in gnomAD. Absence at this allele count is uninformative — not evidence of biallelic lethality; selection signal comes from LOEUF/o-e. | ★★★ |
| 18 | [GTEx/HPA](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | GTEx top tissues (median TPM): Lung=23.2, Esophagus_Muscularis=19.0, Thyroid=12.0, Esophagus_Gastroesophageal_Junction=9.5, Adipose_Subcutaneous=7.9. HPA specificity: Tissue enhanced. Subcellular: Cell membrane. | ★★★ |
| 19 | [GTEx · Adipose_Subcutaneous](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Adipose_Subcutaneous: 7.9 TPM median. | ★★★ |
| 20 | [GTEx · Brain_Cerebellum](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Brain_Cerebellum: 0.6 TPM median. | ★★★ |
| 21 | [GTEx · Brain_Cortex](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Brain_Cortex: 0.7 TPM median. | ★★★ |
| 22 | [GTEx · Esophagus_Gastroesophageal_Junction](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Esophagus_Gastroesophageal_Junction: 9.5 TPM median. | ★★★ |
| 23 | [GTEx · Esophagus_Muscularis](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Esophagus_Muscularis: 19.0 TPM median. | ★★★ |
| 24 | [GTEx · Heart_Atrial_Appendage](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Heart_Atrial_Appendage: 0.3 TPM median. | ★★★ |
| 25 | [GTEx · Heart_Left_Ventricle](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Heart_Left_Ventricle: 0.2 TPM median. | ★★★ |
| 26 | [GTEx · Kidney_Cortex](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Kidney_Cortex: 1.2 TPM median. | ★★★ |
| 27 | [GTEx · Liver](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Liver: 0.0 TPM median. | ★★★ |
| 28 | [GTEx · Lung](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Lung: 23.2 TPM median. | ★★★ |
| 29 | [GTEx · Pancreas](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Pancreas: 0.3 TPM median. | ★★★ |
| 30 | [GTEx · Thyroid](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Thyroid: 12.0 TPM median. | ★★★ |
| 31 | [GTEx · Whole_Blood](https://gtexportal.org/home/gene/TRPC6) | Omics & Expression | TRPC6 GTEx v8 expression in Whole_Blood: 0.1 TPM median. | ★★★ |
| 32 | [GTEx/HPA](https://www.proteinatlas.org/ENSG00000137672) | Omics & Expression | TRPC6 RNA tissue specificity: Tissue enhanced (Human Protein Atlas). | ★★★ |
| 33 | [spoke_anatomy:TRPC6](https://spoke.rbvi.ucsf.edu/api/v1/neighborhood/Gene/name/TRPC6) | Omics & Expression | SPOKE knowledge graph confirms TRPC6 expression in 23 anatomical structure(s) (UBERON): Brodmann (1909) area 9, adipose tissue, adrenal gland, adult mammalian kidney, brain, calcaneal tendon, colon, corpus callosum, dorsolateral prefrontal cortex, frontal cortex (+13 more). | ★★★ |
| 34 | [UniProt](https://www.uniprot.org/uniprotkb/Q9Y210) | Omics & Expression | TRPC6 subcellular localization: Cell membrane (UniProt/HPA). | ★★★ |
| 35 | [DepMap](https://depmap.org/portal/gene/TRPC6) | Functional Genomics | DepMap: TRPC6 dependent in 2/1178 lines (Chronos_Combined; scores from DepMap 24Q4 Public (figshare)). Mean Chronos: -0.015 (SD 0.109, Q1/Q3 -0.079/0.053). | ★★★ |
| 36 | [project_score:TRPC6](https://score.depmap.sanger.ac.uk/gene/SIDG39402) | Functional Genomics | Project Score: TRPC6 a fitness gene in 2/512 Sanger cell lines (scaled BF < 0). Mean scaled BF: 3.485 (negative = depletion). | ★★★ |
| 37 | [gencc:TRPC6](https://thegencc.org/genes/HGNC:12338) | Genetics & Associations | GenCC gene-disease validity for TRPC6: focal segmental glomerulosclerosis 2 (Strong, Ambry Genetics); focal segmental glomerulosclerosis 2 (Strong, Labcorp Genetics (formerly Invitae)); focal segmental glomerulosclerosis 2 (Strong, PanelApp Australia); familial idiopathic steroid-resistant nephrotic syndrome (Supportive, Orphanet). | ★★★ |
| 38 | [gwas_catalog:locus_breadth_summary](https://www.ebi.ac.uk/gwas/genes/TRPC6) | Genetics & Associations | TRPC6 locus: 37 distinct GWAS traits found (0 matched Focal Segmental Glomerulosclerosis). Excluded as off-indication: Bone mineral density mean, MMP8/MMP9 protein level ratio, Brain rsFMRI measurement (amplitude of low frequency fluctations) (Net100_Node38), Height, Menarche (age at onset). | ★★★ |
| 39 | [ontology:TRPC6](https://monarchinitiative.org/HGNC:12338) | Genetics & Associations | Ontology constraints for TRPC6: Mode of inheritance: Autosomal dominant (source: HPO/Monarch); HPO phenotype breadth: 7 phenotype(s) (moderate); top terms: Autosomal dominant inheritance, Chronic kidney disease, Hypertension, Focal segmental glomerulosclerosis, Proteinuria. | ★★★ |
| 40 | [OpenTargets · TRPC6](https://platform.opentargets.org/target/ENSG00000137672/associations?disease=MONDO_0100313) | Genetics & Associations | Open Targets association ENSG00000137672 / MONDO_0100313: overall=0.780, genetic=0.891, literature=0.812, affected_pathway=0.000, animal_model=0.000, clinical=0.000, somatic_mutations=0.000. | ★★★ |
| 41 | [orphanet:TRPC6](https://www.orpha.net/en/disease/detail/656) | Genetics & Associations | Orphanet associations for TRPC6: Hereditary steroid-resistant nephrotic syndrome (ORPHA:656; Disease-causing germline mutation(s) (gain of function) in; Assessed). | ★★★ |
| 42 | [orphanet_prevalence:TRPC6](https://www.orpha.net/en/disease/detail/656) | Genetics & Associations | Orphanet prevalence: Hereditary steroid-resistant nephrotic syndrome (ORPHA:656): Unknown [Point prevalence, Worldwide, Not yet validated]. | ★★★ |
| 43 | [ot_genetics_coloc:locus_breadth_summary](https://platform.opentargets.org/target/ENSG00000137672) | Genetics & Associations | TRPC6 coloc: 23 distinct GWAS traits found (0 matched Focal Segmental Glomerulosclerosis). Excluded as off-indication: Colorectal cancer or advanced adenoma, Educational attainment, Menarche (age at onset), Height, Age when periods started menarche. | ★★★ |
| 44 | [spoke:DOID:1312](https://spoke.rbvi.ucsf.edu/api/v1/neighborhood/Gene/name/TRPC6) | Genetics & Associations | SPOKE graph: TRPC6–focal segmental glomerulosclerosis association via DISEASES; gwas_p=n/a, diseases_score=6.386. | ★★★ |

---

## Extracted Claims

| Source | Claim | Direction | Confidence |
| --- | --- | --- | --- |
| [40] | TRPC6 is associated with Focal Segmental Glomerulosclerosis. | unspecified | 90% |
| [15] | TRPC6 is relatively LoF-tolerant and not haploinsufficient. | unspecified | 90% |
| [17] | TRPC6 has 194 HC pLoF variants observed in gnomAD v4. | unspecified | 90% |
| [37] | TRPC6 is associated with focal segmental glomerulosclerosis 2 and familial idiopathic steroid-resistant nephrotic syndrome. | unspecified | 90% |
| [41] | TRPC6 is associated with hereditary steroid-resistant nephrotic syndrome. | unspecified | 90% |
| [18] | TRPC6 is expressed in the lung at a median TPM of 23.2. | unspecified | 90% |
| [18] | TRPC6 is expressed in the esophagus muscularis at a median TPM of 19.0. | unspecified | 90% |
| [18] | TRPC6 is expressed in the thyroid at a median TPM of 12.0. | unspecified | 90% |
| [18] | TRPC6 is expressed in the esophagus gastroesophageal junction at a median TPM of 9.5. | unspecified | 90% |
| [18] | TRPC6 is expressed in the subcutaneous adipose tissue at a median TPM of 7.9. | unspecified | 90% |
| [32] | TRPC6 is tissue enhanced in the lung. | unspecified | 90% |
| [32] | TRPC6 is tissue enhanced in the esophagus muscularis. | unspecified | 90% |
| [32] | TRPC6 is tissue enhanced in the thyroid. | unspecified | 90% |
| [32] | TRPC6 is tissue enhanced in the esophagus gastroesophageal junction. | unspecified | 90% |
| [32] | TRPC6 is expressed in subcutaneous adipose tissue. | unspecified | 90% |
| [34] | TRPC6 is located in the cell membrane. | unspecified | 90% |
| [28] | TRPC6 is expressed in the lung at a median TPM of 23.2. | unspecified | 90% |
| [23] | TRPC6 is expressed in the esophagus muscularis at a median TPM of 19.0. | unspecified | 90% |
| [30] | TRPC6 is expressed in the thyroid at a median TPM of 12.0. | unspecified | 90% |
| [22] | TRPC6 is expressed in the esophagus gastroesophageal junction at a median TPM of 9.5. | unspecified | 90% |
| [19] | TRPC6 is expressed in the subcutaneous adipose tissue at a median TPM of 7.9. | unspecified | 90% |
| [26] | TRPC6 is expressed in the kidney cortex at a median TPM of 1.17. | unspecified | 90% |
| [35] | TRPC6 CRISPR dependency (DepMap): not a cell-line dependency — fitness effect in 2/1178 DepMap cancer cell lines (0.2%). | unspecified | 90% |
| [36] | TRPC6 CRISPR dependency (Project Score): not a cell-line dependency — fitness effect in 2/512 Project Score cancer cell lines (0.4%). | unspecified | 90% |
| [1] | BI 764198, a TRPC6 inhibitor, was well tolerated in Phase 1 trials. | unspecified | 90% |
| [3] | TRPC6 knockout significantly improves proteinuria and extends lifespan in a mouse model of FSGS. | modulate | 90% |
| [12] | PRO20 protects against adriamycin-induced nephropathy by targeting the intrarenal renin-angiotensin system and downregulating Nox4 and TRPC6 expression. | unspecified | 90% |
| [6] | SAR7334 is a novel, highly potent and bioavailable inhibitor of TRPC6 channels. | unspecified | 90% |
| [7] | Genetic or pharmacological TRPC6 inactivation increases podocyte susceptibility to complement. | unspecified | 90% |
| [14] | NSAIDs (ibuprofen and diclofenac) decrease the activity of TRPC6 channels in podocytes. | inhibit | 90% |
| [40] | OpenTargets genetic association TRPC6↔MONDO_0100313 score=0.891 (strong) | unspecified | 89% |
| [16] | 69 Pathogenic/Likely-Pathogenic ClinVar variant(s) in TRPC6 (40 P, 29 LP) | unspecified | 85% |
| [2] | Sporadic TRPC6-AP demonstrated an earlier progression to kidney failure than familial cases. | unspecified | 85% |
| [4] | The sGC stimulator Riociguat can prevent podocyte injury by inhibiting TRPC6 activity. | unspecified | 85% |
| [10] | Blocking TRPC6 channels may be of therapeutic benefit in idiopathic FSGS. | unspecified | 85% |
| [16] | TRPC6 has 40 Pathogenic and 29 Likely Pathogenic variants associated with Focal Segmental Glomerulosclerosis. | unspecified | 80% |
| [38] | TRPC6 locus is associated with 37 distinct GWAS traits, none of which are Focal Segmental Glomerulosclerosis. | unspecified | 80% |
| [43] | TRPC6 coloc is associated with 23 distinct GWAS traits, none of which are Focal Segmental Glomerulosclerosis. | unspecified | 80% |
| [35] | TRPC6 is dependent in 2 out of 1178 cell lines. | unspecified | 80% |
| [36] | TRPC6 is a fitness gene in 2 out of 512 Sanger cell lines. | unspecified | 80% |
| [13] | DPEP1 acts via leukotrienes on TRPC6 and results in increased podocyte motility and proteinuria. | unspecified | 80% |
| [8] | Vitamin D down-regulates TRPC6 expression in podocytes, reducing proteinuria. | unspecified | 80% |
| [9] | TRPC6 channels are a target for NSAIDs that can be important in the treatment of FSGS. | inhibit | 80% |
| [15] | Constraint interpretation for TRPC6: LOEUF=0.759 (relatively LoF-tolerant (NOT haploinsufficient)); mis_z=1.70 (no meaningful missense constraint; note: higher mis_z = more missense-constrained); pLI=0.0000 (pLI-tolerant (consistent with LoF tolerance)); missense OEUF=0.928 (missense-tolerant (gene accepts amino-acid change globally)). | unspecified | 75% |
| [11] | Lower TRPC6 expression was nominally associated with reduced risk of coronary artery disease and atrial fibrillation. | unspecified | 75% |
| [42] | The prevalence of hereditary steroid-resistant nephrotic syndrome associated with TRPC6 is unknown. | unspecified | 70% |
| [44] | The prevalence of hereditary steroid-resistant nephrotic syndrome associated with TRPC6 is unknown. | unspecified | 70% |
| [5] | TRPC6 inactivation does not significantly protect against glomerulosclerosis and renal fibrosis in aging rats. | unspecified | 70% |
| [44] | SPOKE knowledge graph: TRPC6↔focal segmental glomerulosclerosis association (DISEASES score=6.386, source=DISEASES) | unspecified | 64% |
| [38] | TRPC6 locus: 37 distinct GWAS traits found (0 matched Focal Segmental Glomerulosclerosis). Excluded as off-indication: Bone mineral density mean, MMP8/MMP9 protein level ratio, Brain rsFMRI measurement (amplitude of low frequency fluctations) (Net100_Node38), Height, Menarche (age at onset). | unspecified | 50% |
| [43] | TRPC6 coloc: 23 distinct GWAS traits found (0 matched Focal Segmental Glomerulosclerosis). Excluded as off-indication: Colorectal cancer or advanced adenoma, Educational attainment, Menarche (age at onset), Height, Age when periods started menarche. | unspecified | 50% |
| [37] | GenCC gene-disease validity for TRPC6: focal segmental glomerulosclerosis 2 (Strong, Ambry Genetics); focal segmental glomerulosclerosis 2 (Strong, Labcorp Genetics (formerly Invitae)); focal segmental glomerulosclerosis 2 (Strong, PanelApp Australia); familial idiopathic steroid-resistant nephrotic syndrome (Supportive, Orphanet). | unspecified | 50% |
| [41] | Orphanet associations for TRPC6: Hereditary steroid-resistant nephrotic syndrome (ORPHA:656; Disease-causing germline mutation(s) (gain of function) in; Assessed). | unspecified | 50% |
| [42] | Orphanet prevalence: Hereditary steroid-resistant nephrotic syndrome (ORPHA:656): Unknown [Point prevalence, Worldwide, Not yet validated]. | unspecified | 50% |

---

*Intermediate lens report. Numbered sources above are the original records this
lens reasoned over. See [report.md](../../report.md) for cross-lens synthesis and
[full_report.md](../../full_report.md) for the complete linked evidence dossier.*
