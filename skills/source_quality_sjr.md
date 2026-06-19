# Source Quality Assessment — Predatory Journal Judgment

Use this skill when assessing whether a journal is likely predatory
(SourceQualityAgent, which runs once before the interpretation lenses; the
Critic reads the resulting map rather than recomputing it).

SJR score, quartile, novelty, and preprint status are resolved deterministically
from a bundled SCImago lookup table before this skill is invoked — you are only
called for sources that table couldn't resolve (not Scopus-indexed, or an
unrecognized journal name). Your only job here is the `predatory_flag` and a
short `quality_challenge` note. Do not attempt to assess SJR/impact factor
yourself; you have no way to look it up and any number you produced would be
guessed, not real.

## Predatory journal signals

Flag **predatory_flag=true** when ANY of the following apply:
- Journal appears on Beall's List or Cabell's Predatory Reports (use your training
  knowledge of well-known predatory publishers/journals — OMICS Publishing Group,
  Hindawi-style mass-volume titles flagged in past years, etc.).
- Journal title closely mimics a legitimate journal (e.g., "Journal of Advanced
  Medical Sciences" vs. "JAMA").
- The journal name is generic/vague combined with claims of impossibly fast
  peer review or guaranteed acceptance, if such claims are evident from context.

Default to **predatory_flag=false** unless you have a specific reason to suspect
otherwise — most unmatched journals are simply small/new/non-Scopus-indexed
specialist venues, not predatory. Being unranked is not itself evidence of being
predatory.

## Output format (per source)

```json
{
  "evidence_id": "uuid",
  "predatory_flag": false,
  "quality_challenge": "Not Scopus-indexed; no specific predatory signals found."
}
```
