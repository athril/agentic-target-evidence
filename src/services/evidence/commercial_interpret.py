# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic commercial-landscape framing and lens-output guards.

Pure module — no I/O, fully unit-testable.

    drug/trial counts ──▶ interpret_competitive_landscape() ──▶ prompt framing
    LLM narrative ──────▶ apply_commercial_guards() ──────────▶ annotated text

Mirrors the constraint / clinical-trial guard modules: pre-compute the correct
framing so the LLM has nothing to overstate (Step 1), then annotate — never
silently rewrite — the residual overstatements on the parsed verdict (Step 2).

Reference errors (TRPC6 × FSGS report):
  C1: "there are no known drugs targeting TRPC6 for FSGS" — too strong. The
      retrieval covers APPROVED + clinical-stage programs only; it cannot rule
      out preclinical/discovery work. Accurate form: "no APPROVED TRPC6-targeted
      therapy for FSGS". Approved vs. clinical-candidate vs. preclinical are
      distinct commercial claims.
  C2: "competitive field appears underserved" — target-level whitespace is not
      indication-level whitespace. FSGS overall is commercially contested
      (endothelin antagonists, APOL1 inhibitors, complement inhibitors,
      anti-fibrotics, immunomodulators); TRPC6 specifically may be less crowded.
      Scope "underserved/uncrowded" to the TARGET, never the indication.
      UPDATE: indication-level competition is now *retrieved*, not merely
      asserted-against — `fetch_indication_competition` queries OpenFDA labels
      + ClinicalTrials.gov by indication/condition, any mechanism, and
      `interpret_competitive_landscape`/`apply_commercial_guards` below cite the
      real approved-drug/active-trial counts when present. A zero/unmapped count
      still falls back to the old "we couldn't see it" caution — it is a weak
      signal (query miss, niche disease, registry lag), not proof of whitespace.
  C3: "market size is unknown" — Orphanet and GBD are each one source, not the
      only ones. Orphanet's bulk dataset covers rare/genetic diseases by
      design; GBD is whole-population but depends on a confident disease →
      GBD-cause mapping that can miss. Absence from either is not "unknown":
      published epidemiological prevalence estimates routinely exist outside
      both (FSGS ≈ 2-9 / 100,000). Say "not sizeable from Orphanet/GBD", not
      "unknown".
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Pre-computed prompt framing
# ---------------------------------------------------------------------------


def interpret_competitive_landscape(
    approved_count: int,
    phase3_count: int,
    known_drugs_count: int,
    trial_count: int,
    indication_approved_drug_count: int = 0,
    indication_active_trial_count: int = 0,
    indication_phase3_trial_count: int = 0,
    indication_total_trial_count: int = 0,
) -> str:
    """Return competitive-landscape framing injected into the commercial-lens prompt.

    Encodes two distinctions the LLM otherwise blurs (see C1/C2 above): the
    approved/clinical/preclinical drug-stage ladder, and target-level vs.
    indication-level competition. Counts only flavour the wording — the
    distinctions are stated regardless.

    The four ``indication_*`` counts come from `fetch_indication_competition`
    (OpenFDA + ClinicalTrials.gov queried by indication, any mechanism). When
    present they replace the old "we can't see it" caveat with the actual
    numbers (C2); when absent (query miss / unmapped indication) the caveat
    is kept — absence is not proof the indication is uncontested.
    """
    lines = ["Competitive-landscape framing (pre-computed — apply these distinctions):"]

    if known_drugs_count or approved_count or phase3_count:
        lines.append(
            f"  This target has {known_drugs_count} known drug(s) in Open Targets "
            f"({approved_count} approved, {phase3_count} in Phase 3). Do NOT write "
            f"'no drugs target this gene' — that contradicts the retrieved data."
        )
    else:
        lines.append(
            "  No approved or clinical-stage drugs targeting this gene were retrieved. "
            "State this as 'no approved or clinical-stage therapy in the retrieved evidence', "
            "NOT 'no drugs exist' — preclinical/discovery programs are not captured by this "
            "data, so absence here is not proof of none."
        )

    lines.append(
        "  Drug-stage ladder: always distinguish APPROVED therapies vs. CLINICAL-stage "
        "candidates vs. PRECLINICAL programs — these are different commercial claims. "
        "Prefer 'no approved <gene>-targeted therapy for <indication>' over the absolute "
        "'no drugs targeting <gene>', which the evidence cannot support."
    )

    if indication_approved_drug_count or indication_total_trial_count:
        lines.append(
            f"  Indication-level competition (target-agnostic): this indication has "
            f"{indication_approved_drug_count} approved drug(s) and "
            f"{indication_active_trial_count}/{indication_total_trial_count} active trial(s) "
            f"({indication_phase3_trial_count} in Phase 3). Few programs against THIS target "
            f"does not make the indication underserved — these competing programs do. Scope "
            f"any whitespace claim to the target, citing these indication-level numbers."
        )
    else:
        lines.append(
            "  Target-level whitespace is NOT indication-level whitespace: few programs against "
            "THIS target does not make the INDICATION commercially underserved. The indication "
            "may be contested by competing mechanisms (other drug classes addressing the same "
            "disease) that this target-centric retrieval does not enumerate. Scope any "
            "'underserved'/'uncrowded' claim to the TARGET, never the indication/field."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output guards
# ---------------------------------------------------------------------------

_DRUG_NOUN = (
    r"(?:drugs?|therap(?:y|ies|eutics?)|treatments?|agents?|compounds?|inhibitors?|"
    r"modulators?|antagonists?|agonists?|programs?|candidates?|medicines?|molecules?)"
)

# "no <qualifier>? <drug-noun>" — a blanket-absence claim about drugs for the target.
# The qualifier slot deliberately does NOT include "approved"/"marketed": those make
# the claim correct and must not be flagged (handled by `_APPROVED_NEARBY` below).
_NO_DRUGS_PATTERN = re.compile(
    r"\bno\s+(?:known\s+|existing\s+|current(?:ly)?\s+|available\s+|other\s+){0,2}" + _DRUG_NOUN,
    re.IGNORECASE,
)
# When "approved"/"marketed"/"licensed"/"on the market" sits just before the matched
# drug-noun, the claim is the accurate "no approved therapy" form — leave it alone.
_APPROVED_NEARBY = re.compile(
    r"\b(approved|marketed|licen[cs]ed|on\s+the\s+market|fda[- ]approved)\b", re.IGNORECASE
)
_APPROVED_LOOKBACK_CHARS = 32

# Indication/target-level whitespace overstatement — strong "uncrowded" language.
_UNDERSERVED_PATTERN = re.compile(
    r"\b(underserved|under-served|white[\s-]?space|uncrowded|"
    r"not\s+(?:very\s+|particularly\s+)?crowded|little\s+competition|"
    r"no\s+(?:direct\s+)?competition|wide[\s-]?open|commercially\s+open)\b",
    re.IGNORECASE,
)
# Only fire the underserved guard when the claim is near a scope noun that signals it
# is talking about the field/market/indication (not, e.g., a narrow target statement
# the user said is acceptable).
_SCOPE_NOUN_PATTERN = re.compile(
    r"\b(field|indication|market|landscape|space|area|competition|competitive|disease)\b",
    re.IGNORECASE,
)
_SCOPE_WINDOW_CHARS = 60

# Market-size declared unknown / unsizeable in absolute terms. Two shapes share the
# subject noun: positive linking verb + negative adjective ("size is unknown"), or a
# negative modal + participle ("prevalence could not be determined").
_MARKET_SUBJECT = (
    r"(?:market\s+size|addressable\s+(?:population|market)|patient\s+population|prevalence|"
    r"epidemiolog\w+)"
)
_MARKET_UNKNOWN_PATTERN = re.compile(
    r"\b" + _MARKET_SUBJECT + r"\s+(?:"
    r"(?:is|are|was|remains?)\s+(?:\w+\s+){0,2}?"
    r"(?:unknown|undetermined|unclear|unquantified|unestablished|"
    r"not\s+(?:known|available|established|quantified|determined))"
    r"|"
    r"(?:cannot|could\s+not|can\s+not|can'?t)\s+be\s+(?:\w+\s+){0,2}?"
    r"(?:sized|determined|quantified|estimated|established|assessed|known)"
    r")",
    re.IGNORECASE,
)
# The skill RECOMMENDS "could not be sized from Orphanet/GBD" — that correctly-scoped
# phrasing names its source and must never be flagged.
_SOURCE_SCOPED = re.compile(r"\b(orphanet|gbd|this\s+source|from\s+this)\b", re.IGNORECASE)
_SOURCE_SCOPE_WINDOW = 40


def _has_scope_noun_nearby(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - _SCOPE_WINDOW_CHARS) : min(len(text), end + _SCOPE_WINDOW_CHARS)]
    return bool(_SCOPE_NOUN_PATTERN.search(window))


def apply_commercial_guards(
    text: str,
    *,
    known_drugs_count: int = 0,
    approved_count: int = 0,
    indication_approved_drug_count: int = 0,
    indication_active_trial_count: int = 0,
) -> str:
    """Annotate (never silently rewrite) commercial-lens overstatements.

    Three deterministic checks, each appending a ``[⚠ COMMERCIAL GUARD: …]`` note:

      A. **Blanket no-drugs claim** — "no (known) drugs/therapies/programs …" without
         the "approved" qualifier. The retrieval only covers approved + clinical-stage
         programs, so an absolute claim of no drugs overstates the evidence and (when
         drugs are known) may flatly contradict it.
      B. **Indication underserved** — "underserved"/"uncrowded"/"whitespace" applied to
         the field/market/indication. Target-level whitespace ≠ indication-level
         whitespace. When `indication_approved_drug_count`/`indication_active_trial_count`
         are non-zero (a real `fetch_indication_competition` hit), the annotation cites
         them as a direct contradiction rather than the generic "can't confirm" caution —
         the guard still fires either way; low/zero counts are a weak signal (query miss,
         niche disease), not a license to call the field open.
      C. **Market size unknown** — "market size is unknown" / "prevalence could not be
         sized". Orphanet's silence on a non-rare indication, or GBD's silence from
         an unconfident cause mapping, is not "unknown"; published epidemiological
         estimates may exist outside both.

    Mirrors `apply_constraint_guards` / `apply_clinical_phase_guard`: annotate, never
    silently rewrite; safe on empty inputs.
    """
    if not text:
        return text

    notes: list[str] = []

    # --- Check A: blanket no-drugs claim -----------------------------------
    for m in _NO_DRUGS_PATTERN.finditer(text):
        preceding = text[max(0, m.start() - _APPROVED_LOOKBACK_CHARS) : m.end()]
        if _APPROVED_NEARBY.search(preceding):
            continue  # "no approved drugs …" is the accurate form
        if known_drugs_count or approved_count:
            contradiction = (
                f" Open Targets records {known_drugs_count} known drug(s) "
                f"({approved_count} approved) for this gene — a blanket 'no drugs' claim "
                f"contradicts the retrieved data."
            )
        else:
            contradiction = (
                " No approved or clinical-stage drug was retrieved, but the evidence covers "
                "approved + clinical-stage programs only and cannot rule out preclinical work."
            )
        notes.append(
            "[⚠ COMMERCIAL GUARD: text asserts a blanket absence of drugs/programs for this "
            "target without the 'approved' qualifier. Distinguish APPROVED therapies vs. "
            "CLINICAL-stage candidates vs. PRECLINICAL programs — write 'no approved "
            "<gene>-targeted therapy', not 'no drugs targeting <gene>'." + contradiction + "]"
        )
        break  # one annotation is enough to surface the overstatement

    # --- Check B: indication-level underserved overstatement ---------------
    for m in _UNDERSERVED_PATTERN.finditer(text):
        if not _has_scope_noun_nearby(text, m.start(), m.end()):
            continue
        if indication_approved_drug_count or indication_active_trial_count:
            notes.append(
                "[⚠ COMMERCIAL GUARD: 'underserved'/'uncrowded' applied to the indication, "
                f"but the indication has {indication_approved_drug_count} approved drug(s) "
                f"and {indication_active_trial_count} active trial(s) — it is demonstrably "
                "contested. Scope any whitespace claim to the target.]"
            )
        else:
            notes.append(
                "[⚠ COMMERCIAL GUARD: an 'underserved'/'uncrowded' claim is applied to the "
                "field/indication. Target-level whitespace is NOT indication-level whitespace — "
                "few programs against THIS target does not make the indication commercially "
                "underserved; competing mechanisms (other drug classes) may contest it without "
                "appearing in this target-centric retrieval. Scope the claim to the target.]"
            )
        break

    # --- Check C: market size declared unknown -----------------------------
    market_unknown = False
    for m in _MARKET_UNKNOWN_PATTERN.finditer(text):
        trailing = text[m.end() : m.end() + _SOURCE_SCOPE_WINDOW]
        if _SOURCE_SCOPED.search(trailing):
            continue  # "could not be sized from Orphanet" is correctly scoped
        market_unknown = True
        break
    if market_unknown:
        notes.append(
            "[⚠ COMMERCIAL GUARD: market size is declared unknown/unsizeable. Orphanet and GBD "
            "are each one source, not the only ones — Orphanet's bulk dataset covers "
            "rare/genetic diseases by design, and GBD depends on a confident disease → "
            "GBD-cause mapping that can miss, so absence from either is not 'unknown'. "
            "Published epidemiological prevalence estimates frequently exist in the wider "
            "literature even when neither has a record. Say 'not sizeable from Orphanet/GBD' "
            "and note that external estimates may exist, rather than asserting the market size "
            "is 'unknown'.]"
        )

    if not notes:
        return text
    seen: set[str] = set()
    unique = []
    for n in notes:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return "\n".join([text, *unique])
