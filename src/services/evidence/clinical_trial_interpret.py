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

from services.evidence.guard_core import Violation, annotate, strip_annotations

# ---------------------------------------------------------------------------
# Fact model + extraction
# ---------------------------------------------------------------------------


class TrialFact(BaseModel):
    """Authoritative per-trial registry facts the guard checks LLM prose against."""

    nct_id: str
    phase: str = ""  # raw registry value, e.g. "PHASE2"
    status: str = ""  # raw registry value, e.g. "COMPLETED"
    phase_numbers: tuple[int, ...] = ()  # normalised, e.g. (2,); () when unphased/NA


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
            )
        )
    return facts


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
            window = body[lo:hi]

            if f.phase_numbers:
                stated = {
                    p
                    for tok in _PHASE_IN_TEXT.findall(window)
                    if (p := _phase_token_to_int(tok)) is not None
                }
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
    """Number of retrieved trials per phase number (a multi-phase trial counts in each)."""
    counts: dict[int, int] = dict.fromkeys(range(1, 5), 0)
    for f in facts:
        for n in f.phase_numbers:
            counts[n] = counts.get(n, 0) + 1
    return counts
