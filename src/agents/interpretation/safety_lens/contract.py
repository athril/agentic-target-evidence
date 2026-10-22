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
        "decisive_claim_ids",  # claims always shown when routed (claim_relevance)
        "literature_index",  # paper records for literature ranking (literature_selection)
        "missing_inputs",  # required structured inputs absent from the run (lens_inputs)
        "dependency_profile",  # dependency pattern vs the indication (readings)
        "human_exposure",  # a drug acting on the target has been given to people
        "liability_assessed",  # data on the requested change were examined
        "direction_reading_text",  # direction-of-effect prompt block
        "genetic_direction",  # human-genetics strengths for the direction guard
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
        "secreted",  # UniProt: the protein is secreted (services/evidence/site_of_action)
        "drug_precedent",  # therapeutic-hypothesis precedent (services/evidence/drug_precedent)
        "approved_label_warnings",  # warnings of same-direction drugs approved here
        "safety_stops_elsewhere",  # same-direction drugs stopped for safety elsewhere (listed)
        "liability_mouse_phenotypes",  # labels of the counted mouse finding (omission guard)
    },
    produces={"lens_verdicts"},
    max_loops=1,
    skills=["safety_narrative"],
)
