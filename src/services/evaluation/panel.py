# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Score benchmark-panel runs against expert expectations (tests/fixtures/panel_expectations.yaml).

A change that fixes one target can quietly break another; scoring every panel re-run
against the same expectations — the recommendation band and each lens's verdict — makes
that visible. Pure module: the script (scripts/score_panel.py) loads the runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


class RunScore(BaseModel):
    expectation: Expectation
    observed: Observed | None
    band_ok: bool
    lens_hits: dict[str, bool]  # lens → verdict within the expected set

    @property
    def lens_accuracy(self) -> float:
        return sum(self.lens_hits.values()) / len(self.lens_hits) if self.lens_hits else 0.0


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
        lens_hits={
            lens: observed.lenses.get(lens, "missing") in allowed
            for lens, allowed in expectation.lenses.items()
        },
    )


def score_panel(expectations: list[Expectation], observed: list[Observed]) -> list[RunScore]:
    by_key = {(o.gene.upper(), o.disease.lower(), o.direction.lower()): o for o in observed}
    return [score_run(e, by_key.get(e.key)) for e in expectations]


def render_markdown(scores: list[RunScore], title: str) -> str:
    ran = [s for s in scores if s.observed is not None]
    bands = sum(s.band_ok for s in ran)
    hits = sum(sum(s.lens_hits.values()) for s in ran)
    total = sum(len(s.lens_hits) for s in ran)
    lines = [
        f"# {title}",
        "",
        f"**Bands: {bands}/{len(ran)}** · **Lens verdicts: {hits}/{total}"
        + (f" ({hits / total:.0%})" if total else "")
        + "**"
        + (
            f" · {len(scores) - len(ran)} expected run(s) not found"
            if len(ran) < len(scores)
            else ""
        ),
        "",
        "| Run | Expected | Band (score) | Lenses within expectation | Misses |",
        "|---|---|---|---|---|",
    ]
    for s in scores:
        e = s.expectation
        name = f"{e.gene} · {e.disease} · {e.direction}"
        if s.observed is None:
            lines.append(f"| {name} | {e.expected} | not run | — | — |")
            continue
        o = s.observed
        mark = "✅" if s.band_ok else "❌"
        misses = "; ".join(
            f"{lens}: {o.lenses.get(lens, 'missing')} (want {'/'.join(e.lenses[lens])})"
            for lens, ok in s.lens_hits.items()
            if not ok
        )
        lines.append(
            f"| {name} | {e.expected} ({'/'.join(e.bands)}) | {mark} {o.band or '—'} "
            f"({o.score if o.score is not None else '—'}) | "
            f"{sum(s.lens_hits.values())}/{len(s.lens_hits)} | {misses or '—'} |"
        )
    return "\n".join(lines) + "\n"
