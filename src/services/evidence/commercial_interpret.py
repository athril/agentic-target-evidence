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

from services.evidence.guard_core import (
    Violation,
    annotate,
    is_negated,
    iter_clauses,
    strip_annotations,
)

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
    gene: str = "",
    disease: str = "",
    target_trials: list[str] | None = None,
) -> str:
    """Return competitive-landscape framing injected into the commercial-lens prompt.

    ``target_trials`` lists retrieved phased trials whose record names the target
    (one line each). They are clinical-stage programs against the target even when
    Open Targets' known-drug list misses them (BI 764198 for TRPC6 was in a phase 3
    trial with 0 Open Targets known drugs), so they count as clinical-stage too.

    Encodes two distinctions the LLM otherwise blurs (see C1/C2 above): the
    approved/clinical/preclinical drug-stage ladder, and target-level vs.
    indication-level competition. Counts only flavour the wording — the
    distinctions are stated regardless.

    The four ``indication_*`` counts come from `fetch_indication_competition`
    (OpenFDA + ClinicalTrials.gov queried by indication, any mechanism). When
    present they replace the old "we can't see it" caveat with the actual
    numbers (C2) and a pre-computed "contested" verdict with a ready-to-quote
    sentence that `check_commercial_guards` accepts — stating the numbers alone
    let the LLM still write "the competitive field is underserved … though there
    are 38 active trials". When absent (query miss / unmapped indication) the
    caveat is kept — absence is not proof the indication is uncontested.
    """
    lines = ["Competitive-landscape framing (pre-computed — apply these distinctions):"]
    target_trials = target_trials or []
    has_clinical_stage = bool(known_drugs_count or approved_count or phase3_count or target_trials)

    if known_drugs_count or approved_count or phase3_count:
        lines.append(
            f"  This target has {known_drugs_count} known drug(s) in Open Targets "
            f"({approved_count} approved, {phase3_count} in Phase 3). Do NOT write "
            f"'no drugs target this gene' — that contradicts the retrieved data."
        )
    if target_trials:
        lines.append(
            f"  Clinical-stage programs against this target (retrieved trials naming "
            f"{gene or 'the target'}): "
            + "; ".join(target_trials)
            + ". These are target-level competitors: do NOT write that no clinical-stage "
            "drug targets this gene, and do not call the target uncontested."
        )
    if not has_clinical_stage:
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
        indication = disease or "the indication"
        contested = (
            f"{indication} itself is contested, with {indication_approved_drug_count} approved "
            f"drug(s) and {indication_active_trial_count} active trial(s) across all mechanisms"
        )
        if has_clinical_stage:
            example = f"{contested[0].upper()}{contested[1:]}."
        else:
            target = f"{gene}-targeted therapies" if gene else "Target-specific therapies"
            example = (
                f"{target} are underserved at the target level (no approved or "
                f"clinical-stage {gene or 'target'}-directed drug was retrieved), but {contested}."
            )
        lines.append(
            f"  Indication-level competition (target-agnostic): this indication has "
            f"{indication_approved_drug_count} approved drug(s) and "
            f"{indication_active_trial_count}/{indication_total_trial_count} active trial(s) "
            f"({indication_phase3_trial_count} in Phase 3). VERDICT: the indication is "
            f"CONTESTED, not underserved — few programs against THIS target does not make the "
            f"indication underserved. Never call the competitive field, market, landscape, "
            f"space or indication 'underserved', 'uncrowded', 'whitespace' or 'wide open'. "
            f'Write the competitive point in this form: "{example}"'
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
    r"no\s+(?:direct\s+)?competition|wide[\s-]?open|commercially\s+open|"
    r"free\s+(?:from|of)\s+(?:direct\s+)?competi\w+)\b",
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

# Explicit TARGET scope on the whitespace claim itself — the phrasing the prompt and
# this guard's own note ask for ("scope any whitespace claim to the target"). Only
# counted in the clause *head* (before a justification connective), so "the field is
# underserved, as no drugs targeting TRPC6 were found" — target-level evidence used to
# justify a field-level claim, the exact C2 error — is still flagged.
_TARGET_SCOPE_PATTERN = re.compile(
    r"\bat\s+the\s+target[\s-]+level\b|\btarget[\s-]+level\b|"
    r"\b(?:this|the)\s+(?:specific\s+)?(?:target|gene)\b|"
    r"\bgene[\s-]+target\w*|\w+-targeted\b|\btarget-specific\b",
    re.IGNORECASE,
)
# Indication-level scope stated alongside the target qualifier: "underserved at the
# target level and across the indication" is still an indication claim.
_INDICATION_SCOPE_PATTERN = re.compile(
    r"\b(?:indication|disease\s+area|therapeutic\s+area|overall\s+(?:market|field)|"
    r"broader\s+(?:market|field|space))\b",
    re.IGNORECASE,
)
# Where the whitespace claim ends and its justification begins.
_JUSTIFICATION_PATTERN = re.compile(
    r"\s+(?:with\s+no|as\s+(?:no|there|none)|because|since|given|due\s+to|owing\s+to)\b"
    r"|,\s*(?:as|with|since|given|because)\b",
    re.IGNORECASE,
)

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
# phrasing names its source and must never be flagged. The source may be named before
# the subject ("Orphanet prevalence is unknown") or after it, anywhere in the clause.
_SOURCE_SCOPED = re.compile(r"\b(orphanet|gbd|this\s+source|from\s+this)\b", re.IGNORECASE)
# Rarity claims for the market (PCSK9 × MI: "The market size is ultra-rare, as there are no
# Orphanet prevalence records available for myocardial infarction").
_ULTRA_RARE = re.compile(r"\bultra[- ]?rare\b", re.IGNORECASE)
_RARE = re.compile(r"\b(?:ultra[- ]?)?rare\b(?![- ]disease\s+threshold)", re.IGNORECASE)
_ABSENCE_OF_RECORD = re.compile(
    r"\b(?:no|absence\s+of|lack\s+of|missing|without)\b[^.;]*?\b(?:records?|prevalence|"
    r"data|estimates?)\b",
    re.IGNORECASE,
)

COMMERCIAL_GUARD_MARKER = "COMMERCIAL GUARD"


def _has_scope_noun_nearby(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - _SCOPE_WINDOW_CHARS) : min(len(text), end + _SCOPE_WINDOW_CHARS)]
    return bool(_SCOPE_NOUN_PATTERN.search(window))


def _clause_head(clause: str) -> str:
    """The claim part of a clause — everything before its first justification connective."""
    m = _JUSTIFICATION_PATTERN.search(clause)
    return clause[: m.start()] if m else clause


# "drugs/therapies targeting this gene" describes the EVIDENCE (which programs were
# found), not the scope of the whitespace claim — "the lack of drugs targeting this gene
# indicates an underserved space" is still a field-level inference.
_EVIDENCE_OBJECT_BEFORE = re.compile(
    _DRUG_NOUN + r"\s+(?:\w+\s+){0,2}?(?:targeting|against|that\s+target|directed\s+at)\s+$",
    re.IGNORECASE,
)


def _is_target_scoped(clause: str, gene: str) -> bool:
    """True when the whitespace claim in ``clause`` is explicitly scoped to the target."""
    head = _clause_head(clause)
    if _INDICATION_SCOPE_PATTERN.search(head):
        return False
    markers = list(_TARGET_SCOPE_PATTERN.finditer(head))
    if gene:
        markers += list(re.finditer(rf"\b{re.escape(gene)}\b", head, re.IGNORECASE))
    return any(not _EVIDENCE_OBJECT_BEFORE.search(head[: m.start()]) for m in markers)


def check_commercial_guards(
    text: str,
    *,
    known_drugs_count: int = 0,
    approved_count: int = 0,
    indication_approved_drug_count: int = 0,
    indication_active_trial_count: int = 0,
    gene: str = "",
    indication_prevalence_found: bool = True,
) -> list[Violation]:
    """Structured commercial-lens findings for ``text`` (see `apply_commercial_guards`)."""
    body = strip_annotations(text)
    if not body:
        return []
    clauses = iter_clauses(body)
    found: list[Violation] = []

    # --- Check A: blanket no-drugs claim -----------------------------------
    for clause in clauses:
        hit = None
        for m in _NO_DRUGS_PATTERN.finditer(clause):
            preceding = clause[max(0, m.start() - _APPROVED_LOOKBACK_CHARS) : m.end()]
            if _APPROVED_NEARBY.search(preceding):
                continue  # "no approved drugs …" is the accurate form
            hit = m
            break
        if hit is None:
            continue
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
        found.append(
            Violation(
                rule_id="commercial.no_drugs",
                kind="contradiction" if (known_drugs_count or approved_count) else "overstatement",
                excerpt=clause,
                note=(
                    f"[⚠ {COMMERCIAL_GUARD_MARKER}: text asserts a blanket absence of "
                    "drugs/programs for this target without the 'approved' qualifier. "
                    "Distinguish APPROVED therapies vs. CLINICAL-stage candidates vs. "
                    "PRECLINICAL programs — write 'no approved <gene>-targeted therapy', not "
                    "'no drugs targeting <gene>'." + contradiction + "]"
                ),
            )
        )
        break  # one annotation is enough to surface the overstatement

    # --- Check B: indication-level underserved overstatement ---------------
    for clause in clauses:
        hit = None
        for m in _UNDERSERVED_PATTERN.finditer(clause):
            if is_negated(clause, m.start()):
                continue  # "not underserved" denies whitespace
            if not _has_scope_noun_nearby(clause, m.start(), m.end()):
                continue
            if _is_target_scoped(clause, gene):
                continue  # whitespace claim explicitly scoped to the target — compliant
            hit = m
            break
        if hit is None:
            continue
        if indication_approved_drug_count or indication_active_trial_count:
            found.append(
                Violation(
                    rule_id="commercial.underserved",
                    kind="contradiction",
                    excerpt=clause,
                    note=(
                        f"[⚠ {COMMERCIAL_GUARD_MARKER}: 'underserved'/'uncrowded' applied to "
                        f"the indication, but the indication has {indication_approved_drug_count} "
                        f"approved drug(s) and {indication_active_trial_count} active trial(s) — "
                        "it is demonstrably contested. Scope any whitespace claim to the target.]"
                    ),
                )
            )
        else:
            found.append(
                Violation(
                    rule_id="commercial.underserved",
                    kind="overstatement",
                    excerpt=clause,
                    note=(
                        f"[⚠ {COMMERCIAL_GUARD_MARKER}: an 'underserved'/'uncrowded' claim is "
                        "applied to the field/indication. Target-level whitespace is NOT "
                        "indication-level whitespace — few programs against THIS target does not "
                        "make the indication commercially underserved; competing mechanisms "
                        "(other drug classes) may contest it without appearing in this "
                        "target-centric retrieval. Scope the claim to the target.]"
                    ),
                )
            )
        break

    # --- Check C: market size declared unknown -----------------------------
    for clause in clauses:
        if not _MARKET_UNKNOWN_PATTERN.search(clause) or _SOURCE_SCOPED.search(clause):
            continue  # "could not be sized from Orphanet" / "Orphanet prevalence…" are scoped
        found.append(
            Violation(
                rule_id="commercial.market_unknown",
                kind="overstatement",
                excerpt=clause,
                note=(
                    f"[⚠ {COMMERCIAL_GUARD_MARKER}: market size is declared unknown/unsizeable. "
                    "Orphanet and GBD are each one source, not the only ones — Orphanet's bulk "
                    "dataset covers rare/genetic diseases by design, and GBD depends on a "
                    "confident disease → GBD-cause mapping that can miss, so absence from either "
                    "is not 'unknown'. Published epidemiological prevalence estimates frequently "
                    "exist in the wider literature even when neither has a record. Say 'not "
                    "sizeable from Orphanet/GBD' and note that external estimates may exist, "
                    "rather than asserting the market size is 'unknown'.]"
                ),
            )
        )
        break

    # --- Check D: rarity without an indication-level prevalence -------------
    # "Ultra-rare" is a prevalence band; with no record naming the indication there is no
    # band to quote. And rarity inferred from a missing record is never valid, whatever
    # else was retrieved ("rare, as there are no Orphanet records").
    for clause in clauses:
        ultra = [m for m in _ULTRA_RARE.finditer(clause) if not is_negated(clause, m.start())]
        rare = [m for m in _RARE.finditer(clause) if not is_negated(clause, m.start())]
        inferred = bool(rare) and bool(_ABSENCE_OF_RECORD.search(clause))
        if not (inferred or (ultra and not indication_prevalence_found)):
            continue
        found.append(
            Violation(
                rule_id="commercial.rarity_unsupported",
                kind="contradiction",
                excerpt=clause,
                note=(
                    f"[⚠ {COMMERCIAL_GUARD_MARKER}: no prevalence record names the "
                    "indication, so its population cannot be called rare or ultra-rare — a "
                    "missing Orphanet record is expected for a common disease. Say the market "
                    "could not be sized from Orphanet/GBD.]"
                    if not indication_prevalence_found
                    else f"[⚠ {COMMERCIAL_GUARD_MARKER}: rarity is inferred from a missing "
                    "record; absence from Orphanet/GBD says nothing about how common the "
                    "indication is.]"
                ),
            )
        )
        break

    # --- Check E: small-market claims without an indication-level prevalence -----
    # PCSK9 × MI: "the market size for myocardial infarction, as indicated by Orphanet
    # prevalence records, does not provide a large addressable population … orphan drug
    # pathways". With no record naming the indication there is no basis for "small".
    if not indication_prevalence_found:
        for clause in clauses:
            small = _SMALL_MARKET.search(clause)
            if small is None or is_negated(clause, small.start()):
                continue
            found.append(
                Violation(
                    rule_id="commercial.small_market_unsupported",
                    kind="contradiction",
                    excerpt=clause,
                    note=(
                        f"[⚠ {COMMERCIAL_GUARD_MARKER}: no prevalence record names the "
                        "indication, so nothing supports a small or orphan-scale population; "
                        "Orphanet covers rare diseases only. Say the market could not be sized "
                        "from Orphanet/GBD.]"
                    ),
                )
            )
            break
    return found


_SMALL_MARKET = re.compile(
    r"\b(?:not\s+(?:a\s+)?large(?:\s+enough)?|does\s+not\s+provide\s+a\s+large|small(?:er)?\s+"
    r"(?:addressable\s+)?(?:market|population|patient)|limited\s+(?:addressable\s+)?"
    r"(?:market|population|patient)|few\s+patients|orphan(?:[- ]drug)?\s+(?:pathways?|"
    r"designation|strateg\w*|pricing))",
    re.IGNORECASE,
)


def apply_commercial_guards(
    text: str,
    *,
    known_drugs_count: int = 0,
    approved_count: int = 0,
    indication_approved_drug_count: int = 0,
    indication_active_trial_count: int = 0,
    gene: str = "",
) -> str:
    """Annotate (never silently rewrite) commercial-lens overstatements.

    Three deterministic checks, each appending a ``[⚠ COMMERCIAL GUARD: …]`` note:

      A. **Blanket no-drugs claim** — "no (known) drugs/therapies/programs …" without
         the "approved" qualifier. The retrieval only covers approved + clinical-stage
         programs, so an absolute claim of no drugs overstates the evidence and (when
         drugs are known) may flatly contradict it.
      B. **Indication underserved** — "underserved"/"uncrowded"/"whitespace" applied to
         the field/market/indication. Target-level whitespace ≠ indication-level
         whitespace. A claim explicitly scoped to the target in its own clause ("the
         field is underserved at the target level", "TRPC6-targeted therapies are
         underserved") is the compliant form and is not flagged; ``gene`` lets a bare
         gene-symbol qualifier count as target scope. When
         `indication_approved_drug_count`/`indication_active_trial_count` are non-zero
         (a real `fetch_indication_competition` hit), the annotation cites them as a
         direct contradiction rather than the generic "can't confirm" caution.
      C. **Market size unknown** — "market size is unknown" / "prevalence could not be
         sized". Orphanet's silence on a non-rare indication, or GBD's silence from
         an unconfident cause mapping, is not "unknown"; published epidemiological
         estimates may exist outside both. A clause naming its source is compliant.

    Mirrors `apply_constraint_guards` / `apply_clinical_phase_guard`: annotate, never
    silently rewrite; safe on empty inputs.
    """
    if not text:
        return text
    return annotate(
        text,
        check_commercial_guards(
            text,
            known_drugs_count=known_drugs_count,
            approved_count=approved_count,
            indication_approved_drug_count=indication_approved_drug_count,
            indication_active_trial_count=indication_active_trial_count,
            gene=gene,
        ),
    )
