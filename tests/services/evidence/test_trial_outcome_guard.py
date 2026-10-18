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


def test_a_hard_endpoint_secondary_is_not_read_as_the_primary_endpoint():
    from services.evidence.clinical_trial_interpret import _arm_results

    secondary = {
        "title": "Overall survival",
        "kind": "secondary",
        "analyses": [{"groups": ["Drug", "Placebo"], "p_value": "0.001"}],
    }
    primary = {
        "title": "Response",
        "analyses": [{"groups": ["Drug", "Placebo"], "p_value": "0.20"}],
    }

    arms = _arm_results([primary, secondary])

    assert [a.measure for a in arms] == ["Response"]


def test_a_non_significant_or_null_covering_hard_endpoint_is_not_met():
    from services.evidence.clinical_trial_interpret import hard_endpoints

    def secondary(**analysis):
        return {"title": "Overall survival", "kind": "secondary", "analyses": [analysis]}

    by_p = hard_endpoints(
        [secondary(param_type="Hazard Ratio (HR)", param_value="0.97", p_value="0.76")]
    )
    by_ci = hard_endpoints(
        [secondary(param_type="Hazard Ratio", param_value="0.9", ci_lower="0.80", ci_upper="1.05")]
    )
    by_difference = hard_endpoints(
        [
            secondary(
                param_type="Mean Difference", param_value="1.2", ci_lower="-0.3", ci_upper="2.7"
            )
        ]
    )
    significant = hard_endpoints([secondary(param_type="Hazard Ratio", p_value="<0.001")])
    unanalysed = hard_endpoints([{"title": "Mortality", "kind": "secondary", "analyses": []}])

    assert [h.not_met for h in (*by_p, *by_ci, *by_difference)] == [True, True, True]
    # The registry gives no sign: a significant result is neither a win nor a loss here.
    assert significant[0].not_met is None and unanalysed[0].not_met is None
    assert hard_endpoints([{"title": "Overall survival", "analyses": []}]) == ()  # a primary


def test_deaths_per_arm_give_a_risk_ratio_with_its_interval():
    from services.evidence.clinical_trial_interpret import death_imbalance

    imbalance = death_imbalance(
        [
            {"group": "Drug + standard care", "deaths": 138, "deaths_at_risk": 439},
            {"group": "Placebo + standard care", "deaths": 40, "deaths_at_risk": 222},
        ]
    )

    assert imbalance is not None
    assert imbalance.risk_ratio == pytest.approx(1.745, abs=0.002)
    assert imbalance.ci_lower == pytest.approx(1.276, abs=0.005)
    assert imbalance.ci_upper == pytest.approx(2.386, abs=0.005)
    assert imbalance.excess


def test_no_comparator_arm_or_no_counts_gives_no_death_imbalance():
    from services.evidence.clinical_trial_interpret import death_imbalance

    assert death_imbalance([{"group": "Drug", "deaths": 3, "deaths_at_risk": 30}]) is None
    assert death_imbalance([{"group": "Drug"}, {"group": "Placebo"}]) is None
    zero = death_imbalance(
        [
            {"group": "Drug", "deaths": 0, "deaths_at_risk": 50},
            {"group": "Placebo", "deaths": 0, "deaths_at_risk": 50},
        ]
    )
    assert zero is not None and not zero.excess  # 0.5 added to empty cells


def test_a_trial_registered_under_an_inverted_heading_studies_the_indication():
    """An outcome trial registered as "Leukemia, Myeloid, Acute" was read as studying
    another disease, so its failure never reached the programme classification."""
    from services.evidence.clinical_trial_interpret import TrialFact, studies_indication

    def fact(*conditions: str) -> TrialFact:
        return TrialFact(nct_id="NCT00000001", conditions=conditions)

    assert studies_indication(fact("Leukemia, Myeloid, Acute"), "acute myeloid leukemia")
    assert studies_indication(fact("Arthritis, Rheumatoid"), "rheumatoid arthritis")
    assert not studies_indication(fact("Leukemia, Lymphocytic, Chronic"), "acute myeloid leukemia")


def test_only_short_comma_headings_are_uninverted():
    from core.evidence_text import uninvert_heading

    assert uninvert_heading("Carcinoma, Non-Small-Cell Lung") == "Non-Small-Cell Lung Carcinoma"
    assert uninvert_heading("Disease A") == "Disease A"
    long_text = "Patients with disease a, treated before, who relapsed after two lines of therapy"
    assert uninvert_heading(long_text) == long_text
