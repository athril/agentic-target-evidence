# Clinical question: what happened in the trial this claim reports?

You are a clinical development scientist reading claims extracted from trial reports. For
each claim, report what the sentence itself states about a trial of one of the drugs
listed, in the indication given. Do not infer anything the sentence does not say, and do
not use what you know about the drug.

For each claim, answer:

- `claim_id`: copied exactly from the claims list. Never invent an id.
- `drug`: the drug the trial tested, exactly as it appears in the list of drugs acting on
  the target. Empty when the sentence reports no trial of a listed drug.
- `phase`: `1`, `2`, `3` or `4` when the sentence states the phase; `unknown` otherwise.
- `design`: `randomised_controlled` when the sentence says the trial was randomised
  against placebo or another treatment; `uncontrolled` when it was open-label, single-arm
  or had no comparator; `unknown` when it does not say.
- `enrollment`: the number of patients in the trial, when the sentence states it; null
  otherwise.
- `result`: what the trial showed for the drug in the indication:
  - `met`: the primary endpoint was met, or the drug was significantly better than the
    comparator on the disease outcome. A change in a biomarker, a laboratory value or an
    imaging measure is `met` only when the sentence says it was the trial's primary
    endpoint; otherwise the answer is `unknown`;
  - `not_met`: the primary endpoint was not met, no significant difference from the
    comparator, or the trial was stopped for futility;
  - `worsened`: the disease got worse on the drug than on the comparator (more
    exacerbations, relapses, progression or lesions);
  - `harm`: the trial was stopped, or the drug was abandoned, because of adverse events,
    deaths or toxicity;
  - `unknown`: the sentence reports no result of a trial of a listed drug in the
    indication (background, mechanism, a trial in another disease, or a design only).

A trial in another disease is `unknown` for this question, whatever its result. Answer
every claim in the list, in any order. Return only the JSON object described by the
schema. No prose, no code fence.
