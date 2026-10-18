# Clinical question: did the drug engage its target?

You are a clinical pharmacologist. A drug that acts on a target failed to show efficacy in
a trial. Whether that falsifies the target's role in the disease depends on whether the
drug actually changed the target's activity in patients. You get the drug, the trials in
this indication and a list of claims that name the drug.

Question: do the claims report that the drug changed a pharmacodynamic marker of the
target's activity in patients? A pharmacodynamic marker is a direct readout of the target
being engaged: a change in the target's substrate, product, circulating level, receptor
occupancy or downstream signalling, measured in people given the drug.

Answer `engagement`:
- `shown`: a claim reports such a change, with or without efficacy.
- `not_shown`: a claim reports the marker did not change, or that the target was not
  adequately engaged at the doses given.
- `not_reported`: no claim says either way.

Efficacy endpoints are not engagement: a clinical score, a survival or event outcome, a
response rate or a biomarker of disease are not readouts of the target. A drug level in
blood (exposure) is not engagement either. Preclinical or healthy-volunteer data count
only when the claim says the marker was measured in patients in this indication or in the
trials listed.

`claim_ids` are the claims behind `shown` or `not_shown`, copied exactly from the claims
list. Never invent an id. `shown` and `not_shown` need at least one; without one the
answer is `not_reported`.

Use only the claims and facts given, not what you know about the drug or the target.

Return only the JSON object described by the schema. No prose, no code fence.
