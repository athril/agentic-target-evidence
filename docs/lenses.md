# Lenses & verdicts

> Part of the [docs/](README.md) documentation set. The lenses are the interpretation
> layer of the pipeline in [architecture.md §3](architecture.md); they are run as the
> agents described in [agents.md](agents.md#interpretation--the-six-lenses).

The interpretation layer reasons over screened, typed evidence and produces structured
verdicts. It is built around **six independent lenses** that are reconciled — not averaged
— into a consensus, alongside a single numeric suitability score.

---

## What a lens is

A lens is a `BaseAgent` that takes the run's extracted claims (plus pre-rendered structured
context), reasons over **only the evidence relevant to its perspective**, and emits exactly
one `LensVerdict`. All six share `run_lens()` in
[_lens_base.py](src/agents/interpretation/_lens_base.py):

1. deserialize `CoreClaim`s from `task_spec["extracted_claims"]`;
2. filter to the claims this lens reasons over (`claim_matches_lens`);
3. rank claims by source quality and truncate to a context-safe cap;
4. load the lens's prompt from `skills/{lens}_lens.md` and call the routed LLM;
5. parse a `LensVerdict` (falling back to `insufficient_evidence` if unparseable).

The six lenses: **genetics, biology, safety, clinical, commercial, regulatory.**

### What each lens reads

Structured evidence routes by `EvidenceType`; a claim reaches a lens iff its type is in that
lens's `LENS_EVIDENCE_TYPES` tuple (single source of truth in
[_lens_base.py](src/agents/interpretation/_lens_base.py)):

| Lens | Structured `EvidenceType`s it consumes |
|---|---|
| genetics | `GENETICS`, `CONSTRAINT` |
| biology | `FUNCTIONAL_GENOMICS`, `DRUGGABILITY`, `OMICS`, `EXPRESSION`, `REGULATORY_ELEMENT` |
| safety | `OMICS`, `EXPRESSION`, `GENETICS`, `CONSTRAINT`, `REGULATORY`, `FUNCTIONAL_GENOMICS` |
| clinical | `CLINICAL_TRIAL` |
| commercial | `PATENT`, `REGULATORY` |
| regulatory | `REGULATORY` |

The same source can therefore feed several lenses (e.g. `REGULATORY` from OpenFDA reaches
safety, commercial, **and** regulatory).

**Drug records also route by their relation to the target.** The openFDA text search
returns labels that merely mention the gene or the disease, so each label and its FAERS
bundle is stamped at retrieval with its `target_relation`
([core/evidence_text.target_relation](src/core/evidence_text.py)), and the claims built
from it carry it: `acts_on_target` (an Open Targets known drug, or a mechanism of action
naming the gene), `indication` (approved in the disease by another mechanism) or `other`.
`LENS_TARGET_RELATIONS` decides who reads what: the safety lens reads target drugs only;
regulatory and commercial also read the indication's drugs (class precedent, standard of
care); a record related by text search only reaches no lens. Open Targets safety-liability
rows from pharmacogenomic sources (ClinPGx, formerly PharmGKB: a variant changing a drug's
response) are not liabilities and are listed apart
([liabilities.target_safety](src/services/evidence/liabilities.py)).

**Literature routes differently.** Free-text literature (`ARTICLE`, `ABSTRACT`, `BOOK`,
`CONFERENCE`) carries no native sub-type, so each claim is tagged with `LensTopic`s during
extraction and reaches a lens iff that lens is named in its `topics`. Only four lenses are
`LensTopic`s — **genetics, biology, safety, clinical** — so **commercial and regulatory
never consume literature**; they reason over structured sources only.

---

## What a verdict contains

```python
class LensVerdict(BaseModel):
    lens: Literal["genetics","biology","safety","clinical","commercial","regulatory"]
    overall_verdict: Literal["support","oppose","neutral","insufficient_evidence"]
    confidence: float                 # 0.0–1.0, computed from the axes (see below)
    model_confidence: float | None    # the LLM's self-reported value, kept for audit
    axes: list[AxisVerdict]           # the per-dimension breakdown
    rationale: str                    # 1–3 sentence dossier summary
    narrative: str                    # 2–4 paragraph prose for the report
    validation_flags: list[ValidationFlag]
    # + run_id, trace_id, target_gene, disease, direction

class AxisVerdict(BaseModel):
    axis: str                         # e.g. "causality", "toxicity"
    verdict: bool | None              # True=favourable, False=unfavourable, None=uncertain
    confidence: float                 # computed from the cited claims
    model_confidence: float | None
    evidence_sources: int             # independent sources behind `confidence`
    rationale: str
    supporting_claim_ids: list[str]   # claim IDs grounding this axis
```

### How confidence is computed

The LLM decides the categorical parts — the overall verdict, each axis verdict and which
claims support each axis. The numbers are computed from those citations
([_lens_confidence.py](src/agents/interpretation/_lens_confidence.py)), so they are
reproducible and traceable; the LLM's own numbers stay in `model_confidence` and the lens
report shows both.

- **Axis evidence.** The lens model's own citations, plus the structured records
  [axis_evidence.yaml](config/axis_evidence.yaml) says the axis is about (e.g. UniProt /
  ChEMBL / DGIdb for `druggability`; claims carrying an `exclude` phrase such as "0
  matched" are never attached; an axis's `include` phrases narrow it further, e.g.
  `clinical_validation` takes only trial *results* claims, not "phase 3 recruiting"), plus up to 5 literature claims picked by the **citation
  step** ([_lens_citations.py](src/agents/interpretation/_lens_citations.py)): the 15
  literature claims most similar to the axis (embeddings) are shown to the model, which
  picks those that directly support the verdict. Additions are listed in
  `added_claim_ids` and shown as "added:" in the lens report's source column. The step only
  adds support — the small model's "contradicts" judgments proved unreliable — and is
  skipped cleanly if embeddings or the model are unavailable.
- **Axis score.** Supporting claims the lens was shown are grouped into independent sources
  (one document, or one patent family), each counted once at its strongest claim's
  [evidence-hierarchy](config/evidence_hierarchy.yaml) weight `w`, and sources are grouped by
  *kind* of evidence (each structured type; all literature together). A kind contributes
  `min(0.75, 1 − Π(1 − 0.5·w))` — at most two strong sources' worth, however many papers
  repeat a finding — and kinds combine as independent lines: `1 − Π(1 − s_kind)`, capped at
  0.95. A favourable axis citing a *definitive* claim (GenCC Definitive/Strong, Orphanet
  disease-causing mutation, FDA approval in the indication — type-level, in the hierarchy)
  or an axis-scoped one (a phase 2+ trial for `clinical_precedent` — in axis_evidence.yaml,
  because a recruiting phase 3 settles precedent but not validation) scores at least 0.85.
- **Lens (support / oppose).** Mean of the axes agreeing with the verdict, capped at the
  weakest agreeing axis + 0.2; each contrary axis scales it by `1 − 0.5·confidence`. An
  axis that cites no evidence (no source, confidence 0) is left out of both. A computed
  corroboration (`direction_of_effect`, `perturbation_direction`) that agrees is left out of
  the mean and the cap (unless no other axis agrees); a contrary one still discounts.
- **Lens (neutral / insufficient_evidence).** No finding is asserted, so the LLM's value is
  kept but capped at 0.5 — a placeholder, not a degree of belief. Reports therefore show
  such a lens as "— (mixed: 1 favourable, 2 unfavourable)" or "— (insufficient evidence)"
  instead of a percentage; the consensus confidence only averages lenses that agree with it.

Weights are lens-aware (`by_lens` in the hierarchy): a patent is weak evidence that a target
is biologically valid but primary evidence for the commercial lens; druggability records
are primary for biology, FDA records for regulatory and commercial, the indication's
drug/trial landscape for commercial. Definitive floors (`definitive` in the hierarchy)
cover GenCC Definitive/Strong, Orphanet disease-causing mutations and an FDA approval
whose indication names the run's disease.

A directional verdict whose scored axes all point the other way scores 0 and is flagged
`verdict_axis_conflict`; the lens report says so next to the number. When a deterministic rule
then changes the verdict or its axes (`map_lens_verdict`, `refresh_derived`), the flag is
recomputed on the final verdict, sentences stating another verdict are aligned, and the
model's own verdict is kept in `model_verdict`. The lens report then says "set by rule" and
labels the summary as the model's reading; the dossier's Lens Summary says which verdict
the model gave. A **neutral** verdict
whose axes are all scored and all agree contradicts the lens's own decomposition: it is
replaced by that direction (and scored as such), with a `neutral_axis_conflict` flag
recording the model's verdict.

### Grounding checks

Three deterministic checks run on every lens, each annotating the prose (never rewriting
it) and recording a `ValidationFlag`. All source- and fact-specific knowledge is config,
so they cover any gene, disease or connector without code changes:

| Check | Config | Fires when |
|---|---|---|
| Citation repair | [source_registry.yaml](config/source_registry.yaml) `attach` | an axis rationale names a source but cites none of its claims — its structured claims are attached |
| Numeric grounding | [source_registry.yaml](config/source_registry.yaml) `check_numbers` | a number in a sentence naming a source is absent (to the precision written) from that source's claims |
| Fact checks | [fact_checks.yaml](config/fact_checks.yaml) | prose denies a fact a shown claim states, e.g. "no boxed warning" against a label that has one, a drug's boxed warning called "relevant to the target" when its labelled mechanism does not name the target, or "no clinical-stage drugs targeting {gene}" when a phase 2+ trial of one was retrieved |

A new connector is covered by adding it to the source registry; a new contradiction
pattern by adding a fact check.

An axis whose rationale states, in genetic terms, which way of changing the target helps
must cite at least one claim with a typed effect (a change in the target and a benefit or
harm to the disease). Otherwise the axis is flagged `uncited_direction` and its
confidence is multiplied by 0.6.

### Repair step and independent check

Before the checks annotate a lens's prose, one bounded **repair step**
([_lens_repair.py](src/agents/interpretation/_lens_repair.py), prompt
[skills/lens_repair.md](skills/lens_repair.md)) sends only the sentences a guard, numeric
grounding or fact check flags (non-terminology findings) back to the lens model with the
check's note and the data lines the sentence relies on. Each field's rewrite is re-checked by
the same checks and kept only if it has fewer violations and no new kind, and a rewrite must
stay on the sentence's topic (keep at least two, and a fifth, of its content words); every accepted
rewrite records a low-severity `prose_repaired` flag carrying the original sentence. One call
per flagged lens, no retries; the checks then annotate whatever remains, so flags describe the
published text. `LENS_PROSE_REPAIR=0` turns it off (the setting is part of the lens cache key).

The **critic** then reviews the result independently of the lens that wrote and repaired it:
its deterministic `prose_qa` pass lists, per lens, every corrected sentence and every
medium/high flag still open, and the dossier's **Prose Checks** section renders it.

### Claims built without the LLM

Structured records become claims deterministically
([claim_extraction.structured_claims](src/services/evidence/claim_extraction.py)), so the
lenses never depend on the extraction LLM for them: genetics/constraint/dependency
readings, trials (their registry fields), FDA labels (approval and MoA, one claim per indication — marked when it
names the disease — and the boxed warning or its absence), FAERS counts, patents (owner,
filing date, whether the target is named; patents skip LLM extraction, whose output
restated biology), the indication's drug/trial landscape, and "summary" types whose
connector pre-renders a one-fact `text` (UniProt, ChEMBL, DGIdb, TTD, ENCODE).

Structured database records (genetics, constraint, expression, omics, functional
genomics, regulatory elements, druggability) and the indication landscape skip LLM
extraction entirely: the model only paraphrased them, losing numbers and dropping whole
batches. A record no dedicated builder covers (IMPC, Expression Atlas, GTEx/HPA, an
ontology record, a gnomAD LoF bundle) becomes claims from its own rendered text. A short
text is one claim; a long one is split into one claim per sentence, and a sentence of
`;`-separated items (outside quotes) into one claim per item under its heading, so no fact
sits past what a lens prompt shows of a claim. A builder's decision to emit nothing (a
weak association, a dependency without line counts) stands. The LLM extracts from free
text only: literature, trial summaries and label sections.

LLM-extracted claims (literature) must be supported by their source: a claim is dropped,
with a warning in the log, when it carries a number the source text does not state (to the
precision written; spelled-out numbers and roman phase numerals count) or an identifier
from the prompt's worked example (NSCLC, KRAS, …) that the source lacks. A 7B model once
returned the example's "37% objective response rate in previously treated NSCLC" for a
phase 2 FSGS abstract.

The `overall_verdict` vocabulary — `support` / `oppose` / `neutral` /
`insufficient_evidence` — is shared with the reconciler. Note `insufficient_evidence` is a
distinct, first-class outcome: it means "no evidence of this lens's type passed screening,"
**not** a negative finding.

### Split lenses: typed questions instead of one verdict call

A lens call sends a long skill, pre-computed blocks and up to a hundred claims to one
request that must return every axis, confidence, citation and the prose at once; a 7B
model follows only some of its rules. A lens can instead run as **narrow typed questions**
([_lens_questions.py](src/agents/interpretation/_lens_questions.py)): each question gets
its own short skill, only the facts and at most 25 claims that bear on it, and returns a
pydantic answer. Code validates it, retries once with the validation error, drops
citations to claims the model was not shown, and computes the verdict from the answers by
fixed rules; a final call writes the prose around the fixed verdict, which then meets the
same checks as every lens (`finish_verdict`).

The safety lens asks no questions. Its findings come from structured data
([liabilities.py](src/services/evidence/liabilities.py): curated Open Targets events,
mouse knockout phenotypes, constraint, essentiality, boxed warnings of drugs acting in
the requested direction), each one's status from where people saw it, and
the `toxicity` and `tissue_specificity` axes from fixed rules
([safety_lens/aggregate.py](src/agents/interpretation/safety_lens/aggregate.py)); one call
writes the prose around the fixed verdict, and the lens report prints each finding with its
status. The narrative skill and the aggregation version are part of the lens cache key.

### Per-lens axes and conventions

Axis names are produced by the LLM under each lens's skill prompt (not a code enum). The
canonical axes each skill defines:

| Lens | Axes | Notable conventions (verified in the skill / agent) |
|---|---|---|
| genetics | `causality`, `genetic_validity` | Pathogenic/Likely-Pathogenic **ClinVar** variants are routed to the **causality** axis as *primary* evidence for Mendelian disease (even when tagged constraint-type); ≥2 P/LP (gold-stars ≥1) ⇒ `causality.verdict=true`. Absence of GWAS never lowers a causality verdict already supported by Mendelian/ClinVar variants. A deterministic **Mendelian-causality floor** can override the LLM (see below). **Disease-class-aware** (see below): e.g. for oncology indications the absence of germline signal is *not* penalized. When the gene has **no global missense constraint**, a deterministic missense-framing note (`missense_framing_note`) forbids "missense-intolerant" wording; for a gain-of-function gene it explains that pathogenic missense variants act at specific residues, names LoF tolerance as the favourable signal for inhibition, and gives the recommended sentence, which the `constraint` guard accepts. The overall verdict is computed from the axes and the human-genetics direction reading (`settle_genetics_verdict`): oppose when the direction-of-effect axis is unfavourable; neutral when the reading contradicts or conflicts, or when the model opposed and no human genetics read the direction; support when causality is favourable and genetic validity is not unfavourable; neutral otherwise. |
| biology | `druggability`, `mechanism_of_action`, `developability` | **Disease-class-aware** DepMap reading: near-zero dependency across cancer lines counts for an oncology indication but is *not* held against a non-oncology one. `developability` reasons over UniProt subcellular localization (biologic epitope accessibility) and ChEMBL clinical-candidate progression (chemical-matter existence proof); it is a secondary signal. The overall verdict is computed from the axes (`settle_biology_verdict`): oppose only when `mechanism_of_action` is unfavourable, or `perturbation_direction` is and the human genetics do not support the direction; support when every scored axis is favourable; neutral otherwise. Pan-essentiality is reported with its numbers but weighed by the safety lens and the dependency gates, not here. |
| safety | `toxicity`, `tissue_specificity` | **Polarity is inverted on `toxicity`**: `verdict=true` means the safety profile is acceptable (not a liability); `verdict=false` means there *is* a safety concern. (Skill: `safety_lens.md`.) |
| clinical | `clinical_precedent`, `clinical_validation` | Reasons over clinical-trial evidence only. The `clinical_trial` and `quantity` guards check the prose against the registry's phase and status of each trial. Only outcomes of drugs acting on the target count toward validation. |
| commercial | `ip_landscape`, `competitive_opportunity` | Reads patents + the approved-drug (FDA label) landscape; no literature. Target IP is kept apart from indication-only patents. When indication-level competition counts were retrieved, the framing cites them and scopes any "underserved/whitespace" claim to the target, never the indication. Orphanet prevalence comes from the **gene's** disorder associations, so only a record naming the indication sizes it; other disorders are named but marked as not the indication's, and with no indication-level prevalence (Orphanet or GBD) an unfavourable `market_size` becomes uncertain (`market_size_unsized` flag). |
| regulatory | `approval_precedent`, `label_safety`, `regulatory_de_risking` | Reads FDA labels only (FAERS goes to the safety lens). When no label shown names the target in its mechanism, a pre-scoring rule sets `approval_precedent` unfavourable, `label_safety` uncertain and an unfavourable `regulatory_de_risking` uncertain (`off_target_label` flag): another mechanism's approval or boxed warning is neither target precedent nor a target effect. Matching is by gene symbol, so a label naming only the protein (e.g. "PD-1" for PDCD1) is read as off-target. A model `insufficient_evidence` becomes `neutral` (`regulatory.labels_shown` flag) when the lens was shown at least one label claim: "insufficient" means no label data at all. |

Because axes carry their own `verdict`, treat each axis on its own terms — particularly the
safety `toxicity` inversion above.

### Validation flags

`ValidationFlag` (`lens`, `severity` ∈ {high, medium, low}, `rule_id`, `claim_excerpt`,
`message`) is the mechanism for a deterministic reasoning-check to override or annotate the
LLM's narrative; high-severity flags are intended to surface for human attention. Today this
is used by the **genetics lens**, which emits a `mendelian_causality_floor` flag and floors
the `causality` axis (confidence ≥ 0.60) when it sees gold-star P/LP variants, ClinGen
Definitive/Strong validity, and/or strong knowledge-graph corroboration — i.e. when the
deterministic genetics signal is stronger than the LLM concluded.

---

## Generalizing across disease classes

A MASH target should not be judged with the same heuristics as an oncology, immunology, or
neurology target, and not all evidence deserves equal weight. Rather than scatter
oncology-only `if` branches through the lenses, the system resolves each run's disease into one
or more **disease classes** and feeds that into three deterministic, config-driven mechanisms
that frame what the LLM sees. (All three follow the same *pre-compute the correct framing →
inject into the prompt → guard the output* pattern as the genetics floor above.)

- **Disease-class taxonomy** ([services/evidence/disease_class.py](src/services/evidence/disease_class.py),
  [config/disease_class.yaml](config/disease_class.yaml)). `resolve_disease_class(...)` maps a
  disease to a **set** of classes — `oncology`, `metabolic`, `fibrosis`, `rare_mendelian`,
  `autoimmune`, `infectious`, `neurology`, `other` — that are **not** mutually exclusive (MASH
  resolves to both `metabolic` and `fibrosis`). Broad classes come from Open Targets
  therapeutic areas plus a small curated EFO-override list; `rare_mendelian` is inferred from
  the same genetics-floor signals (gold-star P/LP + ClinGen Definitive/Strong), since rare
  Mendelian genes span every therapeutic area. This replaces the former hardcoded
  oncology/not-oncology binary.
- **Evidence hierarchy** ([services/evidence/evidence_hierarchy.py](src/services/evidence/evidence_hierarchy.py),
  [config/evidence_hierarchy.yaml](config/evidence_hierarchy.yaml)). A deterministic,
  disease-class-conditional weight per evidence type — human causal genetics / intervention /
  clinical efficacy rank highest; cell-culture and generic omics moderate; DepMap *outside
  oncology* low; uncited model prior knowledge lowest. These weights drive **claim
  ranking/truncation** (so generic-omics/DepMap claims are dropped before human-causal-genetics
  claims when the context window is tight) and an injected **evidence-strength ledger** that
  tells the LLM, in-prompt, that uncited prior knowledge cannot raise confidence. (Weighting the
  numeric suitability score itself is deliberately out of scope here.)
- **Disease-class rule matrix** ([services/evidence/disease_class_rules.py](src/services/evidence/disease_class_rules.py),
  [config/disease_class_rules.yaml](config/disease_class_rules.yaml)). Per-`(disease_class,
  lens)` guidance lines, injected into each lens prompt — e.g. *oncology + genetics*: germline
  GWAS absence is neutral; *non-oncology + safety*: an embryonic-lethal mouse knockout is
  low-relevance for an adult-onset chronic indication; *metabolic/fibrosis + biology*: liver
  enrichment is informative; *rare_mendelian + commercial*: Orphanet prevalence sizing applies.
  This replaced the genetics skill's former hardcoded "Oncology targets" prose block, so all six
  lenses get uniform disease-class treatment.

The disease classes are computed once and flow to every lens via `task_spec`, and post-LLM
**guards** (mirroring the constraint/tissue guards) annotate verdicts that lean on, e.g., DepMap
essentiality for a non-oncology target where it carries no signal.

---

## Output guards

After each lens's LLM call, deterministic **guards** check the verdict text (summary, narrative,
every axis rationale) against facts computed for the run, and **annotate** contradictions with
an inline `[⚠ <FAMILY> GUARD: …]` note. They never silently rewrite the prose. All guard
families run from one registry ([services/evidence/guard_rules.py](src/services/evidence/guard_rules.py))
over one fact sheet (`GuardFacts`):

| Family | Checks | Lenses |
|---|---|---|
| `constraint` | haploinsufficiency vs. LOEUF, missense constraint vs. mis_z/MOEUF, raw pLoF counts used as selection evidence | genetics, safety |
| `mendelian_floor` | GWAS/coloc absence cited against a Mendelian-grade gene | genetics |
| `commercial` | blanket "no drugs", indication-level "underserved", market size "unknown" | commercial |
| `clinical_trial` | per-trial phase/status vs. the registry | clinical |
| `outcome_terms` | a named clinical outcome (stable disease, objective response, survival, biomarker/target engagement, PK/PD) that no claim shown to the lens reports | clinical |
| `tissue_relevance` | a high-bulk-TPM, non-disease tissue called disease-relevant | biology, safety |
| `dependency_relevance` | cancer-cell-line dependency cited as support for a non-oncology target | biology |
| `quantity` | vague quantifiers that contradict a measured quantity's band (e.g. "some cell lines" for 2/512) | biology, safety, clinical |
| `direction` | a sentence saying, in genetic terms, that lowering or raising the target raised or lowered the disease's risk, when the run's human-genetics direction reading weighs the other side more ([direction_guard.py](src/services/evidence/direction_guard.py)); penalises `toxicity` and `mechanism_of_action` | genetics, biology, safety |

Design rules that keep the guards precise:

- **Claims, not words.** Every guard judges sentences and clauses through shared primitives
  ([guard_core.py](src/services/evidence/guard_core.py)). These handle clause boundaries, a
  negation before the term (including or-lists and "non-" prefixes), explicit scope ("underserved
  *at the target level*"), and stance ("the absence is *expected*").
- **Numbers become bands, not adjectives.** Quantities are banded deterministically
  ([readings.py](src/services/evidence/readings.py)). The prompt quotes the band label verbatim,
  and the `quantity` guard checks prose against the band. Adding a metric takes one
  `register_metric` call.
- **Missing inputs are visible.** Every lens records a `GuardCheck` per family (`passed`,
  `flagged` or `not_evaluated`), rendered as an **Automated Checks** table in the lens report.
  For example, the dependency checks report "not evaluated" when neither DepMap nor Project
  Score returned data. Cell-line dependency merges DepMap with Project Score as a fallback.
- **Compliance is never punished.** Each family declares the wording it recommends. An invariant
  test runs every family over every recommended phrasing, and checks that guards ignore one
  another's annotations and that annotation is idempotent.
- **Measured against real output.** [tests/fixtures/guard_corpus.yaml](tests/fixtures/guard_corpus.yaml)
  holds labelled sentences harvested from real runs plus synthetic contrast cases from other
  disease classes. `scripts/guard_flag_inventory.py <report dir>` lists the flags in a
  generated report, and `--candidates` emits unlabelled corpus entries from it.

---

## `source_quality` — the field every lens reads but no lens computes

Every lens receives `task_spec["source_quality"]`: a map of `evidence_id → {sjr_score,
sjr_quartile, predatory_flag, preprint_flag, …}` used to rank and weight literature claims
(structured/database evidence is weighted as top-tier-by-construction). **No lens computes
this** — it is produced once, upstream, by `SourceQualityAgent` in the `source_quality` node
*after* claim extraction (see [agents.md](agents.md#screening--turning-raw-evidence-into-typed-scored-claims)),
using SJR (SCImago) with an OpenAlex fallback. The licensing distinction between those two
journal-quality sources is covered in [data_sources.md](data_sources.md#journal-quality-sjr-vs-openalex).

---

## Cross-lens reconciliation

After the lenses run, `reconcile()`
([services/decision/reconciler.py](src/services/decision/reconciler.py)) — a **deterministic,
no-LLM** service — combines the six `LensVerdict`s into an `AgreementMap`:

- **`consensus_verdict`** — majority vote across the lenses' `overall_verdict`s, with a
  **conservative tie-break**: on a tie, the *more conservative* verdict wins, ordered
  `insufficient_evidence` → `oppose` → `neutral` → `support` (six lenses make 3-3 splits
  plausible, so the tie-break matters).
- **`consensus_confidence`** — the mean confidence of the lenses that match the consensus.
- **`agreeing_lenses` / `dissenting_lenses`** — who's with and against the consensus.
- **`conflicts`** — every direct (support, oppose) lens pair, named explicitly (e.g.
  *"commercial supports while clinical opposes"*) rather than averaged away.
- **`shared_claim_conflicts`** — claim IDs cited by **both** a supporting and an opposing
  axis: the specific evidence two lenses read in opposite directions, surfaced for human
  attention.

The design intent is to **surface disagreement, not collapse it** — the reconciler never
blends the six verdicts into one verdict-by-averaging; it reports the consensus and names
exactly where the lenses diverge.

### The single suitability score

Separately from the qualitative consensus, the dossier reports **one numeric suitability
score (0–100)** with a recommendation band. It is computed deterministically by
[services/decision/suitability.py](src/services/decision/suitability.py) from weights in
[config/scoring.yaml](config/scoring.yaml); the `experiment` node's LLM only writes the
rationale that explains it, so the same lens verdicts always give the same score:

- **Evidence score** = 50 + 50 × Σ(weight × sign × confidence) / Σ weight, with sign +1 for
  support, −1 for oppose and 0 for neutral, insufficient or missing. A lens without a
  verdict keeps its weight, so missing evidence pulls the score toward 50 (uncertain).
- **Liability penalty** — points per candidate on-target liability (high 4, medium 1.5),
  capped at 15 and halved when a drug acting on the target has human clinical exposure (an
  approved known drug). Only the liabilities the split
  safety lens left unresolved or confirmed in humans count, and none when a same-direction
  drug is approved in the indication; without per-liability statuses every candidate counts.
- **Mendelian-causality floor** (default 70) — lifts the score for a Mendelian-grade
  gene–disease pair, unless safety opposes or a high-severity liability is open without
  human exposure.
- **Direction-of-effect gate** — applied last: when the direction-of-effect reading
  contradicts the requested direction with at least `min_against` strength, the score is
  capped at 30 (`direction_gate`).
- **Band** — PROCEED ≥ 75, PROCEED WITH CAUTION ≥ 60, INVESTIGATE FURTHER ≥ 45, otherwise
  DEPRIORITISE. The report shows the per-lens contributions.

### Direction of effect

[services/evidence/direction_of_effect.py](src/services/evidence/direction_of_effect.py)
records each piece of evidence that says which way of changing the target helps, as one
typed observation: the change in the target (decrease / increase) and what it did to the
disease (benefit / harm). Every lens and gate reads the same reading. Its sources are drugs
approved in the indication, label warnings, curated Mendelian mechanisms, Open Targets
burden and credible-set rows with direction annotations, and human-genetics literature.

Literature is typed, never tagged. The claim extractor reports how a claim says the target
changed, the words that say so, and the outcome; code checks the words and derives the
claim's direction ([claim_effect.py](src/services/evidence/claim_effect.py)). Genetics
claims that describe a genetic change are also asked the allele question (which way the
gene changed, which way the carriers' risk moved), answered from the sentence alone.

A sentence naming an allele without its effect ("the B2 allele lowered risk") is typed
"unknown"; papers state a variant's effect in one place and its association in another.
A per-run **variant registry**
([variant_registry.py](src/services/evidence/variant_registry.py)) collects every variant
whose effect on the target a run's sentence states, and fills the unknown change of other
sentences naming the same variant. Matching is exact and orientation-safe: a protein change
by position and variant residue ("R46L", "p.Arg46Leu", "the L46 allele" are one key), an rs
id only with its allele, a named polymorphism's allele. The other allele of a pair is never
inferred, a variant two sentences disagree on is dropped, and a truncating change (stop,
frameshift) is a loss by its consequence. A filled answer names its source claim in
`change_source`.

Literature observations are weighed by the **study design** their words name
(`DESIGN_WEIGHTS`), not counted as equal votes: Mendelian randomisation, genetically
proxied exposures and exome or burden analyses of protein-truncating variants count in
full (1.0); carriers of variants named by their function (loss- or gain-of-function,
truncating, null) 0.8; genome-wide associations 0.6; a named polymorphism's association
0.35. Each observation shows the design it was weighed as. Literature is then netted
(margin over the noise expected from that many weighted votes) and capped at 0.6, so it
never decides alone.

### Candidate on-target liabilities

[services/evidence/liabilities.py](src/services/evidence/liabilities.py) lists every
liability the gene's own data support — curated Open Targets safety events, mouse-knockout
organ systems ([organ_systems.py](src/services/evidence/organ_systems.py)), gnomAD
constraint against an inhibitor or degrader, common essentiality, and boxed warnings of
drugs acting on the target (`label_acts_on_target`: the label's MoA names the gene, or the
drug is one of the target's known drugs) — except drugs acting in the opposite direction
to the run (an agonist's warning is not a degrader's liability). The safety lens receives them as a block it must cover item by item
(Rule 18), the dossier prints them under **Key Liabilities**, and the score subtracts them.
When the safety prose omits a mouse-knockout organ system or names one that is not in the
data, the guard lowers the toxicity axis (×0.6, as a failed fact check does) and the lens
confidence is re-aggregated.

---

## How it surfaces in the dossier

`ReportAgent` renders the result under
`results/report/{gene}/{disease}/{direction}/` as `report.md` (the short dossier),
`full_report.md` (the categorized evidence), and one file per lens under `lenses/`. The
dossier's executive summary leads with both headline numbers:

> **The per-lens markdown files under `lenses/` are rewritten during a run** — don't read or
> act on one while a run is still in progress. See
> [faq.md](faq.md#the-lens-markdown-files-under-resultslenses-look-incomplete-mid-run--is-that-a-bug).

```
✅ Overall consensus: Support (confidence 84%) | Suitability score: 75/100

### Lens Summary
- Genetics lens   ✅ Support (90%) — …
- Biology lens    ✅ Support (80%) — …
- Clinical lens   ⚖️ Neutral (70%) — …
- Safety lens     ✅ Support (75%) — …
- Commercial lens ✅ Support (90%) — …
- Regulatory lens ❓ Insufficient evidence (0%) — …
```

…followed by the evidence split into **Literature** (with journal-quality stars, year, first
author) and **Empirical** (Open Targets, genetics, omics, functional, regulatory). Reading
that dossier end to end is covered in [tutorial.md](tutorial.md).

> Per the project's own disclaimer ([NOTICE.md](../NOTICE.md)), every verdict here is
> LLM-generated over retrieved evidence and must not be trusted blindly — always check the
> underlying evidence under `results/data/`.
