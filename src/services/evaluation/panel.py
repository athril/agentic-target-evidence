# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Score benchmark-panel runs against expert expectations (tests/fixtures/panel_expectations.yaml).

A change that fixes one target can quietly break another; scoring every panel re-run
against the same expectations — the recommendation band and each lens's verdict — makes
that visible. Pure module: the script (scripts/score_panel.py) loads the runs.

The band alone overstates the lenses: every approved-in-the-indication target sits on the
validated precedent's floor, so the lenses' own signal is also reported as the ungated
score (evidence score less the liability penalty, before any gate), with the lens verdicts
a deterministic rule set rather than the lens, and how many guard flags and repairs fired.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

FIXTURE = Path("tests/fixtures/panel_expectations.yaml")


class Expectation(BaseModel):
    gene: str
    disease: str
    direction: str
    expected: str
    bands: list[str]
    lenses: dict[str, list[str]] = {}

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.gene.upper(), self.disease.lower(), self.direction.lower())


class Observed(BaseModel):
    gene: str
    disease: str
    direction: str
    band: str = ""
    score: int | None = None
    lenses: dict[str, str] = {}  # lens → overall verdict
    ungated_score: int | None = None  # evidence score less liability penalty, no gates
    ungated_band: str = ""
    # Lenses whose verdict a deterministic rule changed: rules that compute the verdict by
    # design, and rules that rescue a model verdict the evidence contradicts.
    computed_by_design: list[str] = []
    rescued: list[str] = []
    guard_flags: int = 0  # guard and fact-check flags across the lenses
    repairs: int = 0  # sentences the repair step rewrote
    literature_claims: int = 0  # literature claims of the run
    literature_scored: int = 0  # … of which have a source-quality score
    shown_inputs: dict[str, dict[str, int]] = {}  # lens → shown claims per evidence kind
    axis_conflicts: dict[str, int] = {}  # lens → axes whose rationale contradicts the value
    # Components the run went without (core/degradations.py): failed lookups, fallbacks;
    # None for a run from before the ledger existed.
    degradations: list[str] | None = None


class RunScore(BaseModel):
    expectation: Expectation
    observed: Observed | None
    band_ok: bool
    lens_hits: dict[str, bool]  # lens → verdict within the expected set
    ungated_band_ok: bool = False

    @property
    def lens_accuracy(self) -> float:
        return sum(self.lens_hits.values()) / len(self.lens_hits) if self.lens_hits else 0.0


# Validation flags that changed a lens's overall verdict ("<verdict> → <verdict>" in the
# message, or a rule that always does), and the flags a guard or fact check raised (rule
# ids as the lens code writes them).
_VERDICT = r"(support|oppose|neutral|insufficient_evidence)"
_VERDICT_CHANGE = re.compile(rf"\b{_VERDICT} → {_VERDICT}\b")
_VERDICT_RULES = frozenset({"neutral_axis_conflict"})
_GUARD_FLAG = re.compile(r"_guard$|^fact_check\.|^numeric_grounding$")
# Rules that compute a lens verdict by design (from its axes or from the programmes), as
# opposed to rescuing a model verdict the evidence contradicts.
_DESIGNED_RULE = re.compile(
    r"^(?:commercial\.competitive_computed|clinical\.computed_axes\.|genetics\.causality_by_rule"
    r"|genetics\.verdict_from_axes|biology\.verdict_from_axes)"
)

RuleKind = Literal["design", "rescue"]


def verdict_rules(flags: list[dict[str, Any]]) -> set[RuleKind]:
    """Which kinds of rule changed a lens's verdict, judged from its validation flags."""
    kinds: set[RuleKind] = set()
    for f in flags:
        rule_id = str(f.get("rule_id") or "")
        m = _VERDICT_CHANGE.search(str(f.get("message") or ""))
        if (m and m.group(1) != m.group(2)) or rule_id in _VERDICT_RULES:
            kinds.add("design" if _DESIGNED_RULE.match(rule_id) else "rescue")
    return kinds


def flag_counts(lens_verdicts: list[dict[str, Any]]) -> tuple[int, int]:
    """(guard and fact-check flags, repaired sentences) across a run's lenses."""
    flags = [f for lv in lens_verdicts for f in lv.get("validation_flags") or []]
    guards = sum(1 for f in flags if _GUARD_FLAG.search(str(f.get("rule_id") or "")))
    repairs = sum(1 for f in flags if f.get("rule_id") == "prose_repaired")
    return guards, repairs


def axis_conflict_counts(lens_verdicts: list[dict[str, Any]]) -> dict[str, int]:
    """lens → number of `axis_rationale_conflict` flags (an axis whose rationale affirms
    what its value denies, or the reverse)."""
    out: dict[str, int] = {}
    for lv in lens_verdicts:
        n = sum(
            1
            for f in lv.get("validation_flags") or []
            if f.get("rule_id") == "axis_rationale_conflict"
        )
        if n:
            out[str(lv.get("lens"))] = n
    return out


def ungated(breakdown: dict[str, Any], bands: list[tuple[int, str]]) -> tuple[int | None, str]:
    """Score and band before the gates: evidence score less the liability penalty."""
    evidence = breakdown.get("evidence_score")
    if evidence is None:
        return None, ""
    score = round(
        max(0.0, min(100.0, float(evidence) - float(breakdown.get("liability_penalty") or 0)))
    )
    band = next((label for lo, label in bands if score >= lo), bands[-1][1] if bands else "")
    return score, band


def load_expectations(section: str = "panel", path: Path = FIXTURE) -> list[Expectation]:
    data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    return [Expectation.model_validate(e) for e in data.get(section) or []]


def score_run(expectation: Expectation, observed: Observed | None) -> RunScore:
    if observed is None:
        return RunScore(
            expectation=expectation,
            observed=None,
            band_ok=False,
            lens_hits={lens: False for lens in expectation.lenses},
        )
    return RunScore(
        expectation=expectation,
        observed=observed,
        band_ok=observed.band in expectation.bands,
        ungated_band_ok=observed.ungated_band in expectation.bands,
        lens_hits={
            lens: observed.lenses.get(lens, "missing") in allowed
            for lens, allowed in expectation.lenses.items()
        },
    )


def score_panel(expectations: list[Expectation], observed: list[Observed]) -> list[RunScore]:
    by_key = {(o.gene.upper(), o.disease.lower(), o.direction.lower()): o for o in observed}
    return [score_run(e, by_key.get(e.key)) for e in expectations]


def _conflict_line(ran: list[RunScore]) -> str:
    totals: dict[str, int] = {}
    for s in ran:
        for lens, n in (s.observed.axis_conflicts if s.observed else {}).items():
            totals[lens] = totals.get(lens, 0) + n
    detail = ", ".join(f"{lens} {n}" for lens, n in sorted(totals.items()))
    return f"Axes whose rationale contradicts their value: {sum(totals.values())}" + (
        f" ({detail})." if detail else "."
    )


def degradation_line(ran: list[RunScore]) -> str:
    """How many runs went on without something, and what. A systematic failure (the same
    component in every run) shows here instead of in a grep of the logs."""
    recorded = [s for s in ran if s.observed and s.observed.degradations is not None]
    if not recorded:
        return "Degraded runs: not recorded (the runs predate the degradation ledger)."
    counts: dict[str, int] = {}
    degraded = 0
    for s in recorded:
        components = list(dict.fromkeys(s.observed.degradations or [] if s.observed else []))
        degraded += bool(components)
        for component in components:
            counts[component] = counts.get(component, 0) + 1
    if not degraded:
        return f"Degraded runs: 0/{len(recorded)}."
    listed = ", ".join(
        f"{c} ({n}/{len(recorded)})" for c, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    return f"Degraded runs: {degraded}/{len(recorded)} — {listed}."


def render_markdown(scores: list[RunScore], title: str) -> str:
    ran = [s for s in scores if s.observed is not None]
    bands = sum(s.band_ok for s in ran)
    hits = sum(sum(s.lens_hits.values()) for s in ran)
    total = sum(len(s.lens_hits) for s in ran)
    ungated_ok = sum(s.ungated_band_ok for s in ran)
    by_design = sum(len(s.observed.computed_by_design) for s in ran if s.observed)
    rescued = sum(len(s.observed.rescued) for s in ran if s.observed)
    guards = sum(s.observed.guard_flags for s in ran if s.observed)
    repairs = sum(s.observed.repairs for s in ran if s.observed)
    unscored = [
        s
        for s in ran
        if s.observed and s.observed.literature_claims and not s.observed.literature_scored
    ]
    lines = [
        f"# {title}",
        "",
        f"**Bands: {bands}/{len(ran)}** · **Ungated bands: {ungated_ok}/{len(ran)}** · "
        f"**Lens verdicts: {hits}/{total}"
        + (f" ({hits / total:.0%})" if total else "")
        + "**"
        + (
            f" · {len(scores) - len(ran)} expected run(s) not found"
            if len(ran) < len(scores)
            else ""
        ),
        "",
        f"Lens verdicts computed by design: {by_design} · rescued by a rule: {rescued} · "
        f"guard flags: {guards} · repaired sentences: {repairs}. The ungated band is the "
        "lenses' own signal, before precedent and direction gates.",
        "",
        _conflict_line(ran),
        "",
        f"Runs whose literature had no quality score: {len(unscored)}/{len(ran)}"
        + (
            " ("
            + ", ".join(f"{s.expectation.gene} · {s.expectation.disease}" for s in unscored)
            + ")"
            if unscored
            else ""
        )
        + ".",
        "",
        degradation_line(ran),
        "",
        "| Run | Expected | Band (score) | Ungated | Lenses within expectation | Misses "
        "| Computed by design | Rescued |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        e = s.expectation
        name = f"{e.gene} · {e.disease} · {e.direction}"
        if s.observed is None:
            lines.append(f"| {name} | {e.expected} | not run | — | — | — | — | — |")
            continue
        o = s.observed
        mark = "✅" if s.band_ok else "❌"
        misses = "; ".join(
            f"{lens}: {o.lenses.get(lens, 'missing')} (want {'/'.join(e.lenses[lens])})"
            for lens, ok in s.lens_hits.items()
            if not ok
        )
        ungated_mark = "✅" if s.ungated_band_ok else "❌"
        ungated_cell = (
            f"{ungated_mark} {o.ungated_band} ({o.ungated_score})"
            if o.ungated_score is not None
            else "—"
        )
        lines.append(
            f"| {name} | {e.expected} ({'/'.join(e.bands)}) | {mark} {o.band or '—'} "
            f"({o.score if o.score is not None else '—'}) | {ungated_cell} | "
            f"{sum(s.lens_hits.values())}/{len(s.lens_hits)} | {misses or '—'} | "
            f"{', '.join(o.computed_by_design) or '—'} | {', '.join(o.rescued) or '—'} |"
        )
    inputs = [s for s in ran if s.observed and s.observed.shown_inputs]
    if inputs:
        lens_names = sorted(
            {lens for s in inputs if s.observed for lens in s.observed.shown_inputs}
        )
        lines += [
            "",
            "Claims each lens was shown, per evidence kind:",
            "",
            "| Run | " + " | ".join(lens_names) + " |",
            "|---|" + "---|" * len(lens_names),
        ]
        for s in inputs:
            seen = s.observed
            if seen is None:
                continue
            cells = [
                ", ".join(f"{k} {n}" for k, n in sorted(seen.shown_inputs.get(lens, {}).items()))
                or "—"
                for lens in lens_names
            ]
            e = s.expectation
            lines.append(f"| {e.gene} · {e.disease} · {e.direction} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
