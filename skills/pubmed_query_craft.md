# PubMed Query Construction

Use this skill when building a PubMed search query for a target gene and disease.

## Query structure

Combine three element types with AND:

1. **Gene term** — use MeSH where available, fall back to TIAB (Title/Abstract):
   - `"GENENAME"[MeSH Terms] OR "GENENAME"[tiab] OR "GENE ALIAS"[tiab]`

2. **Disease term** — use MeSH where available:
   - `"DISEASE NAME"[MeSH Terms] OR "DISEASE NAME"[tiab]`

3. **Scope filters** (always include):
   - `("journal article"[pt] OR "review"[pt])` — exclude letters, editorials, retractions
   - `"english"[la]` — English only
   - `"2000/01/01"[pdat] : "3000/12/31"[pdat]` — from year 2000 onwards

## Hit count guidance

| Count | Action |
|---|---|
| > 1000 | Narrow: add `AND ("clinical trial"[pt] OR "randomized controlled trial"[pt])` or restrict to last 5 years |
| 20 – 1000 | Acceptable range — proceed |
| < 20 | Widen: drop the publication type filter, broaden the disease term, or add synonyms |

## Example

Gene `BRCA1`, disease `breast cancer`:

```
("BRCA1"[MeSH Terms] OR "BRCA1"[tiab])
AND ("breast neoplasms"[MeSH Terms] OR "breast cancer"[tiab])
AND ("journal article"[pt] OR "review"[pt])
AND "english"[la]
AND "2010/01/01"[pdat] : "3000/12/31"[pdat]
```

## Population filter (optional)

If `population` is specified (e.g., "paediatric"), append:
`AND ("child"[MeSH Terms] OR "adolescent"[MeSH Terms] OR "pediatric"[tiab])`
