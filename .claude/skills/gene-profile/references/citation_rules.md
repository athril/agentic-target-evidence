# Citation and value rules

## Syntax in `draft.md`

| Write | Renders as | Notes |
|---|---|---|
| `… slit diaphragm [@uniprot_extra].` | `… slit diaphragm [3].` | Put the citation at the end of the sentence, bullet or table row. |
| `… [@gencc; @pmid:15879175]` | `[4, 12]` | Several keys go in one group, separated by `;`. |
| `LOEUF {{gnomad_constraint:loeuf\|.2f}}` | `LOEUF 0.76` | Value anchor. The path is dotted, with list indices. |
| `{{gnomad_clinvar:pathogenic\|len}}` | `17` | `len` gives the length of a list. |
| `{{ot_associations:top.0.disease_name}}` | `familial idiopathic …` | Strings work too. |
| `{{alphafold:fractionPlddtVeryHigh\|pct}}` | `34%` | `pct` turns a 0–1 fraction into a percentage. |

- **Formats:** any Python format spec (`.2f`, `.3g`, `,d`, `.1e`), plus `len` and `pct`.
- **Unresolved anchors** fail the check (`E_ANCHOR`). Get the path from `raw/<key>.json → data`.
- **Every anchor needs a matching citation:** a block that uses `{{gnomad_constraint:…}}` must also cite `[@gnomad_constraint]` (`E_ANCHOR_NOT_CITED`).

## Citation keys

| Key | Kind | Comes from |
|---|---|---|
| `hgnc`, `gnomad_constraint`, `ot_tractability`, … | database record, Tier 1 | `gather.py`; same name as `raw/<key>.json` |
| `pmid:<id>` | article, Tier 1 | `gather.py` PubMed fetches, or `register_source.py pmid` |
| `nct:<id>` | trial registry, Tier 2 | `gather.py` CT.gov fetches, or `register_source.py nct` |
| `patent:<id>` | patent, Tier 2 | `gather.py` USPTO fetch |
| `web:<slug>` | web page, Tier 3 by default (Tier 2 for regulators/agencies) | `register_source.py web` |

To list the keys available: `jq -r '.[].key' <run_dir>/sources.json`. Only cited sources appear in the final reference list.

## What needs a citation

- **Needs one:** every paragraph, list item and table data row in sections other than *Evidence gaps & conflicts*.
- **Exempt:**
  - headings;
  - table header rows;
  - lead-in lines ending in `:`;
  - bold-only labels;
  - questions (lines ending in `?`);
  - blocks starting with `*Interpretation:*`;
  - blocks starting with "Not checked" or "Not assessed" (for sections no source covered, e.g. at `--depth quick`).
- **Interpretation blocks** should still reference the facts they rest on, by repeating the key citation where it helps the reader.

## Choosing the source

1. **Prefer the system of record.** Cite the database record (`[@gnomad_constraint]`), not a paper quoting it.
2. **Cite papers for:**
   - mechanisms;
   - trial outcomes;
   - landmark discoveries;
   - findings no database encodes.

   Cite the primary paper, not a review, when the title and abstract support the claim. Use reviews for broad context.
3. **Read the abstract first.** Before citing a paper for a specific claim, run `register_source.py <run_dir> abstract <PMID>`. If the abstract doesn't support the claim, don't cite that paper for it.
4. **Respect tiers.**
   - Tier 1: curated databases and peer-reviewed literature.
   - Tier 2: trial registries, patents, regulator pages.
   - Tier 3: company press releases, news, investor material. Tier 3 is never used in the Snapshot and is marked ◇ in the report.

## Absence language

| Manifest status | Correct wording | Wrong wording |
|---|---|---|
| `empty` | "No ClinGen gene–disease curation was found [@clingen]." | "TRPC6 has no disease association." |
| `skipped` (licence gate, no key, gene-only run) | In *Evidence gaps*: "OMIM not checked (licence gate off)." | "Not in OMIM." |
| `error` | In *Evidence gaps*: "Expression Atlas not checked (API timeout)." | Silence. |

Absence from a **keyword search** (PubMed, CT.gov, USPTO, openFDA) is weak. Write "the search returned no …", not "there are no …".
