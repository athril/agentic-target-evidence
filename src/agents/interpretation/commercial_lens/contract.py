# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="commercial_lens",
    consumes={
        "ot_known_drugs",
        "disease_classes",
        "target_gene",
        "disease",
        "direction",
        "gene_id",
        "disease_id",
        "extracted_claims",
        "source_quality",
        "decisive_claim_ids",  # claims always shown when routed (claim_relevance)
        "patent_count",
        "indication_patent_count",
        "source_groups",
        "target_programmes",  # drug programmes with competitive status (target_programmes)
        "precedent_status",  # drug_precedent status: validated means the market is proven
        "programme_reading",  # same-direction programmes in the indication (programme_outcomes)
        "direction_contradicted",  # human evidence contradicts the requested direction
        "indication_landscape_known",  # the indication's approved-drug count was retrieved
        "trial_count",
        "ot_known_drugs_approved_count",
        "ot_known_drugs_count",
        "ot_known_drugs_phase3_count",
        "ot_known_drugs_text",
        "fda_label_text",
        "gbd_prevalence_text",
        "orphanet_prevalence_text",
        "indication_prevalence_found",
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
