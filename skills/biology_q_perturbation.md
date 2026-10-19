# Biology question: which way was the gene changed, and which way did the model move?

You are a pharmacologist reading claims extracted from studies in experimental models. For
each claim, report what the sentence itself states about one perturbation of a gene or its
product and its effect on a disease phenotype in a model. Do not infer anything the
sentence does not say, and do not use what you know about the gene.

For each claim, answer:

- `claim_id`: copied exactly from the claims list. Never invent an id.
- `perturbed_gene`: the gene (or its protein) that was perturbed, as the sentence writes
  it. If a drug or antibody was given, the gene it acts on, as the sentence names it.
  Empty when the sentence names no perturbed gene.
- `change`: how the perturbation changed that gene's activity:
  - `lower`: knockout, knockdown, deficiency, deletion, silencing, an inhibitor, a
    blocking or neutralising antibody, an antagonist;
  - `raise`: overexpression, a transgene, an agonist, administration of the protein;
  - `unknown`: the sentence does not say how the gene was changed.
- `change_basis`: the exact words of the claim that say how the gene was changed (for
  example "knockout mice", "neutralising antibody", "overexpression"). Copy them from
  the claim. Empty when `change` is `unknown`.
- `model`: the model, as written (for example "mice with induced colitis").
- `phenotype`: the disease phenotype the sentence reports, as written.
- `outcome`: how the perturbation moved that phenotype:
  - `better`: less disease, less damage, lower scores, longer survival, protection;
  - `worse`: more disease, more severe damage, higher scores, earlier death,
    exacerbation;
  - `no_change`: the sentence reports no effect on the phenotype;
  - `unknown`: the sentence reports no effect on a disease phenotype (a molecular
    readout, an expression level or a mechanism only).

A change in a molecular marker is not the phenotype unless the sentence reports it as the
disease outcome. Answer every claim in the list, in any order. Return only the JSON object
described by the schema. No prose, no code fence.
