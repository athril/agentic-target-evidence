# Statement reading: pick the mentions, do not judge

You read sentences from biomedical abstracts. In each sentence the entities are already
marked and numbered, with what each one is: the target gene, another gene, a variant of
the target or of another gene, a drug acting on the target, another chemical, a disease or
phenotype, a species. You choose among these numbers. You write no text of your own, and
you never use what you know about the gene: only what the sentence says.

For each sentence, answer:

- `id`: copied exactly from the sentence list.
- `exposure`: the number of the mention the sentence reports an effect of, or `0` when it
  reports none. It must be the target, the target's ortholog, a variant of the target or a
  drug acting on the target.
- `exposure_kind`: what the sentence did with that exposure:
  - `variant`: people (or animals) carrying the variant were compared with those who do
    not; the exposure must be a variant;
  - `drug`: the drug was given; the exposure must be a drug;
  - `genetic_perturbation`: the gene itself was changed by an experiment — knocked out,
    knocked down, deleted, silenced, overexpressed, transgenic, mutated in a model; the
    exposure must be a gene;
  - `observed_level`: the gene's level, expression or activity was only measured and
    compared (higher in patients, associated with the disease); the exposure must be a
    gene;
  - `none`: none of these.
- `change`: only for `genetic_perturbation`: `decrease` when the experiment removed or
  lowered the gene (knockout, knockdown, deletion, silencing, loss-of-function mutation),
  `increase` when it added or raised it (overexpression, transgene, gain-of-function
  mutation); otherwise `none`.
- `outcome`: the number of the disease, phenotype or chemical whose amount, risk or
  severity the sentence reports moving with the exposure, or `0`.
- `outcome_direction`: whether the exposed group (the carriers, the treated, the
  perturbed models, the people with a higher level) had more or less of that outcome:
  - for a disease or phenotype, `lower` means less disease: lower risk, protection,
    remission, response to a treatment, efficacy, improvement, slower progression;
    `higher` means more disease: higher risk, onset, relapse, progression, worsening;
  - for a chemical or a level, `higher` or `lower` as the sentence says it moved;
  - `none` when the sentence reports no change or does not say which way.

  A treatment that works against a disease ("was effective in", "achieved remission",
  "improved outcomes") is `lower` for that disease, never `higher`.
- `design`: the study the sentence reports: `rct`, `meta_analysis`,
  `mendelian_randomisation`, `genetic_association`, `observational`, `preclinical` (cells
  or animals) or `other`.

A sentence that only describes background, a method or an aim reports no effect: answer
`exposure` 0, `outcome` 0. Answer every sentence in the list, in any order.

## Output

Return only a JSON object, no prose and no code fence:

{"sentences": [{"id": "<id>", "exposure": <n>, "exposure_kind": "<kind>", "change": "<decrease|increase|none>", "outcome": <n>, "outcome_direction": "<higher|lower|none>", "design": "<design>"}]}
