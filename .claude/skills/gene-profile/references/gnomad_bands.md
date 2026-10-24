# gnomAD constraint bands

Use this wording for the *Human genetics* constraint table and anywhere constraint is
interpreted. `check_profile.py` enforces the guardrails marked ⚠.

## Loss-of-function constraint

Use LOEUF; fall back to pLI only when LOEUF is absent.

| Metric | Band | Wording |
|---|---|---|
| LOEUF | < 0.35 | strong LoF constraint (candidate haploinsufficient) |
| LOEUF | 0.35–0.75 | moderate LoF constraint |
| LOEUF | ≥ 0.75 | no strong depletion of pLoF variants (not haploinsufficient) |
| pLI | ≥ 0.9 | LoF intolerant (corroborates a low LOEUF) |
| pLI | ≤ 0.1 | low pLI: little evidence of selection against heterozygous LoF |

These are population statements: how depleted pLoF variants are in gnomAD. They never say
that every truncating variant is benign, that complete or biallelic loss is harmless, or
that inhibiting the protein is safe. Prefer "no strong depletion of pLoF variants" to "LoF
tolerant". `{{table:constraint}}` writes these readings with the gnomAD release and the
transcript the metrics were computed on.

⚠ LOEUF ≥ 0.35 does **not** support haploinsufficiency. Never call the gene
haploinsufficient, or dosage-sensitive on the strength of LOEUF, when LOEUF ≥ 0.35.

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
