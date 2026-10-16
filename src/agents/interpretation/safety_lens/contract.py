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
        "missing_inputs",  # required structured inputs absent from the run (lens_inputs)
        "direction_reading_text",  # direction-of-effect prompt block
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
    },
    produces={"lens_verdicts"},
    max_loops=1,
    skills=["safety_lens"],
)
