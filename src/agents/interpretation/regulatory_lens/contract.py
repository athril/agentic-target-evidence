# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="regulatory_lens",
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
        "fda_label_text",
        "drug_precedent",  # therapeutic-hypothesis precedent (services/evidence/drug_precedent)
    },
    produces={"lens_verdicts"},
    max_loops=1,
    skills=["regulatory_lens"],
)
