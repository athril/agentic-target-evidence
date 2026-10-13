# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from harness.contract import AgentContract

CONTRACT = AgentContract(
    name="investigator",
    consumes={
        "ot_known_drugs",
        "target_gene",
        "disease",
        "direction",
        "gene_id",
        "disease_id",
        "review_gaps",
        "agreement_map",
        "lens_summary",
        "trial_facts",
        "extracted_claims",
        "gwas_traits_in_scope",  # GWAS Catalog traits matched to the disease (run fact)
    },
    produces={"investigation_summary", "tools_used", "error"},
    max_loops=1,
    skills=["investigator"],
)
