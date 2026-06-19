# Druggability Assessment Framework

Use this skill when generating hypotheses about whether a gene is a viable drug target.

## Five druggability axes

For each axis output: **verdict** (yes/no), **confidence** (0.0–1.0), **rationale** (1–3 sentences citing evidence IDs).

### 1. Druggability

Is the protein structurally amenable to small-molecule or biologic intervention?

- Look for: protein with a defined binding pocket, enzyme active site, receptor ligand-binding domain, or known interactor surface.
- Flag: intrinsically disordered proteins, scaffolding proteins with no pocket, redundant family members that would cause off-target effects.
- Tractability signals from Open Targets: `tractability.smallmolecule`, `tractability.antibody`.

### 2. Safety / Toxicity

Is target inhibition or activation likely to cause unacceptable on-target toxicity?

- Look for: essential housekeeping function, expression in critical tissues (heart, liver, CNS), known loss-of-function phenotype in humans or mouse models.
- Flag: high expression in non-diseased tissue, monogenic disease association where heterozygous LoF is already harmful.

### 3. Solubility / Developability

For biologic targets: is the protein extracellular or has an accessible epitope?  
For small-molecule targets: does the binding site have drug-like properties (Lipinski-compatible)?

### 4. Causality

Is there human genetic evidence that perturbing this gene **causes** the disease (not just correlates)?

- Strong evidence: Mendelian disease, common variant GWAS with fine-mapping, pLI < 0.1 (tolerates LoF), somatic driver mutation in disease tissue.
- Weak evidence: expression association only, animal model only.

### 5. Tissue / Cell-type specificity

Is the target expressed in the relevant disease tissue at sufficient levels?

- Use GTEx, Human Protein Atlas, and any internal RNA-seq data.
- Flag: ubiquitous expression (increases toxicity risk) or absent expression in disease tissue.

## Output format

```json
[
  {"axis": "druggability",  "verdict": true,  "confidence": 0.85, "rationale": "...", "supporting_evidence_ids": ["uuid1"]},
  {"axis": "toxicity",      "verdict": false, "confidence": 0.70, "rationale": "...", "supporting_evidence_ids": []},
  {"axis": "solubility",    "verdict": true,  "confidence": 0.60, "rationale": "...", "supporting_evidence_ids": []},
  {"axis": "causality",     "verdict": true,  "confidence": 0.90, "rationale": "...", "supporting_evidence_ids": ["uuid2"]},
  {"axis": "tissue_specificity", "verdict": true, "confidence": 0.75, "rationale": "...", "supporting_evidence_ids": []}
]
```
