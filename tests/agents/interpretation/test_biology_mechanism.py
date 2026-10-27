# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Biology mechanism rules, direction-aware druggability and the support condition."""

from __future__ import annotations

import uuid

from agents.interpretation.biology_lens.agent import settle_biology_verdict
from agents.interpretation.biology_lens.mechanism import (
    apply_mechanism_rules,
    knockout_contradiction,
)
from agents.interpretation.biology_lens.tractability import druggability_axis
from schemas.verdicts import AxisVerdict, LensVerdict

# HSD17B13 biology, 2026-10-26 panel review.
_HSD_MOA = (
    "Multiple genetic perturbation studies demonstrate that reducing HSD17B13 activity "
    "improves hepatic steatosis and fibrosis in mouse models, while overexpression "
    "exacerbates disease."
)


def _verdict(moa: bool | None = True, rationale: str = _HSD_MOA) -> LensVerdict:
    return LensVerdict(
        run_id=uuid.uuid4(),
        trace_id="t",
        lens="biology",
        target_gene="GENE-A",
        disease="disease one",
        overall_verdict="support",
        confidence=0.9,
        axes=[
            AxisVerdict(axis="druggability", verdict=True, confidence=0.85, rationale="d"),
            AxisVerdict(
                axis="mechanism_of_action", verdict=moa, confidence=0.9, rationale=rationale
            ),
            AxisVerdict(axis="developability", verdict=True, confidence=0.9, rationale="dev"),
        ],
    )


def test_a_knockout_phenotype_the_rationale_says_lowering_improves_is_a_contradiction():
    assert knockout_contradiction(_HSD_MOA, ["liver inflammation", "hepatic steatosis"]) == (
        "hepatic steatosis"
    )
    # Lowering worsening the phenotype agrees with the knockout.
    worse = "Knockout of the gene worsens hepatic steatosis in mice."
    assert knockout_contradiction(worse, ["hepatic steatosis"]) == ""
    assert knockout_contradiction(_HSD_MOA, ["small liver"]) == ""


def test_the_mechanism_is_uncertain_when_its_premise_contradicts_the_knockout():
    out = apply_mechanism_rules(
        _verdict(),
        direction="degrade",
        programme_reading=None,
        knockout_phenotypes=["hepatic steatosis"],
    )
    axes = {a.axis: a for a in out.axes}
    assert axes["mechanism_of_action"].verdict is None
    assert axes["developability"].corroborative
    assert settle_biology_verdict(out).overall_verdict == "neutral"


def test_several_failed_programmes_leave_the_mechanism_uncertain():
    """BACE1: mechanism favourable at 95% beside five failed phase 2/3 programmes."""
    several = {"available": True, "gate": {"min_strength": 0.6, "max": 44, "reason": "r"}}
    one = {"available": True, "gate": {"min_strength": 0.3, "max": 59, "reason": "r"}}
    moa = "Reducing the target lowers amyloid in mice."
    out = apply_mechanism_rules(
        _verdict(rationale=moa),
        direction="inhibit",
        programme_reading=several,
        knockout_phenotypes=[],
    )
    assert {a.axis: a.verdict for a in out.axes}["mechanism_of_action"] is None
    kept = apply_mechanism_rules(
        _verdict(rationale=moa), direction="inhibit", programme_reading=one, knockout_phenotypes=[]
    )
    assert {a.axis: a.verdict for a in kept.axes}["mechanism_of_action"] is True


def test_support_needs_a_favourable_mechanism():
    assert settle_biology_verdict(_verdict(moa=True)).overall_verdict == "support"
    assert settle_biology_verdict(_verdict(moa=None)).overall_verdict == "neutral"


def test_druggability_counts_only_drugs_with_the_requested_effect():
    """PCSK9 · activate: "druggable, 94%" from its inhibitors."""
    inhibitors = [{"drug_name": "DRUG-A", "action_type": "INHIBITOR", "max_phase": 4.0}]
    tract = {"available": True, "small_molecule": True}
    activate = druggability_axis(inhibitors, tract, "activate")
    assert activate is not None and activate.verdict is None
    assert "opposite direction" in activate.rationale
    inhibit = druggability_axis(inhibitors, tract, "inhibit")
    assert inhibit is not None and inhibit.verdict is True
