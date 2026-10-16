# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Panel scoring against expert expectations (services/evaluation/panel.py)."""

from __future__ import annotations

from services.evaluation.panel import (
    Expectation,
    Observed,
    load_expectations,
    render_markdown,
    score_panel,
)

_E = Expectation(
    gene="G",
    disease="Disease One",
    direction="inhibit",
    expected="NO-GO",
    bands=["DEPRIORITISE"],
    lenses={"clinical": ["oppose"], "safety": ["oppose", "neutral"]},
)


def test_runs_are_matched_by_gene_disease_and_direction():
    observed = Observed(
        gene="g",
        disease="disease one",
        direction="Inhibit",
        band="DEPRIORITISE",
        score=30,
        lenses={"clinical": "oppose", "safety": "support"},
    )
    [s] = score_panel([_E], [observed])
    assert s.band_ok
    assert s.lens_hits == {"clinical": True, "safety": False}
    assert "safety: support (want oppose/neutral)" in render_markdown([s], "t")


def test_a_missing_run_scores_nothing_and_is_reported():
    [s] = score_panel([_E], [])
    assert not s.band_ok and s.lens_accuracy == 0
    assert "1 expected run(s) not found" in render_markdown([s], "t")


def test_the_fixture_is_well_formed():
    panel = load_expectations()
    assert len(panel) == 10
    lenses = {"genetics", "biology", "clinical", "safety", "commercial", "regulatory"}
    verdicts = {"support", "oppose", "neutral", "insufficient_evidence"}
    bands = {"PROCEED", "PROCEED WITH CAUTION", "INVESTIGATE FURTHER", "DEPRIORITISE"}
    for e in panel:
        assert set(e.lenses) == lenses, e.gene
        assert all(set(v) <= verdicts for v in e.lenses.values()), e.gene
        assert set(e.bands) <= bands, e.gene
    assert all(set(e.bands) <= bands for e in load_expectations("held_out"))
