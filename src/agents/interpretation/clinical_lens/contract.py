# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="clinical_lens",
    consumes={
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
        "trial_facts",  # authoritative per-trial phase/status for the guards
        "drug_precedent",  # therapeutic-hypothesis precedent (services/evidence/drug_precedent)
        "programme_reading",  # programme outcomes (services/evidence/programme_outcomes)
        "ot_known_drugs",  # name and approval of the target's drugs (drug-approval guard)
    },
    produces={"lens_verdicts"},
    max_loops=1,
    skills=["clinical_lens"],
)
