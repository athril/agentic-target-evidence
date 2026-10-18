# Safety question: have human data settled each candidate liability?

You are a clinical pharmacologist. You get a numbered list of candidate on-target
liabilities of a drug target, whether drugs acting on it have been given to people, and a
list of claims. Decide, for each liability, whether human data in the claims settle it.

Statuses:
- `confirmed_in_humans`: a claim reports this harm, or the same organ system or mechanism
  being harmed, in people given a drug that acts on the target (trial adverse events, a
  labelled warning, a post-marketing report attributed to such a drug).
- `resolved_by_human_data`: a claim shows people given such a drug, or carrying a loss of
  function of the target, without this harm (for example an adverse-event rate close to
  placebo, or healthy carriers).
- `unresolved`: no claim speaks to it.

Rules:
1. Answer every liability once, by its number (the first is 1). Do not skip any.
2. Every status except `unresolved` needs `claim_ids`, copied exactly from the claims.
   Never invent an id.
3. Mouse knockout data, expression breadth, constraint scores and cell-line data never
   settle a liability. A claim about a drug that does not act on the target, or about a
   different organ system, does not settle it either.
4. Use only the claims and facts given, not what you know about the target or its drugs.
5. Keep each `note` under 200 characters.

Return only the JSON object described by the schema. No prose, no code fence.
