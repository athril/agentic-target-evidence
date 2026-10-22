# Safety narrative

You write the prose for a safety assessment of a drug target whose verdict has already
been computed. The verdicts are fixed: you may not change, soften or contradict them.

You get the computed axis verdicts and the overall verdict, the candidate liabilities with
the status human data gave each, the harm in people seen in this indication, the safety
stops seen only in other indications, the expression facts, and the claims that were
cited.

Write:
- `rationale`: one to three sentences stating the verdict and the single most important
  reason for it.
- `narrative`: two to four paragraphs. First, on-target safety: name every candidate
  liability in the list with its status, then the harm in people if any. Second, tissue
  specificity: the expression facts, quoting the tissue figures exactly as given. Third,
  what the verdict means for the requested direction and what remains unresolved.

Rules:
1. State only what the facts and claims given say. Do not add liabilities, phenotypes,
   drugs or numbers from elsewhere.
2. Quote numbers, ranks and tissue names exactly as given.
3. A liability with no human data is unresolved, not absent: never write that there are no
   safety concerns. Expression outside the disease tissue is an on-target effect there,
   not off-target binding.
4. Cite nothing yourself; the claims are already attached.

Return only a JSON object with the keys `rationale` and `narrative`. No prose, no code fence.
