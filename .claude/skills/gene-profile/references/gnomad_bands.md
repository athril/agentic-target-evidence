# gnomAD constraint bands

Use this wording for the *Human genetics* constraint table and anywhere constraint is
interpreted. `check_profile.py` enforces the guardrails marked ⚠.

## Loss-of-function constraint

Use LOEUF, a continuous measure; fall back to pLI only when LOEUF is absent. These are the
bands for gnomAD v4. [gnomAD's guidance](https://gnomad.broadinstitute.org/news/2026-03-gnomad-v4-1-1/)
for a constrained gene is LOEUF < 0.45 in v4.1.1 (the 15th gene percentile), and
[it was < 0.6 for v4.0](https://gnomad.broadinstitute.org/news/2024-03-gnomad-v4-0-gene-constraint).
v2's 0.35 does not carry over, since LOEUF rose for nearly all genes. Runs of template 3 and
earlier keep the bands they were written with.

| Metric | Band | Wording |
|---|---|---|
| LOEUF | < 0.45 | strong pLoF depletion |
| LOEUF | 0.45–0.60 | moderate pLoF depletion, between gnomAD's published v4 cutoffs: make no categorical call |
| LOEUF | 0.60–0.75 | moderate pLoF depletion |
| LOEUF | ≥ 0.75 | no strong depletion of pLoF variants |
| pLI | ≥ 0.9 | LoF intolerant (corroborates a low LOEUF) |
| pLI | ≤ 0.1 | low pLI: little evidence of selection against heterozygous LoF |

These are population statements: how depleted pLoF variants are in gnomAD. They never say
that every truncating variant is benign, that complete or biallelic loss is harmless, or
that inhibiting the protein is safe. Prefer "no strong depletion of pLoF variants" to "LoF
tolerant". `{{table:constraint}}` writes these readings with the gnomAD release and the
transcript the metrics were computed on.

Give the interval beside the value: `{{table:constraint}}` shows observed/expected with its 90%
CI, and LOEUF is that interval's upper end, so a rounded value near a cutoff settles nothing.

⚠ **Constraint neither shows nor excludes haploinsufficiency.** Say a gene is haploinsufficient
only from a dosage or disease record: a ClinGen haploinsufficiency score of 3
(`clingen_dosage`), or a paper showing that losing one copy causes the disease. Such a record
stands whatever LOEUF says. A low LOEUF alone makes a gene a candidate, nothing more.

⚠ **Population measures do not negate disease evidence.** Pathogenic variants can sit in a gene
without strong depletion: reduced penetrance, late onset or a survivable phenotype all allow
it. Report both.

## Missense constraint

| Metric | Band | Wording |
|---|---|---|
| mis_z | ≥ 3.09 | significant missense constraint |
| mis_z | < 3.09 | not significant |
| missense OEUF | < 0.8 | missense intolerant |
| missense OEUF | > 0.8 | no strong depletion of missense variants |

⚠ A **higher** mis_z means **more** constraint. Never describe a low mis_z as missense
criticality.

⚠ Infer missense constraint **only** from missense metrics (mis_z, missense OEUF), never
from LOEUF or pLI, which measure loss-of-function depletion.

## Recessive and quality signals

- High pRec (> 0.9) with high pLI: biallelic loss is lethal but heterozygous loss is
  tolerated, so partial inhibition may be viable.
- Low pRec with high pLI: a single-allele reduction already causes problems, the profile
  most exposed to inhibitor toxicity.
- syn_z: expected near 0. Flag only |syn_z| > 2, which points to a gnomAD data-quality issue.

## Direction of effect

LOEUF measures depletion of *loss*-of-function variants. A gain-of-function disease gene
can show no LoF depletion, and its matching modality is an inhibitor.
