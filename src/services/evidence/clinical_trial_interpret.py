# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic clinical-trial fact extraction and lens-output guards.

Pure module — no I/O, fully unit-testable.

    Evidence.extra (per trial) ──▶ build_trial_facts() ──▶ list[TrialFact]
    list[TrialFact] + LLM narrative ──▶ apply_clinical_phase_guard() ──▶ annotated text

Each registry trial has exactly ONE phase and ONE recruitment status. The clinical
lens LLM is prone to *conflating* distinct trials — e.g. reporting "two Phase 3
trials (NCT-A and NCT-B)" when one is Phase 2, or calling a COMPLETED trial
"recruiting". The per-trial phase/status are authoritative structured fields, so
these errors are deterministically detectable. This guard annotates them (never
silently rewrites) the same way `apply_constraint_guards` annotates inverted
gnomAD constraint bands.

Reference error (TRPC6 × FSGS report):
  "BI 764198 is currently being tested in two Phase 3 clinical trials
   (NCT05213624 and NCT07220083)" — NCT05213624 is PHASE2/COMPLETED, only
   NCT07220083 is PHASE3/RECRUITING. One Phase 3 trial, not two; not all recruiting.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel

from core.evidence_text import is_diagnostic_study, registry_ids
from services.evidence.guard_core import (
    Violation,
    annotate,
    predicate_denied,
    split_sentences,
    strip_annotations,
    unnegated_matches,
)

# ---------------------------------------------------------------------------
# Fact model + extraction
# ---------------------------------------------------------------------------


class ArmResult(BaseModel):
    """One arm's posted primary-outcome comparison against the trial's comparator."""

    arm: str  # arm title, e.g. "BI 764198 20 mg"
    measure: str = ""  # outcome measure title
    p_value: float
    p_text: str  # as posted, e.g. "0.0024" or "<0.001"


class TrialFact(BaseModel):
    """Authoritative per-trial registry facts the guard checks LLM prose against."""

    nct_id: str
    phase: str = ""  # raw registry value, e.g. "PHASE2"
    status: str = ""  # raw registry value, e.g. "COMPLETED"
    phase_numbers: tuple[int, ...] = ()  # normalised, e.g. (2,); () when unphased/NA
    interventions: tuple[str, ...] = ()  # registry intervention names, comparators included
    arm_results: tuple[ArmResult, ...] = ()  # posted primary-outcome arm-vs-comparator p-values
    title: str = ""
    enrollment: int | None = None
    # "gene" or "drug:<name>" — how retrieval found the trial (services/retrieval)
    matched_by: str = "gene"
    # Evidence ids of retrieved documents (publications) whose text names this trial's
    # registry id — the sources besides the registry record that may report its results.
    publication_ids: tuple[str, ...] = ()
    # Registry primary purpose (TREATMENT, PREVENTION, DIAGNOSTIC, SCREENING, …); "" if absent.
    primary_purpose: str = ""

    @property
    def is_therapeutic(self) -> bool:
        """Tests a therapy (or prevention), not a diagnostic/screening agent.

        Imaging and diagnostic studies are registered with a phase for their tracer or
        test (a dopamine-transporter scan, a PET ligand), and name the gene in their
        cohort definition — they are not trials of a modality acting on the target.
        """
        return not is_diagnostic_study(self.primary_purpose, self.interventions)


def normalize_phase(raw: str | None) -> tuple[int, ...]:
    """Parse a registry phase string into the set of phase numbers it names.

    Handles "PHASE2", "PHASE 2", "EARLY_PHASE1", and multi-phase values like
    "PHASE1, PHASE2" / "PHASE2|PHASE3". Returns () for "", "NA", or anything with
    no PHASE<n> token — an unphased trial cannot contradict a stated phase.
    """
    if not raw:
        return ()
    nums = sorted({int(m) for m in re.findall(r"PHASE\s*([1-4])", raw, re.IGNORECASE)})
    return tuple(nums)


def build_trial_facts(evidences: Iterable[object] | None) -> list[TrialFact]:
    """Build TrialFacts from CLINICAL_TRIAL Evidence objects.

    Reads each evidence's ``source`` (NCT id) and ``extra`` phase/status — the
    same authoritative fields the trial markdown and structured claim are built
    from (see ``services/retrieval/clinical_trial._render_markdown`` and
    ``services/evidence/claim_extraction.structured_claims``). Accepts any
    iterable of objects exposing ``.source`` and ``.extra`` so it stays decoupled
    from the Evidence schema; silently skips items missing an NCT id.
    """
    facts: list[TrialFact] = []
    for ev in evidences or []:
        nct = (getattr(ev, "source", "") or "").strip()
        if not nct:
            continue
        extra = getattr(ev, "extra", None) or {}
        phase = (extra.get("phase") or "").strip()
        status = (extra.get("status") or "").strip()
        facts.append(
            TrialFact(
                nct_id=nct,
                phase=phase,
                status=status,
                phase_numbers=normalize_phase(phase),
                interventions=tuple(str(i) for i in extra.get("interventions") or [] if i),
                arm_results=_arm_results(extra.get("results") or []),
                title=str(extra.get("title") or ""),
                enrollment=_int_or_none(extra.get("enrollment")),
                matched_by=str(extra.get("matched_by") or "gene"),
                primary_purpose=str((extra.get("study_plan") or {}).get("primary_purpose") or ""),
            )
        )
    return facts


_COMPARATOR = re.compile(
    r"\b(?:placebo|control|vehicle|sham|standard\s+of\s+care|usual\s+care)\b", re.IGNORECASE
)
_P_VALUE = re.compile(r"^\s*[<≤=]?\s*(\d*\.?\d+(?:[eE]-?\d+)?)\s*$")


def _int_or_none(v: object) -> int | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        return int(str(v).strip())
    except ValueError:
        return None


_STOPPED = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}


def _trial_weight(f: TrialFact) -> tuple[int, int, int, int]:
    """Sort key: late phase, completed, posted results, enrolment (larger first)."""
    # Phase 3 and 4 are one late-phase tier: many phase 4 records are small
    # post-marketing studies that must not outrank a completed outcome trial.
    phase = min(max(f.phase_numbers or (0,)), 3)
    status = f.status.upper()
    done = 2 if status == "COMPLETED" else (0 if status in _STOPPED else 1)
    return (phase, done, 1 if f.arm_results else 0, f.enrollment or 0)


def key_trials_prompt(facts: list[TrialFact], n: int = 6) -> str:
    """Prompt block ranking the retrieved trials by evidential weight.

    The clinical lens led with a small phase 2 and a terminated phase 3 while the
    27,564-patient outcome trial sat unnamed in its context (PCSK9 × MI). Ranking is
    deterministic: phase, completion, posted results, enrolment.
    """
    phased = [f for f in facts if f.phase_numbers and f.is_therapeutic]
    if not phased:
        return ""
    ranked = sorted(phased, key=_trial_weight, reverse=True)[:n]
    lines = [
        "Key registry trials, ranked by evidential weight (late phase, completed, posted "
        "results, enrolment — pre-computed; lead with these, and name a trial's status "
        "when it was terminated or withdrawn):"
    ]
    for f in ranked:
        status = f.status.replace("_", " ").lower() or "status unknown"
        size = f"n={f.enrollment:,}" if f.enrollment else "n unknown"
        via = f"; found via {f.matched_by[5:]}" if f.matched_by.startswith("drug:") else ""
        flag = " — stopped early: weaker evidence" if f.status.upper() in _STOPPED else ""
        lines.append(
            f"  • {f.nct_id} ({f.phase or 'phase n/a'}, {status}, {size}{via}): "
            f"{f.title[:110]}{flag}"
        )
    return "\n".join(lines)


def target_drugs(facts: list[TrialFact]) -> list[str]:
    """Interventions of the retrieved trials other than comparators (placebo, control)."""
    names = [i for f in facts for i in f.interventions if i and not _COMPARATOR.search(i)]
    return list(dict.fromkeys(names))


def _arm_results(results: list[object]) -> tuple[ArmResult, ...]:
    """Arm-vs-comparator p-values from a trial's posted primary outcomes (raw
    ``OutcomeResult`` dicts); analyses without a single non-comparator arm or a
    parseable p-value are skipped."""
    out: list[ArmResult] = []
    for raw in results:
        if not isinstance(raw, dict):
            continue
        for analysis in raw.get("analyses") or []:
            groups = [str(g) for g in analysis.get("groups") or []]
            arms = [g for g in groups if not _COMPARATOR.search(g)]
            m = _P_VALUE.match(str(analysis.get("p_value") or ""))
            if len(arms) != 1 or len(groups) < 2 or not m:
                continue
            out.append(
                ArmResult(
                    arm=arms[0],
                    measure=str(raw.get("title") or ""),
                    p_value=float(m.group(1)),
                    p_text=str(analysis.get("p_value")).strip(),
                )
            )
    return tuple(out)


# ---------------------------------------------------------------------------
# Output guard
# ---------------------------------------------------------------------------

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_COUNT_WORDS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

# Recruitment-status words that may appear in prose, mapped to whether the trial
# is still enrolling (True) or closed to enrollment (False). Used to flag a trial
# described as recruiting when it is actually completed/terminated, and vice versa.
_OPEN_STATUSES = frozenset({"RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"})
_CLOSED_STATUSES = frozenset(
    {"COMPLETED", "TERMINATED", "WITHDRAWN", "SUSPENDED", "ACTIVE_NOT_RECRUITING"}
)
_RECRUITING_WORDS = re.compile(r"\b(recruiting|enrolling|enrollment is open)\b", re.IGNORECASE)
_COMPLETED_WORDS = re.compile(r"\b(completed|concluded|finished)\b", re.IGNORECASE)

# "<count> [filler words] phase <N> ... trials" — a claim that N trials share a phase.
# The count token must not itself be a phase label: in "Phase 2 and ongoing Phase 3
# trials" the "2" names a phase, it does not count Phase 3 trials.
_COUNT_PHASE_TRIALS = re.compile(
    r"(?<!phase\s)\b(two|three|four|five|six|seven|eight|nine|ten|[2-9]|\d{2,})\s+"
    r"(?:[A-Za-z][A-Za-z-]*\s+){0,3}?"
    r"phase\s+(\d+|i{1,3}|iv)\b"
    r"[^.]*?\btrials\b",
    re.IGNORECASE,
)
_PHASE_IN_TEXT = re.compile(r"phase\s+(\d+|i{1,3}|iv)\b", re.IGNORECASE)
_ANY_NCT = re.compile(r"\bNCT\d{8}\b")
_NCT_BEFORE = 80  # chars of left-context to attribute a phase/status to an NCT mention
_NCT_AFTER = 25
# Attribution never crosses a clause or sentence break: in "(NCT-A, recruiting; NCT-B,
# completed)" or "…NCT-A (completed); one Phase 3 trial…" the remark after ";" is not NCT-A's.
_CLAUSE_BREAK = re.compile(r"[;.](?=\s|$)")


def _owned_end(body: str, nct_end: int) -> int:
    """End of an NCT mention's own parenthetical remark, e.g. "NCT07220083, recruiting)"."""
    close = body.find(")", nct_end, nct_end + _NCT_AFTER)
    if close == -1:
        return nct_end
    remark = body[nct_end:close]
    # Not owned when another bracket opens first or another NCT id shares the parenthesis.
    return nct_end if "(" in remark or _ANY_NCT.search(remark) else close + 1


def _phase_token_to_int(tok: str) -> int | None:
    tok = tok.strip().lower()
    if tok.isdigit():
        n = int(tok)
        return n if 1 <= n <= 4 else None
    return _ROMAN.get(tok)


def _count_token_to_int(tok: str) -> int | None:
    tok = tok.strip().lower()
    return int(tok) if tok.isdigit() else _COUNT_WORDS.get(tok)


CLINICAL_GUARD_MARKER = "CLINICAL TRIAL GUARD"


def check_clinical_phase_guard(text: str, facts: list[TrialFact]) -> list[Violation]:
    """Structured per-trial phase/status findings for ``text`` (see `apply_clinical_phase_guard`)."""
    body = strip_annotations(text)
    if not body or not facts:
        return []

    found: list[Violation] = []
    phased = [f for f in facts if f.phase_numbers]

    # --- Check A: phase-count conflation -----------------------------------
    flagged_counts: set[tuple[int, int]] = set()
    for m in _COUNT_PHASE_TRIALS.finditer(body):
        claimed = _count_token_to_int(m.group(1))
        phase_n = _phase_token_to_int(m.group(2))
        if claimed is None or phase_n is None or (claimed, phase_n) in flagged_counts:
            continue
        actual = sum(1 for f in phased if phase_n in f.phase_numbers)
        if claimed > actual:
            flagged_counts.add((claimed, phase_n))
            ids = ", ".join(f.nct_id for f in phased if phase_n in f.phase_numbers) or "none"
            found.append(
                Violation(
                    rule_id="clinical.phase_count",
                    kind="contradiction",
                    excerpt=m.group(0),
                    note=(
                        f"[⚠ {CLINICAL_GUARD_MARKER}: text claims {claimed} Phase {phase_n} "
                        f"trials, but only {actual} of the retrieved trials is Phase {phase_n} "
                        f"({ids}). Each NCT id has exactly one phase — do not aggregate trials "
                        f"of different phases under one phase number.]"
                    ),
                )
            )

    # --- Checks B & C: per-trial phase / status attribution ----------------
    # A trial's attribution window never crosses another NCT mention: in "a Phase 2
    # trial (NCT-A) and a recruiting Phase 3 trial (NCT-B)", "Phase 2" belongs to NCT-A.
    # Text inside an NCT id's own parenthetical ("(NCT-B, recruiting)") belongs to it.
    nct_spans = [m.span() for m in _ANY_NCT.finditer(body)]
    owned_end = {b: _owned_end(body, e) for b, e in nct_spans}
    for f in facts:
        for occ in re.finditer(re.escape(f.nct_id), body):
            prior = [owned_end[b] for b, e in nct_spans if e <= occ.start()]
            lo = max([occ.start() - _NCT_BEFORE, 0, *prior])
            hi = min([occ.end() + _NCT_AFTER, *(b for b, _ in nct_spans if b >= occ.end())])
            lo = max([lo, *(m.end() for m in _CLAUSE_BREAK.finditer(body, lo, occ.start()))])
            brk = _CLAUSE_BREAK.search(body, occ.end(), hi)
            hi = brk.start() if brk else hi
            # An id that opens a parenthetical labels the phrase before it — "Phase 2
            # (NCT-A) and Phase 3 (NCT-B)" — so nothing after its own ")" is NCT-A's.
            if body[: occ.start()].rstrip().endswith("(") and owned_end[occ.start()] > occ.end():
                hi = min(hi, owned_end[occ.start()])
            window = body[lo:hi]

            if f.phase_numbers:
                # The phase nearest before the id labels it ("Phase 2 and Phase 3 trials,
                # including a Phase 2 trial (NCT-A)" says Phase 2 of NCT-A); phases after
                # it, inside its own clause, do too.
                before = list(_PHASE_IN_TEXT.finditer(body, lo, occ.start()))
                tokens = [before[-1].group(1)] if before else []
                tokens += [m.group(1) for m in _PHASE_IN_TEXT.finditer(body, occ.end(), hi)]
                stated = {p for tok in tokens if (p := _phase_token_to_int(tok)) is not None}
                wrong = stated - set(f.phase_numbers)
                if wrong:
                    true_p = "/".join(str(n) for n in f.phase_numbers)
                    bad_p = "/".join(str(n) for n in sorted(wrong))
                    found.append(
                        Violation(
                            rule_id="clinical.trial_phase",
                            kind="contradiction",
                            excerpt=window,
                            note=(
                                f"[⚠ {CLINICAL_GUARD_MARKER}: {f.nct_id} is described as Phase "
                                f"{bad_p}, but the registry records it as "
                                f"{f.phase or f'Phase {true_p}'} (Phase {true_p}).]"
                            ),
                        )
                    )

            su = f.status.upper()
            if su in _CLOSED_STATUSES and _RECRUITING_WORDS.search(window):
                found.append(
                    Violation(
                        rule_id="clinical.trial_status",
                        kind="contradiction",
                        excerpt=window,
                        note=(
                            f"[⚠ {CLINICAL_GUARD_MARKER}: {f.nct_id} is described as "
                            f"recruiting/enrolling, but the registry status is {f.status}. A "
                            f"closed trial is not recruiting.]"
                        ),
                    )
                )
            elif su in _OPEN_STATUSES and _COMPLETED_WORDS.search(window):
                found.append(
                    Violation(
                        rule_id="clinical.trial_status",
                        kind="contradiction",
                        excerpt=window,
                        note=(
                            f"[⚠ {CLINICAL_GUARD_MARKER}: {f.nct_id} is described as completed, "
                            f"but the registry status is {f.status}.]"
                        ),
                    )
                )
            break  # one annotation per trial is enough to surface the contradiction
    return found


def apply_clinical_phase_guard(text: str, facts: list[TrialFact]) -> str:
    """Annotate clinical-lens prose that misstates per-trial phase or status.

    Three deterministic checks, each appending a ``[⚠ CLINICAL TRIAL GUARD: …]``
    note rather than editing the prose:

      A. **Phase-count conflation** — "N Phase X trials" where fewer than N of the
         known trials are actually Phase X.
      B. **Per-trial phase mismatch** — a "Phase X" stated next to an NCT id whose
         true phase is not X.
      C. **Per-trial status mismatch** — an NCT id called "recruiting" when closed,
         or "completed" when still open.

    Vague plural quantifiers ("multiple Phase 3 trials") are checked by the generic
    quantity guard (`services.evidence.readings`), which reads per-phase counts.

    Mirrors `apply_constraint_guards`: annotate, never silently rewrite; safe to
    run on empty inputs.
    """
    if not text or not facts:
        return text
    return annotate(text, check_clinical_phase_guard(text, facts))


def trial_phase_counts(facts: list[TrialFact]) -> dict[int, int]:
    """Number of retrieved therapy trials per phase number (a multi-phase trial counts in
    each; diagnostic/screening studies are not counted — see ``TrialFact.is_therapeutic``)."""
    counts: dict[int, int] = dict.fromkeys(range(1, 5), 0)
    for f in facts:
        if not f.is_therapeutic:
            continue
        for n in f.phase_numbers:
            counts[n] = counts.get(n, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Prompt framing: per-phase counts the lens quotes verbatim
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {n: w for w, n in _COUNT_WORDS.items()} | {1: "one"}

# Registry status → prose the phase/status guard reads as consistent with that status.
# ACTIVE_NOT_RECRUITING must not contain "recruiting": check C would read it as open.
_STATUS_PROSE = {
    "ACTIVE_NOT_RECRUITING": "active, enrollment closed",
    "NOT_YET_RECRUITING": "not yet recruiting",
    "ENROLLING_BY_INVITATION": "enrolling by invitation",
}


def _status_prose(status: str) -> str:
    su = status.strip().upper()
    return _STATUS_PROSE.get(su, su.replace("_", " ").lower())


def trial_count_phrases(facts: list[TrialFact]) -> list[str]:
    """Ready-to-quote count per retrieved phase, e.g. "one Phase 2 trial (NCT05213624, completed)".

    The wording both trial guards accept: an exact count instead of a vague quantifier,
    and each NCT id followed by its own status.
    """
    phrases: list[str] = []
    for n, count in trial_phase_counts(facts).items():
        if not count:
            continue
        trials = "; ".join(
            f"{f.nct_id}, {_status_prose(f.status)}" if f.status else f.nct_id
            for f in facts
            if n in f.phase_numbers and f.is_therapeutic
        )
        noun = "trial" if count == 1 else "trials"
        phrases.append(f"{_NUMBER_WORDS.get(count, str(count))} Phase {n} {noun} ({trials})")
    return phrases


def trial_count_prompt(facts: list[TrialFact]) -> str:
    """Prompt block handing the clinical lens exact per-phase counts to quote verbatim.

    Empty when no registry trial carries a phase — there is nothing to count.
    """
    phrases = trial_count_phrases(facts)
    if not phrases:
        return ""
    counts = trial_phase_counts(facts)
    missing = [f"Phase {n}" for n, c in counts.items() if not c]
    lines = [
        "Registry trials per phase (pre-computed from the ClinicalTrials.gov records "
        "retrieved in this run — quote these counts, NCT ids and statuses verbatim):",
        *(f"  • {p}" for p in phrases),
    ]
    if missing:
        lines.append(f"  • no {' or '.join(missing)} trial retrieved")
    lines.append(
        "Never write 'multiple', 'several', 'many', 'various' or 'a number of' Phase N "
        "trials, and never pool phases under one quantifier ('multiple Phase 2 and Phase 3 "
        "trials') — write each phase's count separately, e.g. "
        f'"{" and ".join(phrases)}". Trials reported only in the literature are not in '
        "these counts; attribute them to their PMID."
    )
    diagnostic = [f for f in facts if f.phase_numbers and not f.is_therapeutic]
    if diagnostic:
        ids = ", ".join(f"{f.nct_id} ({f.primary_purpose.lower()})" for f in diagnostic[:8])
        lines.append(
            f"  • not counted: {len(diagnostic)} diagnostic/screening stud"
            f"{'y' if len(diagnostic) == 1 else 'ies'} registered with a phase for an imaging or "
            f"diagnostic agent ({ids}) — they do not test a therapy on the target; never cite "
            "them as clinical precedent, safety or pharmacodynamic evidence."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Primary-outcome readings: which arms reached significance
# ---------------------------------------------------------------------------

SIGNIFICANCE_ALPHA = 0.05
OUTCOME_GUARD_MARKER = "TRIAL OUTCOME GUARD"

_DOSE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|µg|ug|mcg|g)\b", re.IGNORECASE)


def _arm_best(fact: TrialFact) -> dict[str, ArmResult]:
    """Each arm's strongest (lowest-p) primary-outcome comparison, in posting order."""
    best: dict[str, ArmResult] = {}
    for r in fact.arm_results:
        if r.arm not in best or r.p_value < best[r.arm].p_value:
            best[r.arm] = r
    return best


def _dose(arm: str) -> float | None:
    m = _DOSE.search(arm)
    return float(m.group(1)) if m else None


def _dose_label(arm: str) -> str:
    m = _DOSE.search(arm)
    return f"{m.group(1)} {m.group(2)}" if m else arm


def outcome_split(fact: TrialFact) -> tuple[list[ArmResult], list[ArmResult]]:
    """(arms that reached p < alpha, arms that did not) on the primary outcome."""
    best = list(_arm_best(fact).values())
    return (
        [r for r in best if r.p_value < SIGNIFICANCE_ALPHA],
        [r for r in best if r.p_value >= SIGNIFICANCE_ALPHA],
    )


def significance_not_dose_ordered(fact: TrialFact) -> bool:
    """A higher-dose arm missed significance that a lower-dose arm reached."""
    sig, nonsig = outcome_split(fact)
    sig_doses = [d for d in (_dose(r.arm) for r in sig) if d is not None]
    nonsig_doses = [d for d in (_dose(r.arm) for r in nonsig) if d is not None]
    return bool(sig_doses and nonsig_doses and max(nonsig_doses) > min(sig_doses))


def _outcome_line(fact: TrialFact) -> str:
    sig, nonsig = outcome_split(fact)
    arms = "; ".join(
        f"{r.arm} p={r.p_text}" + (" (significant)" if r.p_value < SIGNIFICANCE_ALPHA else "")
        for r in _arm_best(fact).values()
    )
    line = f"{fact.nct_id} primary outcome vs comparator — {arms}."
    if sig and nonsig:
        line += (
            f" Only {', '.join(_dose_label(r.arm) for r in sig)} reached p<{SIGNIFICANCE_ALPHA}"
            f"; {', '.join(_dose_label(r.arm) for r in nonsig)} did not."
        )
    elif not sig:
        line += f" No arm reached p<{SIGNIFICANCE_ALPHA}."
    if significance_not_dose_ordered(fact):
        line += (
            " Significance does not rise with dose, so no dose-response was shown; with "
            "small arms a non-significant arm is not evidence of no effect — do not infer "
            "a best, minimal or unsafe dose from this pattern."
        )
    return line


def trial_outcome_prompt(facts: list[TrialFact]) -> str:
    """Prompt block giving the clinical lens each trial's per-arm primary-outcome result.

    Empty when no trial posted an arm-vs-comparator p-value.
    """
    lines = [_outcome_line(f) for f in facts if f.arm_results]
    if not lines:
        return ""
    return "\n".join(
        [
            "Posted primary-outcome results per arm (pre-computed from the ClinicalTrials.gov "
            "records — authoritative):",
            *(f"  • {line}" for line in lines),
            "Name the arm(s) that reached significance; never call a trial's result "
            "'significant' without naming them when other arms did not, never write that "
            "dose-level results were not reported, and never call the effect dose-dependent "
            "when significance does not rise with dose.",
        ]
    )


_SIGNIFICANT = re.compile(r"\bsignifican(?:t|tly|ce)\b", re.IGNORECASE)
_NOT_SIGNIFICANT = re.compile(
    r"\b(?:not|non|no|without|failed\s+to\s+(?:reach|achieve))[- ]+(?:statistical(?:ly)?\s+)?"
    r"significan",
    re.IGNORECASE,
)
_DOSE_UNREPORTED = re.compile(
    r"\bdose[- ]?(?:dependent|response|related|level)[^.;]*?\b(?:not|never)\s+(?:been\s+)?"
    r"(?:reported|available|provided|described|posted|shown)",
    re.IGNORECASE,
)
_DOSE_DEPENDENT = re.compile(
    r"\bdose[- ]?(?:dependent|related)\s+(?:reduction|effect|response|improvement|decrease|"
    r"benefit|efficacy)",
    re.IGNORECASE,
)
_NEGATION_BEFORE = re.compile(r"\b(?:no|not|lack(?:ed|s|ing)?\s+of|absence\s+of|without)\b[^,;]*$")
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


def _names_trial(sentence: str, fact: TrialFact) -> bool:
    if fact.nct_id in sentence:
        return True
    low = sentence.lower()
    return any(
        len(i) >= 3 and not _COMPARATOR.search(i) and i.lower() in low for i in fact.interventions
    )


def _names_arm(sentence: str, arm: str) -> bool:
    label = _dose_label(arm)
    return label.lower().replace(" ", "") in sentence.lower().replace(" ", "")


def check_trial_outcome_guard(text: str, facts: list[TrialFact]) -> list[Violation]:
    """Sentences about a trial that misstate which arms reached significance or whether
    per-dose results exist (see `trial_outcome_prompt`)."""
    body = strip_annotations(text)
    found: list[Violation] = []
    for fact in facts:
        sig, nonsig = outcome_split(fact)
        if not fact.arm_results:
            continue
        posted = "; ".join(f"{r.arm} p={r.p_text}" for r in _arm_best(fact).values())
        for sentence in _SENTENCES.split(body):
            if not _names_trial(sentence, fact):
                continue
            if (
                sig
                and nonsig
                and _SIGNIFICANT.search(sentence)
                and not _NOT_SIGNIFICANT.search(sentence)
                and not any(_names_arm(sentence, r.arm) for r in sig)
            ):
                found.append(
                    Violation(
                        rule_id="clinical.outcome_significance",
                        kind="contradiction",
                        excerpt=sentence[:300],
                        note=(
                            f"[⚠ {OUTCOME_GUARD_MARKER}: in {fact.nct_id} only "
                            f"{', '.join(_dose_label(r.arm) for r in sig)} reached "
                            f"p<{SIGNIFICANCE_ALPHA} ({posted}) — name the significant arm.]"
                        ),
                    )
                )
            if len(_arm_best(fact)) >= 2 and _DOSE_UNREPORTED.search(sentence):
                found.append(
                    Violation(
                        rule_id="clinical.outcome_dose_unreported",
                        kind="contradiction",
                        excerpt=sentence[:300],
                        note=(
                            f"[⚠ {OUTCOME_GUARD_MARKER}: {fact.nct_id} posts per-dose "
                            f"primary-outcome results ({posted}).]"
                        ),
                    )
                )
            m = _DOSE_DEPENDENT.search(sentence)
            if (
                m
                and significance_not_dose_ordered(fact)
                and not _NEGATION_BEFORE.search(sentence[: m.start()])
            ):
                found.append(
                    Violation(
                        rule_id="clinical.outcome_dose_order",
                        kind="contradiction",
                        excerpt=sentence[:300],
                        note=(
                            f"[⚠ {OUTCOME_GUARD_MARKER}: in {fact.nct_id} significance does "
                            f"not rise with dose ({posted}) — no dose-response.]"
                        ),
                    )
                )
    return found


# ---------------------------------------------------------------------------
# Outcome terms the prose asserts but no claim reports
# ---------------------------------------------------------------------------

OUTCOME_TERM_MARKER = "OUTCOME TERM GUARD"

# Named clinical outcomes a small model copies from the skill's tier descriptions (TRPC6:
# "stable disease and biomarker engagement observed in the Phase 2 trial" — neither is in
# the data). Each is (label, pattern); asserting one needs a claim that reports it.
OUTCOME_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("stable disease", r"\bstable\s+disease\b"),
        ("objective response", r"\bobjective\s+response|\bORR\b"),
        ("complete response", r"\bcomplete\s+(?:response|remission)\b"),
        ("partial response", r"\bpartial\s+(?:response|remission)\b"),
        ("progression-free survival", r"\bprogression[- ]free\b|\bPFS\b"),
        ("overall survival", r"\boverall\s+survival\b"),
        ("biomarker engagement", r"\bbiomarker\s+engagement\b"),
        ("target engagement", r"\btarget\s+engagement\b"),
        ("PK/PD", r"\bPK/PD\b|\bpharmacodynamic\w*"),
    )
)


def outcome_terms_in(texts: Iterable[str]) -> frozenset[str]:
    """Labels of the outcome terms some text (a claim the lens saw) reports."""
    joined = "\n".join(texts)
    return frozenset(label for label, pat in OUTCOME_TERMS if pat.search(joined))


def check_outcome_terms(text: str, reported: frozenset[str]) -> list[Violation]:
    """Asserted outcome terms that no claim reports (negated mentions are fine)."""
    body = strip_annotations(text)
    found: list[Violation] = []
    for label, pat in OUTCOME_TERMS:
        if label in reported:
            continue
        hits = [
            (clause, m)
            for clause, m in unnegated_matches(pat, body)
            if not predicate_denied(clause, m.end())
        ]
        if hits:
            found.append(
                Violation(
                    rule_id="clinical.unreported_outcome",
                    kind="contradiction",
                    excerpt=hits[0][0][:300],
                    note=(
                        f"[⚠ {OUTCOME_TERM_MARKER}: no claim shown to this lens reports "
                        f"{label} — describe only the outcomes the data report.]"
                    ),
                )
            )
    return found


# ---------------------------------------------------------------------------
# Trial attribution — a figure stated for a trial must come from that trial
# ---------------------------------------------------------------------------

ATTRIBUTION_MARKER = "TRIAL ATTRIBUTION GUARD"


def link_trial_publications(
    facts: list[TrialFact], documents: Iterable[tuple[str, str]]
) -> list[TrialFact]:
    """Attach to each trial the ids of the documents whose text names its registry id.

    ``documents`` are (evidence id, text) pairs — typically retrieved abstracts, which
    name the trial they report ("ClinicalTrials.gov number, <id>").
    """
    by_registry_id: dict[str, list[str]] = {}
    for doc_id, text in documents:
        for rid in registry_ids(text):
            by_registry_id.setdefault(rid, []).append(doc_id)
    out: list[TrialFact] = []
    for f in facts:
        linked = by_registry_id.get(f.nct_id.upper())
        out.append(
            f.model_copy(update={"publication_ids": tuple(dict.fromkeys(linked))}) if linked else f
        )
    return out


_THOUSANDS_SEP = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_VALUE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")


def _values(text: str) -> list[float]:
    return [float(m.group(0)) for m in _VALUE.finditer(_THOUSANDS_SEP.sub("", text or ""))]


def trial_reported_values(
    facts: list[TrialFact], claims: Iterable[tuple[str, str]]
) -> dict[str, tuple[float, ...]]:
    """Every number each trial's own sources report: its registry fields and posted
    results, claims naming its registry id, and claims extracted from the documents
    linked to it (``publication_ids``). ``claims`` are (source evidence id, claim text)."""
    values: dict[str, set[float]] = {}
    by_document: dict[str, list[str]] = {}
    for f in facts:
        rid = f.nct_id.upper()
        own = values.setdefault(rid, set())
        for text in (f.title, *f.interventions, *(a.arm for a in f.arm_results)):
            own.update(_values(text))
        own.update(a.p_value for a in f.arm_results)
        if f.enrollment:
            own.add(float(f.enrollment))
        for doc_id in f.publication_ids:
            by_document.setdefault(doc_id, []).append(rid)
    for source_id, text in claims:
        nums = _values(text)
        if not nums:
            continue
        for rid in registry_ids(text) | set(by_document.get(source_id, ())):
            if rid in values:
                values[rid].update(nums)
    return {rid: tuple(sorted(v)) for rid, v in values.items()}


# A quantity a trial *result* is stated with: a percentage or a decimal (ratios, p-values,
# effect sizes). Bare integers (counts, doses, phases) are not checked, nor are confidence
# levels and power ("95% CI", "90% power") or design quantities with a dose/time unit.
_EFFECT_NUMBER = re.compile(
    r"(?<![\w.])(?P<num>\d+(?:\.\d+)?)(?![\w])(?P<pct>\s*(?:%|percent\b))?"
    r"(?!\s*(?:%\s*)?(?:CI\b|confidence|power\b|credible|prediction))"
    r"(?!\s*(?:mg|g|µg|mcg|ug|ml|mL|kg|IU|units?|weeks?|months?|years?|days?|hours?|h|min)\b)",
    re.IGNORECASE,
)


# Design parameters stated before their number ("powered at 90%", "a 95% confidence
# interval", "alpha of 0.05") are not results.
_DESIGN_BEFORE = re.compile(
    r"\b(?:power(?:ed)?|confidence(?:\s+level)?|alpha|significance\s+level|"
    r"non-?inferiority\s+margin)\s+(?:of\s+|at\s+|to\s+|was\s+|=\s*)?$",
    re.IGNORECASE,
)


def _derived(value: float, known: tuple[float, ...], percent: bool) -> list[float]:
    """Values a sentence may legitimately derive from a source number: a ratio as a
    percentage or a relative change (HR 0.85 → "15% lower", OR 2.1 → "110% higher"),
    and a percentage as a fraction."""
    out: list[float] = []
    for k in known:
        out.append(k)
        if percent and 0 < k <= 10:
            out += [k * 100, abs(1 - k) * 100]
        elif not percent and 1 < k <= 100:
            out += [k / 100, 1 - k / 100]
    return out


def _is_grounded(number: str, known: tuple[float, ...], percent: bool) -> bool:
    value = float(number)
    decimals = len(number.split(".")[1]) if "." in number else 0
    tolerance = 0.5 * 10**-decimals + 1e-9
    return any(abs(value - c) <= tolerance for c in _derived(value, known, percent))


def check_trial_attribution(
    text: str, trial_values: dict[str, tuple[float, ...]]
) -> list[Violation]:
    """Result figures stated next to a trial's registry id that none of that trial's own
    sources report.

    PCSK9 × MI: an outcome trial was credited with "a 61% reduction" (the drug's LDL
    lowering, from another claim) and "hazard ratio 0.50" (a genetic study's estimate);
    every number appeared *somewhere* in the claims, so source-agnostic grounding passed.
    A trial with no reported numbers at all is not checked (nothing to compare with).
    """
    out: list[Violation] = []
    for sentence in split_sentences(strip_annotations(text)):
        named = sorted(rid for rid in registry_ids(sentence) if trial_values.get(rid))
        if not named:
            continue
        known = tuple(v for rid in named for v in trial_values[rid])
        body = sentence
        for rid in registry_ids(sentence):
            body = re.sub(re.escape(rid), " ", body, flags=re.IGNORECASE)
        bad: list[str] = []
        body = _THOUSANDS_SEP.sub("", body)
        for m in _EFFECT_NUMBER.finditer(body):
            number, percent = m.group("num"), bool(m.group("pct"))
            if not percent and "." not in number:
                continue
            if _DESIGN_BEFORE.search(body[max(0, m.start() - 40) : m.start()]):
                continue
            if not _is_grounded(number, known, percent):
                bad.append(f"{number}%" if percent else number)
        if bad:
            figures = ", ".join(dict.fromkeys(bad))
            out.append(
                Violation(
                    rule_id="grounding.trial_attribution",
                    kind="contradiction",
                    excerpt=sentence,
                    note=(
                        f"[⚠ {ATTRIBUTION_MARKER}: {figures} — not reported by "
                        f"{', '.join(named)}: neither the registry record nor a retrieved "
                        "publication naming it gives this figure; quote that trial's own "
                        "result or attribute the figure to its actual source.]"
                    ),
                )
            )
    return out
