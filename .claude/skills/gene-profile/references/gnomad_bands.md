# gnomAD constraint bands

Use this wording for the *Human genetics* constraint table and anywhere constraint is
interpreted. `check_profile.py` enforces the guardrails marked ⚠.

## Loss-of-function constraint

Use LOEUF; fall back to pLI only when LOEUF is absent.

| Metric | Band | Wording |
|---|---|---|
| LOEUF | < 0.35 | haploinsufficient: one functional copy is not enough |
| LOEUF | 0.35–0.8 | intermediate LoF constraint |
| LOEUF | > 0.8 | LoF tolerant: buffered against heterozygous loss |
| pLI | > 0.9 | LoF intolerant (corroborates a low LOEUF) |
| pLI | < 0.1 | LoF tolerant |

⚠ LOEUF ≥ 0.35 does **not** support haploinsufficiency. Never call the gene
haploinsufficient, or dosage-sensitive on the strength of LOEUF, when LOEUF ≥ 0.35.

## Missense constraint

| Metric | Band | Wording |
|---|---|---|
| mis_z | ≥ 3.09 | significant missense constraint |
| mis_z | < 3.09 | not significant |
| missense OEUF | < 0.8 | missense intolerant |
| missense OEUF | > 1.0 | missense tolerant |

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

LOEUF measures tolerance to *loss* of function. A gain-of-function disease gene can be
LoF tolerant, and its matching modality is an inhibitor.
