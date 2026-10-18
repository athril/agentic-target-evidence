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


# ------------------------------------------------------------- lens signal under the gates


def test_the_ungated_score_is_the_evidence_less_the_liabilities():
    from services.evaluation.panel import ungated

    bands = [
        (75, "PROCEED"),
        (60, "PROCEED WITH CAUTION"),
        (45, "INVESTIGATE FURTHER"),
        (0, "DEPRIORITISE"),
    ]
    assert ungated({"evidence_score": 64.1, "liability_penalty": 4.8}, bands) == (
        59,
        "INVESTIGATE FURTHER",
    )
    assert ungated({}, bands) == (None, "")


def test_a_rule_that_changed_the_verdict_is_counted():
    from services.evaluation.panel import set_by_rule

    assert set_by_rule([{"rule_id": "x", "message": "…; verdict oppose → neutral."}])
    assert not set_by_rule([{"rule_id": "x", "message": "…; verdict oppose → oppose."}])
    assert set_by_rule([{"rule_id": "neutral_axis_conflict", "message": "axes"}])
    assert not set_by_rule([{"rule_id": "citation_repair", "message": "attached"}])


def test_guard_flags_and_repairs_are_counted_across_lenses():
    from services.evaluation.panel import flag_counts

    verdicts = [
        {"validation_flags": [{"rule_id": "trial_readout_guard"}, {"rule_id": "prose_repaired"}]},
        {"validation_flags": [{"rule_id": "fact_check.boxed_warning_denied"}]},
        {"validation_flags": [{"rule_id": "citation_repair"}]},
    ]
    assert flag_counts(verdicts) == (2, 1)


def test_the_report_shows_the_ungated_band_and_the_rule_set_lenses():
    exp = Expectation(
        gene="G1", disease="d", direction="inhibit", expected="GO", bands=["PROCEED"], lenses={}
    )
    obs = Observed(
        gene="G1",
        disease="d",
        direction="inhibit",
        band="PROCEED",
        score=75,
        ungated_score=59,
        ungated_band="INVESTIGATE FURTHER",
        set_by_rule=["commercial"],
    )
    text = render_markdown(score_panel([exp], [obs]), "t")
    assert "Ungated bands: 0/1" in text
    assert "❌ INVESTIGATE FURTHER (59)" in text and "| commercial |" in text
