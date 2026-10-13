# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Trial-outcome readings and guard.

TRPC6 × FSGS: NCT05213624 posted per-dose primary-outcome results (20 mg p=0.0024,
40 mg p=0.7906, 80 mg p=0.1325), yet the clinical lens wrote "a significant reduction
in proteinuria" and "dose-dependent effects were not reported".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.evidence.clinical_trial_interpret import (
    build_trial_facts,
    check_trial_outcome_guard,
    outcome_split,
    significance_not_dose_ordered,
    trial_outcome_prompt,
)


def _analysis(arm: str, p: str) -> dict:
    return {"groups": [arm, "Placebo"], "param_type": "Relative difference", "p_value": p}


_RESULTS = [
    {
        "title": "Relative Change From Baseline at Week 12 of 24-hour UPCR",
        "analyses": [
            _analysis("BI 764198 20 mg", "0.0024"),
            _analysis("BI 764198 40 mg", "0.7906"),
            _analysis("BI 764198 80 mg", "0.1325"),
        ],
    },
    {
        # Odds ratios without p-values carry no significance reading.
        "title": "Achievement of at Least 25% Reduction in UPCR",
        "analyses": [{"groups": ["BI 764198 20 mg", "Placebo"], "param_value": "10.00"}],
    },
]


def _facts(results=_RESULTS):
    ev = SimpleNamespace(
        source="NCT05213624",
        extra={
            "phase": "PHASE2",
            "status": "COMPLETED",
            "interventions": ["BI 764198", "Placebo"],
            "results": results,
        },
    )
    return build_trial_facts([ev])


def test_posted_comparisons_become_arm_results():
    (fact,) = _facts()
    assert [(r.arm, r.p_value) for r in fact.arm_results] == [
        ("BI 764198 20 mg", 0.0024),
        ("BI 764198 40 mg", 0.7906),
        ("BI 764198 80 mg", 0.1325),
    ]
    sig, nonsig = outcome_split(fact)
    assert [r.arm for r in sig] == ["BI 764198 20 mg"]
    assert len(nonsig) == 2
    assert significance_not_dose_ordered(fact)


def test_prompt_names_the_significant_arm_and_the_missing_dose_response():
    text = trial_outcome_prompt(_facts())
    assert "BI 764198 20 mg p=0.0024 (significant)" in text
    assert "Only 20 mg reached p<0.05; 40 mg, 80 mg did not." in text
    assert "no dose-response" in text


def test_no_prompt_or_guard_without_posted_comparisons():
    facts = _facts(results=[])
    assert trial_outcome_prompt(facts) == ""
    assert check_trial_outcome_guard("BI 764198 showed a significant effect.", facts) == []


@pytest.mark.parametrize(
    "sentence,rule",
    [
        (
            "The Phase 2 trial of BI 764198, a TRPC6 inhibitor, showed a significant reduction "
            "in proteinuria at Week 12 (NCT05213624).",
            "clinical.outcome_significance",
        ),
        (
            "The Phase 2 trial showed a significant reduction in proteinuria, but detailed "
            "biomarker engagement or dose-dependent effects were not reported (NCT05213624).",
            "clinical.outcome_dose_unreported",
        ),
        (
            "BI 764198 produced a dose-dependent reduction in proteinuria.",
            "clinical.outcome_dose_order",
        ),
    ],
)
def test_misstatements_are_flagged(sentence, rule):
    assert rule in {v.rule_id for v in check_trial_outcome_guard(sentence, _facts())}


@pytest.mark.parametrize(
    "sentence",
    [
        "In NCT05213624 only the 20 mg arm of BI 764198 reached significance (p=0.0024).",
        "BI 764198 40 mg was not significant versus placebo (NCT05213624).",
        "BI 764198 showed no dose-dependent reduction in proteinuria.",
        "A significant unmet need remains in FSGS.",  # names no trial
    ],
)
def test_accurate_or_unrelated_sentences_pass(sentence):
    assert check_trial_outcome_guard(sentence, _facts()) == []
