# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic quantitative readings and the generic quantity-wording guard.

Pure module — no I/O, fully unit-testable.

A lens LLM handed a raw number picks its own adjective: 2/512 cell lines became
"dependency across some cancer cell lines". The fix that generalises is the one
`interpret_constraint` already applies to gnomAD: band every number
deterministically, hand the band label to the prompt verbatim, and check the prose
against the band.

    raw values ──▶ make_reading(metric, value) ──▶ QuantReading(label, band)
                                                      │
    prompt  ◀── reading.label (quote verbatim) ◀──────┤
    LLM prose ──▶ check_quantity_wording(text, readings) ──▶ list[Violation]

A metric is one `MetricSpec`: a topic pattern (which clauses talk about it) and
ordered bands, each with the phrasing that overstates or understates it. Adding a
metric is one `register_metric` call — the prompt label and the guard come with it.

Registered metrics:
  * ``cell_line_dependency`` — fraction of CRISPR-screened cancer cell lines in which
    the gene is a fitness/dependency gene (DepMap Chronos, Project Score scaled BF).
  * ``phase{1..4}_trial_count`` — retrieved registry trials per clinical phase; a
    quantifier over a phase list ("multiple Phase 2 and Phase 3 trials") is judged on
    the pooled count by `check_pooled_phase_wording`.

Source merging (`build_dependency_reading`): DepMap first, Project Score as fallback,
so a DepMap outage no longer silently disables the dependency checks.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from services.evidence.guard_core import (
    Violation,
    is_negated,
    iter_clauses,
    predicate_denied,
    strip_annotations,
)

QUANTITY_GUARD_MARKER = "QUANTITY GUARD"

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Band:
    """One interval of a metric: values in [previous band's upper, ``upper``)."""

    key: str
    upper: float
    description: str  # leads the reading label, e.g. "not a cell-line dependency"
    overstatement: re.Pattern[str] | None = None  # phrasing too strong for this band
    understatement: re.Pattern[str] | None = None  # phrasing too weak for this band


@dataclass(frozen=True)
class MetricSpec:
    metric: str
    noun: str  # what the metric measures, for the guard note
    topic: re.Pattern[str]  # a clause must mention the topic for its wording to be checked
    bands: tuple[Band, ...]  # ascending by ``upper``; the last band's upper is +inf

    def band_for(self, value: float) -> Band:
        for band in self.bands:
            if value < band.upper:
                return band
        return self.bands[-1]


class QuantReading(BaseModel):
    """A banded quantitative fact: the label is what the prompt quotes verbatim."""

    model_config = ConfigDict(frozen=True)

    metric: str
    value: float
    band: str
    label: str
    source: str = ""


METRICS: dict[str, MetricSpec] = {}


def register_metric(spec: MetricSpec) -> MetricSpec:
    METRICS[spec.metric] = spec
    return spec


def make_reading(metric: str, value: float, detail: str, source: str = "") -> QuantReading:
    """Band ``value`` under ``metric``; the label is "<band description> — <detail>"."""
    spec = METRICS[metric]
    band = spec.band_for(value)
    label = f"{band.description} — {detail}" if detail else band.description
    return QuantReading(metric=metric, value=value, band=band.key, label=label, source=source)


def check_quantity_wording(text: str, readings: list[QuantReading]) -> list[Violation]:
    """Flag clauses whose quantifiers contradict a reading's band.

    Only clauses that mention the metric's topic are checked. Overstatements must be
    un-negated to count — neither "not a dependency in most lines" nor "dependency
    across cell lines is negligible" is an overstatement; understatement
    patterns carry their own negation and are taken as written.
    """
    body = strip_annotations(text)
    if not body or not readings:
        return []
    clauses = iter_clauses(body)
    found: list[Violation] = []
    for reading in readings:
        spec = METRICS.get(reading.metric)
        if spec is None:
            continue
        band = spec.band_for(reading.value)
        hit = _first_wording_hit(clauses, spec, band)
        if hit is None:
            continue
        clause, phrase, too_strong = hit
        found.append(
            Violation(
                rule_id=f"quantity.{reading.metric}",
                kind="overstatement" if too_strong else "contradiction",
                excerpt=clause,
                note=(
                    f"[⚠ {QUANTITY_GUARD_MARKER}: The text describes {spec.noun} as "
                    f"'{phrase}', but the data show: {reading.label}. Quote the number "
                    f"instead of a vague quantifier.]"
                ),
            )
        )
    return found


def _first_wording_hit(
    clauses: list[str], spec: MetricSpec, band: Band
) -> tuple[str, str, bool] | None:
    for clause in clauses:
        if not spec.topic.search(clause):
            continue
        if band.overstatement is not None:
            for m in band.overstatement.finditer(clause):
                if not is_negated(clause, m.start()) and not predicate_denied(clause, m.end()):
                    return clause, m.group(0), True
        if band.understatement is not None:
            under = band.understatement.search(clause)
            if under:
                return clause, under.group(0), False
    return None


# ---------------------------------------------------------------------------
# Metric: cell-line dependency fraction
# ---------------------------------------------------------------------------

_POPULATION = (
    r"(?:cancer\s+|tumou?r\s+|human\s+)?"
    r"(?:cell[\s-]+lines?|lines|lineages?|models?|cancers?|cancer\s+types?|tumou?r\s+types?|screens?)"
)
_MANY = r"(?:several|multiple|many|most|numerous|various|a\s+(?:number|range|variety)\s+of)"
_MOST = r"(?:most|the\s+majority\s+of|nearly\s+all|almost\s+all|all)"
_BROAD_ESSENTIAL = (
    r"\b(?:common(?:ly)?|selective(?:ly)?|broad(?:ly)?|pan[\s-]cancer|widespread|"
    r"universal(?:ly)?)\s+(?:\w+\s+)?(?:essential\w*|dependen\w+)"
)


def _quantified(quantifiers: str) -> str:
    """'<quantifier> [≤2 words] <population>' and '<dependency> across/in <quantifier> …'."""
    return (
        rf"\b{quantifiers}\s+(?:[\w-]+\s+){{0,2}}?{_POPULATION}\b"
        rf"|\b(?:dependen\w+|essential\w*|fitness\s+effects?)\s+(?:across|in)\s+"
        rf"{quantifiers}\s+(?:[\w-]+\s+){{0,2}}?{_POPULATION}\b"
    )


_DEPENDENCY_TOPIC = re.compile(
    r"\b(?:cell[\s-]+lines?|lineages?|depmap|chronos|crispr|project\s+score|fitness|"
    r"dependen\w+|essential\w*)\b",
    re.IGNORECASE,
)

register_metric(
    MetricSpec(
        metric="cell_line_dependency",
        noun="cell-line dependency",
        topic=_DEPENDENCY_TOPIC,
        bands=(
            Band(
                key="none",
                upper=0.01,
                description="not a cell-line dependency",
                overstatement=re.compile(
                    _quantified(rf"(?:some|{_MANY}|{_MOST})")
                    # "dependency across cancer cell lines" — "across" implies breadth
                    + rf"|\b(?:dependen\w+|essential\w*)\s+across\s+(?:[\w-]+\s+){{0,2}}?{_POPULATION}\b"
                    + rf"|{_BROAD_ESSENTIAL}",
                    re.IGNORECASE,
                ),
            ),
            Band(
                key="rare",
                upper=0.10,
                description="rare cell-line dependency",
                overstatement=re.compile(
                    _quantified(rf"(?:many|most|numerous|{_MOST})") + rf"|{_BROAD_ESSENTIAL}",
                    re.IGNORECASE,
                ),
            ),
            Band(
                key="subset",
                upper=0.50,
                description="dependency in a subset of cell lines",
                overstatement=re.compile(
                    _quantified(_MOST) + r"|\b(?:common(?:ly)?|pan[\s-]cancer|universal(?:ly)?)"
                    r"\s+(?:\w+\s+)?(?:essential\w*|dependen\w+)",
                    re.IGNORECASE,
                ),
            ),
            Band(
                key="broad",
                upper=math.inf,
                description="broad cell-line dependency",
                understatement=re.compile(
                    r"\b(?:not\s+(?:a\s+)?(?:meaningful\s+)?(?:dependen\w+|essential)|"
                    r"non[\s-]?essential|no\s+(?:meaningful\s+|significant\s+)?dependen\w+|"
                    rf"rarely\s+essential|few\s+(?:[\w-]+\s+)?{_POPULATION})",
                    re.IGNORECASE,
                ),
            ),
        ),
    )
)


# ---------------------------------------------------------------------------
# Metric: retrieved trials per clinical phase
# ---------------------------------------------------------------------------

_ROMAN = {1: "i", 2: "ii", 3: "iii", 4: "iv"}
_ROMAN_TO_INT = {r: n for n, r in _ROMAN.items()}
_PLURAL_QUANTIFIERS = r"(?:multiple|several|numerous|many|various|a\s+(?:number|series|range)\s+of)"
_PHASE_TOKEN = r"(?:[1-4]|iv|i{1,3})\b"
# The tail of a coordinated phase list: "Phase 2[ and Phase 3]", "Phase 2[/3]", "Phase 2[, 3]".
_PHASE_LIST_TAIL = rf"\s*(?:,\s*and|,\s*or|,|/|&|and|or)\s*(?:phase\s+)?{_PHASE_TOKEN}"


def _phase_metric(n: int) -> MetricSpec:
    phase = rf"phase\s+(?:{n}|{_ROMAN[n]})\b"
    # A quantifier over a phase list ("multiple Phase 2 and Phase 3 trials") counts the
    # pooled trials, not each phase — `check_pooled_phase_wording` judges it.
    plural = re.compile(
        rf"\b{_PLURAL_QUANTIFIERS}\s+(?:[A-Za-z-]+\s+){{0,2}}{phase}(?!{_PHASE_LIST_TAIL})",
        re.IGNORECASE,
    )
    return MetricSpec(
        metric=f"phase{n}_trial_count",
        noun=f"the number of Phase {n} trials",
        topic=re.compile(rf"\b{phase}", re.IGNORECASE),
        bands=(
            Band(
                key="none",
                upper=1,
                description=f"no Phase {n} trial retrieved",
                overstatement=plural,
            ),
            Band(
                key="single", upper=2, description=f"a single Phase {n} trial", overstatement=plural
            ),
            Band(key="multiple", upper=math.inf, description=f"multiple Phase {n} trials"),
        ),
    )


for _n in range(1, 5):
    register_metric(_phase_metric(_n))


def trial_count_readings(
    phase_counts: dict[int, int], ids_by_phase: dict[int, list[str]]
) -> list[QuantReading]:
    """One reading per clinical phase, labelled with the count and NCT ids."""
    readings: list[QuantReading] = []
    for n in range(1, 5):
        count = phase_counts.get(n, 0)
        ids = ", ".join(ids_by_phase.get(n, [])[:6])
        detail = f"{count} retrieved Phase {n} trial(s)" + (f" ({ids})" if ids else "")
        readings.append(make_reading(f"phase{n}_trial_count", count, detail, "ClinicalTrials.gov"))
    return readings


_POOLED_PHASES = re.compile(
    rf"\b{_PLURAL_QUANTIFIERS}\s+(?:[A-Za-z-]+\s+){{0,2}}"
    rf"(phase\s+{_PHASE_TOKEN}(?:{_PHASE_LIST_TAIL})+)",
    re.IGNORECASE,
)


def check_pooled_phase_wording(text: str, ids_by_phase: dict[int, list[str]]) -> list[Violation]:
    """Flag a plural quantifier over a phase list that covers fewer than two trials.

    "multiple Phase 2 and Phase 3 trials" is true for one Phase 2 plus one Phase 3
    trial; it overstates only when the listed phases hold a single distinct trial
    (e.g. one Phase 2/3 trial counted under both phases) or none.
    """
    body = strip_annotations(text)
    if not body:
        return []
    for clause in iter_clauses(body):
        for m in _POOLED_PHASES.finditer(clause):
            if is_negated(clause, m.start()) or predicate_denied(clause, m.end()):
                continue
            phases = sorted(
                {
                    int(t) if t.isdigit() else _ROMAN_TO_INT[t.lower()]
                    for t in re.findall(rf"\b{_PHASE_TOKEN}", m.group(1), re.IGNORECASE)
                }
            )
            ids = sorted({i for n in phases for i in ids_by_phase.get(n, [])})
            if len(ids) >= 2:
                continue
            listed = "/".join(str(n) for n in phases)
            found_ids = f" ({', '.join(ids)})" if ids else ""
            return [
                Violation(
                    rule_id="quantity.pooled_trial_count",
                    kind="overstatement",
                    excerpt=clause,
                    note=(
                        f"[⚠ {QUANTITY_GUARD_MARKER}: The text describes the number of Phase "
                        f"{listed} trials as '{m.group(0)}', but the data show: {len(ids)} "
                        f"retrieved trial(s) across Phase {listed}{found_ids}. Quote the "
                        f"number instead of a vague quantifier.]"
                    ),
                )
            ]
    return []


# ---------------------------------------------------------------------------
# Dependency reading: DepMap first, Project Score as fallback
# ---------------------------------------------------------------------------

# DepMap's Chronos dependency threshold — a line is dependent at score ≤ −0.5.
_CHRONOS_DEPENDENT = -0.5
# Below this fraction a non-oncology target's cancer-cell-line data carry no signal.
_UNINFORMATIVE_FRACTION = 0.10


class DependencyReading(BaseModel):
    """Cell-line dependency for one gene, from whichever CRISPR source returned data."""

    model_config = ConfigDict(frozen=True)

    source: str  # "DepMap" | "Project Score"
    n_dependent: int
    n_total: int
    fraction: float
    mean_score: float | None = None
    mean_score_name: str = ""  # "Chronos" | "scaled BF"
    is_common_essential: bool = False
    reading: QuantReading

    @property
    def label(self) -> str:
        return self.reading.label

    def detail(self) -> str:
        mean = (
            f", mean {self.mean_score_name}={self.mean_score:.3f}"
            if self.mean_score is not None
            else ""
        )
        return f"{self.source}: {self.n_dependent}/{self.n_total} lines ({self.fraction:.1%}){mean}"


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def build_dependency_reading(
    depmap: dict[str, Any] | None, project_score: dict[str, Any] | None
) -> DependencyReading | None:
    """Merge the DepMap and Project Score bundles into one reading (DepMap preferred).

    Returns None only when neither source returned line counts — callers must treat
    that as "dependency data unavailable", never as "no dependency".
    """
    dm = depmap or {}
    n_dep, n_tot = _int(dm.get("num_dependent_lines")), _int(dm.get("total_lines"))
    if n_dep is not None and n_tot:
        source, mean, mean_name = "DepMap", dm.get("gene_effect_mean"), "Chronos"
        common = bool(dm.get("is_common_essential"))
    else:
        ps = project_score or {}
        n_dep, n_tot = _int(ps.get("num_fitness_lines")), _int(ps.get("total_lines"))
        if n_dep is None or not n_tot:
            return None
        source, mean, mean_name = "Project Score", ps.get("bf_scaled_mean"), "scaled BF"
        common = bool(ps.get("is_pancan_core_fitness"))
    fraction = n_dep / n_tot
    detail = f"fitness effect in {n_dep}/{n_tot} {source} cancer cell lines ({fraction:.1%})"
    if common:
        detail += "; pan-cancer common essential"
    return DependencyReading(
        source=source,
        n_dependent=n_dep,
        n_total=n_tot,
        fraction=fraction,
        mean_score=mean if isinstance(mean, (int, float)) else None,
        mean_score_name=mean_name,
        is_common_essential=common,
        reading=make_reading("cell_line_dependency", fraction, detail, source),
    )


def dependency_band_prompt(reading: DependencyReading) -> str:
    """Prompt line handing the lens the pre-computed band instead of a raw fraction."""
    return (
        "Cell-line dependency band (pre-computed — quote this wording and its numbers "
        "verbatim; do NOT replace them with 'some', 'several', 'multiple' or 'across cell "
        f"lines'): {reading.label}"
    )


def dependency_is_uninformative(reading: DependencyReading | None, *, is_oncology: bool) -> bool:
    """True when cancer-cell-line dependency carries no signal for THIS target.

    Source-agnostic generalisation of `depmap_is_uninformative`: non-oncology, not
    common-essential, under 10% dependent lines and (for DepMap) a mean Chronos above
    the −0.5 dependency threshold. False when the reading is missing — absent data is
    "not evaluated", not "uninformative".
    """
    if reading is None or is_oncology or reading.is_common_essential:
        return False
    if (
        reading.source == "DepMap"
        and reading.mean_score is not None
        and reading.mean_score <= _CHRONOS_DEPENDENT
    ):
        return False
    return reading.fraction < _UNINFORMATIVE_FRACTION
