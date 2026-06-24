# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="commercial_lens",
    consumes={
        "disease_classes",
        "target_gene",
        "disease",
        "direction",
        "gene_id",
        "disease_id",
        "extracted_claims",
        "source_quality",
        "patent_count",
        "trial_count",
        "ot_known_drugs_approved_count",
        "ot_known_drugs_count",
        "ot_known_drugs_phase3_count",
        "ot_known_drugs_text",
        "fda_label_text",
        "gbd_prevalence_text",
        "orphanet_prevalence_text",
        "indication_competition_text",
        "indication_approved_drug_count",
        "indication_active_trial_count",
        "indication_phase3_trial_count",
        "indication_total_trial_count",
    },
    produces={"lens_verdicts"},
    max_loops=1,
    skills=["commercial_lens"],
)
