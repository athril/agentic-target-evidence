# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="safety_lens",
    consumes={
        "gtex_tissue_ranks",
        "ot_mouse_phenotype_labels",
        "disease_classes",
        "target_gene",
        "disease",
        "direction",
        "gene_id",
        "disease_id",
        "extracted_claims",
        "source_quality",
        "literature_index",  # paper records for literature ranking (literature_selection)
        "missing_inputs",  # required structured inputs absent from the run (lens_inputs)
        "dependency_profile",  # dependency pattern vs the indication (readings)
        "human_exposure",  # a drug acting on the target has been given to people
        "direction_reading_text",  # direction-of-effect prompt block
        "direction_reading",  # its verdict and strength, for the split lens's toxicity rule
        "ot_mouse_text",
        "ot_safety_liability_count",
        "ot_safety_liability_events",
        "ot_safety_text",
        "safety_structured_text",
        "faers_text",
        "bulk_tpm",
        "hpa_specificity",
        "disease_tissue",
        "disease_tissue_expression_note",
        "top_tpm_tissues",
        "disease_relevant_tissues",
        "constraint_reading",
        "dependency_reading",  # DepMap merged with the Project Score fallback
        "mechanism_direction",
        "candidate_liabilities",
        "disease_cell_types",
        "drug_precedent",  # therapeutic-hypothesis precedent (services/evidence/drug_precedent)
        "approved_label_warnings",  # warnings of same-direction drugs approved here (split lens)
        "harm_trials",  # posted deaths / serious events per arm in trials of the target's drugs
    },
    produces={"lens_verdicts"},
    max_loops=1,
    skills=["safety_lens"],
)
