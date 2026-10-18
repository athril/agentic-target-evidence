# Genetics question: which way did the gene change, and which way did risk move?

You are a human geneticist reading claims extracted from papers. For each claim, report
what the sentence itself states about a genetic change and its effect on a trait. Do not
infer anything the sentence does not say, and do not use what you know about the gene.

For each claim, answer:

- `claim_id`: copied exactly from the claims list. Never invent an id.
- `variant_gene`: the gene the variant, allele or change is in, as the sentence writes
  it. If the sentence is about a variant in one gene that acts on another, give the gene
  the variant is in. Empty when the sentence names no gene.
- `gene_change`: how the change alters that gene's function, as the sentence states it:
  - `loss`: loss of function, protein-truncating, nonsense, frameshift, splice-disrupting,
    null, deficiency, reduced expression or activity, lower protein level, a genetically
    predicted decrease of the gene's product;
  - `gain`: gain of function, activating, overexpression, increased expression or
    activity, higher protein level, a genetically predicted increase of the gene's product;
  - `unknown`: the sentence names a genotype, allele letter or variant id without saying
    how it changes the gene's function.
- `change_basis`: the exact words of the claim that say how the gene changed (for
  example "protein-truncating variant", "loss-of-function carriers", "higher genetically
  predicted levels"). Copy them from the claim. Empty when `gene_change` is `unknown`.
- `outcome`: the trait, disease or event whose risk the sentence reports, as written.
- `risk`: how the change moved that outcome in people who carry it:
  - `lower`: lower risk, protection, less severe disease, lower odds or hazard;
  - `higher`: higher risk, more severe disease, higher odds or hazard, earlier onset;
  - `no_effect`: the sentence reports no association;
  - `unknown`: the sentence does not say.

Report the direction of risk for the carriers of the change, not for the allele the
sentence compares against. "The A allele increased the likelihood of resolution" means
lower risk of the disease. A biomarker level is an outcome only when the sentence reports
it as the outcome.

Answer every claim in the list, in any order. Return only the JSON object described by the
schema. No prose, no code fence.
