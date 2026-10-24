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

- **Formats:** any Python format spec (`.2f`, `.3g`, `,d`, `.1e`), plus `len` and `pct`. An unknown format is an error, not ignored.
- **Units:** a 0–1 fraction is written with `|pct`, never followed by a typed `%`; a count is a count, never "N-fold". `check_profile.py` flags both (`E_ANCHOR_UNIT`).
- **Lists:** a list anchor is written out only up to 8 items; for longer ones anchor an item (`rows.0.name`) or count them (`|len`).
- **Names follow their sources.** A company, institution or person named in a sentence must appear in a source that sentence cites (`W_UNGROUNDED_NAME`); a literature-map or count record does not support an assignee or a sponsor.
- **Blocks:** a line holding only `{{table:allelic_series}}`, `{{table:variant_spectrum}}`, `{{table:faers}}`, `{{figure:protein_map}}` or `{{figure:expression}}` is a programmatic block, not an anchor. The renderer replaces it with a table or figure whose rows and caption carry their own citations; it needs no citation of its own. Unknown block names fail the check (`E_UNKNOWN_BLOCK`); the allelic series is required in *Human genetics* when the run has rows (`E_MISSING_TABLE`).
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
  - blocks starting with `*Interpretation:*`;
  - blocks starting with "Not checked" or "Not assessed" (for sections no source covered, e.g. at `--depth quick`).
- **Questions need a citation** like any claim: an open question cites the record or paper that leaves it open.
- **Interpretation blocks** should still reference the facts they rest on, by repeating the key citation where it helps the reader.

## Choosing the source

1. **Prefer the system of record.** Cite the database record (`[@gnomad_constraint]`), not a paper quoting it.
2. **Cite papers for:**
   - mechanisms;
   - trial outcomes;
   - landmark discoveries;
   - findings no database encodes.

   Cite the primary paper, not a review, when the title and abstract support the claim. Use reviews for broad context.
3. **Read the abstract first.** Before citing a paper for a specific claim, read it in `raw/pubmed_abstracts.json` (every listed, GeneRIF, literature-map and allelic-series paper is there) or run `register_source.py <run_dir> abstract <PMID>`. If the abstract doesn't support the claim, don't cite that paper for it. A GeneRIF is NLM's one-line summary of a finding: cite the paper, after reading its abstract.
4. **Cite papers about the gene.** The digest leaves out keyword hits PubTator3 shows are not about the gene. A cited article PubTator annotated without any mention of the gene, an ortholog, a variant of it or one of its drugs raises `W_OFF_TARGET_CITATION`: replace it, or say in the sentence why a paper on something else supports the claim.
5. **Relation types are tags.** `pubtator_map` relation types (Association, Positive_Correlation, Negative_Correlation…) are text-mining labels of a co-mention. They show where to look, never what was found: state a finding only from an abstract you read.
6. **Respect tiers.**
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
