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
    confidence: float                 # 0.0–1.0
    axes: list[AxisVerdict]           # the per-dimension breakdown
    rationale: str                    # 1–3 sentence dossier summary
    narrative: str                    # 2–4 paragraph prose for the report
    validation_flags: list[ValidationFlag]
    # + run_id, trace_id, target_gene, disease, direction

class AxisVerdict(BaseModel):
    axis: str                         # e.g. "causality", "toxicity"
    verdict: bool | None              # True=favourable, False=unfavourable, None=uncertain
    confidence: float
    rationale: str
    supporting_claim_ids: list[str]   # claim IDs grounding this axis
```

The `overall_verdict` vocabulary — `support` / `oppose` / `neutral` /
`insufficient_evidence` — is shared with the reconciler. Note `insufficient_evidence` is a
distinct, first-class outcome: it means "no evidence of this lens's type passed screening,"
**not** a negative finding.

### Per-lens axes and conventions

Axis names are produced by the LLM under each lens's skill prompt (not a code enum). The
canonical axes each skill defines:

| Lens | Axes | Notable conventions (verified in the skill / agent) |
|---|---|---|
| genetics | `causality`, `genetic_validity` | Pathogenic/Likely-Pathogenic **ClinVar** variants are routed to the **causality** axis as *primary* evidence for Mendelian disease (even when tagged constraint-type); ≥2 P/LP (gold-stars ≥1) ⇒ `causality.verdict=true`. Absence of GWAS never lowers a causality verdict already supported by Mendelian/ClinVar variants. A deterministic **Mendelian-causality floor** can override the LLM (see below). **Disease-class-aware** (see below): e.g. for oncology indications the absence of germline signal is *not* penalized. When the gene has **no global missense constraint**, a deterministic missense-framing note (`missense_framing_note`) forbids "missense-intolerant" wording; for a gain-of-function gene it explains that pathogenic missense variants act at specific residues, names LoF tolerance as the favourable signal for inhibition, and gives the recommended sentence, which the `constraint` guard accepts. |
| biology | `druggability`, `mechanism_of_action`, `developability` | **Disease-class-aware** DepMap reading: near-zero dependency across cancer lines counts for an oncology indication but is *not* held against a non-oncology one. `developability` reasons over UniProt subcellular localization (biologic epitope accessibility) and ChEMBL clinical-candidate progression (chemical-matter existence proof); it is a secondary signal. The overall verdict is computed from the axes (`settle_biology_verdict`): oppose only when `mechanism_of_action` is unfavourable, or `perturbation_direction` is and the human genetics do not support the direction; support when every scored axis is favourable; neutral otherwise. Pan-essentiality is reported with its numbers but weighed by the safety lens and the dependency gates, not here. |
| safety | `toxicity`, `tissue_specificity` | **Polarity is inverted on `toxicity`**: `verdict=true` means the safety profile is acceptable (not a liability); `verdict=false` means there *is* a safety concern. (Skill: `safety_lens.md`.) |
| clinical | `clinical_precedent`, `clinical_validation` | Reasons over clinical-trial evidence only. The prompt carries exact per-phase registry counts with NCT ids and statuses (`trial_count_prompt`), so the prose quotes numbers rather than "multiple trials"; the `clinical_trial` and `quantity` guards check them. |
| commercial | `ip_landscape`, `competitive_opportunity` | Reads patents + the approved-drug (FDA label) landscape; no literature. Target IP is kept apart from indication-only patents. When indication-level competition counts were retrieved, the framing states that the indication is **contested**, bans field-level "underserved/whitespace" wording, and supplies a target-scoped sentence to quote ("underserved at the target level …, but <indication> itself is contested, with N approved drugs and M active trials"). |
| regulatory | `approval_precedent`, `label_safety`, `regulatory_de_risking` | Reads FDA label/regulatory evidence only. |

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
| `tissue_relevance` | a high-bulk-TPM, non-disease tissue called disease-relevant | biology, safety |
| `dependency_relevance` | cancer-cell-line dependency cited as support for a non-oncology target | biology |
| `quantity` | vague quantifiers that contradict a measured quantity's band (e.g. "some cell lines" for 2/512) | biology, safety, clinical |

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
score (0–100)**. This comes from the **experiment/scoring path** (the `experiment` node), not
from collapsing the lens verdicts. The score can be clamped upward by a deterministic
**Mendelian-causality floor** (`get_mendelian_score_floor`, default 70, from
[config/scoring.yaml](config/scoring.yaml)) when the target's genetics is Mendelian-grade —
the floor never lowers a score the model already set higher
([services/decision/suitability.py](src/services/decision/suitability.py)). So a run yields
**both**: a consensus *verdict* (with named conflicts) and a single suitability *number*.

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
