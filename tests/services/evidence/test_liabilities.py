# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Candidate on-target liabilities and the shared organ-system vocabulary.

A safety lens could leave a liability out entirely (lung and kidney knockout phenotypes
omitted while the verdict stayed "support 93%"); the liabilities are now derived from
the gene's own data, so the prompt, the report and the score all see every one.
"""

from __future__ import annotations

from services.evidence.liabilities import (
    CURATED_EVENTS,
    candidate_liabilities,
    literature_harm_system,
    render_liabilities_block,
    split_by_direction,
    target_safety,
)
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
    assert {"lung", "kidney", "dosage sensitivity", "labelled boxed warning"} <= set(systems)
    assert "cell fitness (common essential)" in systems
    assert any(li.systems == ["liver"] for li in items)
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


def test_death_under_an_experimental_challenge_is_medium():
    """A knockout that dies only when challenged is not lethal on its own."""
    [challenge] = candidate_liabilities(
        mouse_phenotypes=["increased susceptibility to xenobiotic induced morbidity/mortality"]
    )
    assert challenge.system.startswith("lethality") and challenge.severity == "medium"
    mixed = candidate_liabilities(
        mouse_phenotypes=[
            "increased susceptibility to bacterial infection induced morbidity/mortality",
            "complete embryonic lethality",
        ]
    )
    assert next(li for li in mixed if li.system.startswith("lethality")).severity == "high"


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


def test_common_essentiality_is_a_liability_for_lowering_not_raising_the_target():
    assert candidate_liabilities(common_essential=True, direction="inhibit")
    assert candidate_liabilities(common_essential=True, direction="")
    assert not candidate_liabilities(common_essential=True, direction="activate")


def test_only_stop_reasons_naming_harm_become_a_liability():
    """Elenbecestat stopped for an adverse-event profile worse than placebo; lanabecestat
    for futility — only the first is a safety finding."""
    [li] = candidate_liabilities(
        trial_stop_reasons=[
            "NCT1 (phase 3, terminated): the adverse event profile being worse than placebo",
            "NCT2 (phase 3, terminated): stopped for futility at interim analysis",
            "NCT3 (phase 2, terminated): elevations in liver enzymes",
        ]
    )
    assert li.system == "trials stopped for safety" and li.severity == "high"
    assert len(li.evidence) == 2 and "futility" not in " ".join(li.evidence)


def test_evidence_that_the_requested_change_worsens_the_disease_is_a_liability():
    [li] = candidate_liabilities(
        direction_harms=[("drug-a", 'drug-a label warns: "Cases of the disease were reported"')]
    )
    assert li.system == "the requested change worsens the disease"
    assert li.source == "drug-a"


# ---------------------------------------------------------------------------
# The change of the target that causes each liability
# ---------------------------------------------------------------------------


def _row(event: str, *directions: str) -> dict:
    return {
        "event": event,
        "datasource": "curated",
        "effects": [{"direction": d, "dosing": ""} for d in directions],
    }


def test_open_targets_events_take_their_curated_direction():
    ot = {
        "safety_liabilities": [
            # Open Targets joins the words; read one string at a time, every event was
            # "either"
            _row("hepatotoxicity", "Inhibition/Decrease/Downregulation"),
            _row("hypertension", "Activation/Increase/Upregulation"),
            _row("arrhythmia", "Inhibition", "Activation"),
            _row("rash"),
            _row("nausea", "unknown"),
            _row("edema", "Downregulation"),
            _row("edema", "Upregulation"),  # rows of one event that disagree
        ]
    }
    safety = target_safety(ot)
    assert safety["event_directions"] == {
        "hepatotoxicity": "decrease",
        "hypertension": "increase",
        "arrhythmia": "either",
        "edema": "either",
    }
    # A row naming no change of the target would count against both: listed instead.
    assert [li["event"] for li in safety["listed"]] == ["rash", "nausea"]
    assert safety["events"] == ["hepatotoxicity", "hypertension", "arrhythmia", "edema"]
    legacy = target_safety({"safety_liability_events": ["hepatotoxicity"]})
    assert legacy["event_directions"] == {"hepatotoxicity": "either"}


def test_assay_and_pathway_rows_are_listed_not_counted():
    ot = {
        "safety_liabilities": [
            {"event": "regulation of catalytic activity", "datasource": "ToxCast", "effects": []},
            {
                "event": "Decrease, Population growth rate",
                "datasource": "AOP-Wiki",
                "effects": [{"direction": "Inhibition/Decrease/Downregulation"}],
            },
            _row("prolongation of QT interval of ECG", "Inhibition/Decrease/Downregulation"),
        ]
    }
    safety = target_safety(ot)

    assert safety["events"] == ["prolongation of QT interval of ECG"]
    assert {li["datasource"] for li in safety["listed"]} == {"ToxCast", "AOP-Wiki"}
    assert "2 record(s) that are not harms in people" in safety["text"]
    # the QT row is a harm of lowering the target, not of raising it
    items = candidate_liabilities(
        safety_events=safety["events"],
        event_directions=safety["event_directions"],
        direction="activate",
    )
    counted, opposite, _ = split_by_direction(items, "activate")
    assert counted == []
    assert [(li.systems, li.perturbation) for li in opposite] == [(["heart"], "decrease")]


def test_curated_events_are_one_finding_per_change():
    items = candidate_liabilities(
        safety_events=["hepatotoxicity", "liver injury", "QT prolongation", "hypertension"],
        event_directions={
            "hepatotoxicity": "decrease",
            "liver injury": "increase",
            "QT prolongation": "decrease",
            "hypertension": "decrease",
        },
    )
    by_change = {li.perturbation: li for li in items}

    assert len(items) == 2
    lowered = by_change["decrease"]
    assert lowered.systems == ["heart", "blood vessels / blood pressure", "liver"]
    assert lowered.system == f"{CURATED_EVENTS} (heart, blood vessels / blood pressure, liver)"
    assert lowered.severity == "high"
    assert by_change["increase"].systems == ["liver"]


def test_each_source_is_tagged_with_the_change_that_causes_it():
    items = candidate_liabilities(
        safety_events=["hepatotoxicity", "hypertension"],
        event_directions={"hepatotoxicity": "decrease", "hypertension": "increase"},
        mouse_phenotypes=["abnormal type II pneumocyte morphology"],
        constraint={"is_lof_constrained": True, "loeuf": 0.3, "pli": 0.9},
        direction="inhibit",
        common_essential=True,
        boxed_warnings=[("DRUG-A", "Risk of serious infections.")],
        trial_stop_reasons=["stopped for safety"],
        direction_harms=[("label", "worsens disease")],
    )
    tags = {(li.systems[0] if li.systems else li.system): li.perturbation for li in items}
    assert tags["liver"] == "decrease"
    assert tags["blood vessels / blood pressure"] == "increase"
    assert tags["lung"] == "decrease"
    assert tags["dosage sensitivity"] == "decrease"
    assert tags["cell fitness (common essential)"] == "decrease"
    # drugs of the requested direction, and harm from the requested change itself
    assert tags["labelled boxed warning"] == "decrease"
    assert tags["trials stopped for safety"] == "decrease"
    assert tags["the requested change worsens the disease"] == "decrease"


def test_one_system_splits_by_direction():
    items = candidate_liabilities(
        safety_events=["hepatotoxicity", "liver injury"],
        event_directions={"hepatotoxicity": "decrease", "liver injury": "increase"},
    )
    assert sorted((li.systems, li.perturbation) for li in items) == [
        (["liver"], "decrease"),
        (["liver"], "increase"),
    ]


def test_literature_harms_become_one_medium_item_per_direction():
    items = candidate_liabilities(
        literature_harms=[
            ("increase", "PMID:1", "Overexpression of GENE-A caused cardiomyopathy."),
            ("increase", "PMID:2", "GENE-A gain of function caused arrhythmia."),
            ("decrease", "PMID:3", "GENE-A knockdown impaired wound healing."),
            ("none", "PMID:4", "ignored"),
        ]
    )
    by_change = {li.perturbation: li for li in items}
    assert set(by_change) == {"increase", "decrease"}
    raised = by_change["increase"]
    assert raised.system == literature_harm_system("increase")
    assert raised.severity == "medium"
    assert raised.source == "PMID:1, PMID:2"
    assert len(raised.evidence) == 2


_TAGS = {"hepatotoxicity": "decrease", "hypertension": "increase"}


def _common_inputs(direction: str, tags: dict[str, str] | None = _TAGS) -> dict:
    return {
        "safety_events": ["hepatotoxicity", "hypertension", "rash"],
        "event_directions": tags,
        "mouse_phenotypes": ["abnormal type II pneumocyte morphology"],
        "constraint": {"is_lof_constrained": True, "loeuf": 0.3, "pli": 0.9},
        "common_essential": True,
        "direction": direction,
    }


def test_a_lowering_run_leaves_out_only_increase_events():
    items = candidate_liabilities(**_common_inputs("inhibit"))
    counted, opposite, assessed = split_by_direction(items, "inhibit")

    assert [(li.systems, li.perturbation) for li in opposite] == [
        (["blood vessels / blood pressure"], "increase")
    ]
    curated = [li for li in counted if li.source == "Open Targets safety"]
    assert sorted((li.perturbation, li.evidence) for li in curated) == [
        ("decrease", ["hepatotoxicity"]),
        ("either", ["rash"]),
    ]
    assert {"lung", "dosage sensitivity", "cell fitness (common essential)"} <= {
        li.system for li in counted
    }
    assert assessed


def test_a_raising_run_counts_no_knockout_constraint_or_essentiality_item():
    items = candidate_liabilities(**_common_inputs("activate"))
    counted, opposite, assessed = split_by_direction(items, "activate")

    assert all(li.perturbation in ("increase", "either") for li in counted)
    assert not {"lung", "dosage sensitivity", "cell fitness (common essential)"} & {
        li.system for li in counted
    }
    assert "lung" in {li.system for li in opposite}
    assert any(li.systems == ["liver"] for li in opposite)
    assert assessed  # the increase-tagged event covers the requested change


def test_a_raising_run_with_only_lowering_data_is_not_assessed():
    items = candidate_liabilities(
        mouse_phenotypes=["abnormal type II pneumocyte morphology"],
        safety_events=["rash"],
        direction="activate",
    )
    counted, opposite, assessed = split_by_direction(items, "activate")

    assert [(li.system, li.perturbation) for li in counted] == [(CURATED_EVENTS, "either")]
    assert opposite and not assessed


def test_a_run_without_a_requested_change_counts_everything():
    items = candidate_liabilities(**_common_inputs("unspecified"))
    counted, opposite, assessed = split_by_direction(items, "unspecified")
    assert counted == items and opposite == [] and assessed
