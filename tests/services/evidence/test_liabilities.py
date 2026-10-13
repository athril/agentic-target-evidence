# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Candidate on-target liabilities and the shared organ-system vocabulary.

A safety lens could leave a liability out entirely (lung and kidney knockout phenotypes
omitted while the verdict stayed "support 93%"); the liabilities are now derived from
the gene's own data, so the prompt, the report and the score all see every one.
"""

from __future__ import annotations

from services.evidence.liabilities import candidate_liabilities, render_liabilities_block
from services.evidence.organ_systems import group_by_system, prose_names_system


def test_adrenal_is_endocrine_not_kidney():
    grouped = group_by_system(["abnormal adrenal gland secretion"])
    assert "kidney" not in grouped and "endocrine" in grouped


def test_memory_t_cells_are_immune_not_nervous():
    grouped = group_by_system(["abnormal memory T cell physiology"])
    assert "nervous system" not in grouped and "immune / blood cells" in grouped


def test_grouping_keeps_every_system_a_label_names():
    grouped = group_by_system(
        [
            "abnormal type II pneumocyte morphology",
            "accumulation of giant lysosomes in renal tubule cells",
            "increased circulating cholesterol level",
            "preweaning lethality, complete penetrance",
        ]
    )
    assert set(grouped) == {
        "lung",
        "kidney",
        "metabolism (glucose / lipids)",
        "lethality / development",
    }


def test_prose_check_ignores_unrelated_uses_of_development():
    assert not prose_names_system("the drug development programme", "lethality / development")
    assert prose_names_system("knockouts are not viable", "lethality / development")


def test_liabilities_from_every_source_most_severe_first():
    items = candidate_liabilities(
        safety_events=["hepatotoxicity"],
        mouse_phenotypes=["abnormal type II pneumocyte morphology", "kidney inflammation"],
        constraint={"claims_haploinsufficiency_ok": True, "loeuf": 0.2},
        direction="inhibit",
        common_essential=True,
        boxed_warnings=[("DRUG-A", "Risk of serious infections.")],
    )
    systems = [li.system for li in items]
    assert {"liver", "lung", "kidney", "dosage sensitivity", "labelled boxed warning"} <= set(
        systems
    )
    assert "cell fitness (common essential)" in systems
    severities = [li.severity for li in items]
    assert severities == sorted(severities, key=["high", "medium", "low"].index)


def test_constraint_is_a_liability_only_against_inhibition():
    c = {"claims_haploinsufficiency_ok": True, "loeuf": 0.2}
    assert candidate_liabilities(constraint=c, direction="inhibit")
    assert not candidate_liabilities(constraint=c, direction="activate")


def test_unplaced_safety_event_is_still_listed():
    items = candidate_liabilities(safety_events=["teratogenicity signal"])
    assert items and items[0].evidence == ["teratogenicity signal"]


def test_block_states_absence_is_not_safety():
    assert "not evidence of safety" in render_liabilities_block([])
    block = render_liabilities_block(candidate_liabilities(mouse_phenotypes=["lung fibrosis"]))
    assert "name every item" in block and "lung" in block


def test_incomplete_penetrance_lethality_is_medium():
    partial = candidate_liabilities(
        mouse_phenotypes=["preweaning lethality, incomplete penetrance"]
    )
    full = candidate_liabilities(mouse_phenotypes=["embryonic lethality prior to organogenesis"])
    assert partial[0].severity == "medium" and full[0].severity == "high"


def test_organ_inflammation_is_not_also_an_immune_liability():
    assert set(group_by_system(["kidney inflammation"])) == {"kidney"}


def test_naming_the_disease_does_not_count_as_naming_the_organ():
    from services.evidence.grounding_guards import check_phenotype_omission

    text = "Inhibition is well tolerated in patients with myocardial infarction. " * 12
    assert check_phenotype_omission(text, ["enlarged heart"], "myocardial infarction")
    assert not check_phenotype_omission(
        text + " The heart phenotype is a liability.", ["enlarged heart"], "myocardial infarction"
    )


def test_cell_type_expression_needs_a_claim_naming_the_cell_type():
    from services.evidence.grounding_guards import cell_types_reported, check_cell_type_claims

    cts = ["dopaminergic neuron"]
    text = "The protein is expressed in the disease-relevant dopaminergic neurons (TPM 3.3)."
    assert check_cell_type_claims(text, cts, cell_types_reported(cts, ["Kinase activity rises."]))
    supported = cell_types_reported(cts, ["GENE-A protein is detected in dopaminergic neurons."])
    assert not check_cell_type_claims(text, cts, supported)
    # Naming the cell type without an expression statement is fine.
    assert not check_cell_type_claims("Dopaminergic neurons degenerate.", cts, frozenset())
