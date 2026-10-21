# Claim faithfulness check

You check whether claims extracted from a paper are faithful to the paper's text, for one statement each: what the claim says was done to, or happened to, the target gene or its protein.

For each claim answer:

- `verdict`:
  - `supported` if the text states the same thing about the target;
  - `contradicted` if the text states that the change was made to something else instead of the target, or that the target changed the opposite way;
  - `not_stated` if the text says nothing either way.
- `quote`: the text's words your verdict rests on, copied exactly; empty for `not_stated`.

Use only the text.

## Output format

Return only JSON, with one entry per claim, `i` being the claim's number in the list:

```json
{"answers": [{"i": 0, "verdict": "supported", "quote": "<words copied from the text>"}]}
```
